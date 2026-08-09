"""trace_write — 唯一写入路径。

CLI、网页表单、agent API 全部调这里，不允许任何一方绕过去直接写文件。
上一代系统的 bug 根源就是存在第二条写入路径（直接 sqlite3 INSERT），
导致父子关系只写进了一半的地方。这里用"只有一个函数会创建 note.md"来杜绝。

只追加原则（P2）在这里强制：
  * id 由服务端分配，正常情况下不重编号；
  * parent 一旦写下就不可改（update_step 直接抛 Conflict）。

唯一的例外是 delete_step()：真删目录，用来处理"这条记录本身就不该存在"
（误建、测试数据、粘进去的令牌）。它和 status=dead 是两件事——dead 是研究结论。
代价（id 可能被重用、子步骤变孤儿）在那个函数的说明里写清楚了。

双语（note.<lang>.md）同样只有一条写入路径：write_translation /
write_project_translation / drop_translation。它们碰不到原文，也写不出结构键——
见文件下半部分「翻译」那一节的长注释。
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

try:                      # POSIX
    import fcntl
except ImportError:       # pragma: no cover - Windows
    fcntl = None          # type: ignore[assignment]
try:                      # Windows
    import msvcrt
except ImportError:       # pragma: no cover - POSIX
    msvcrt = None         # type: ignore[assignment]

from trace_core import (
    NOTE_NAME,
    PROJECT_NOTE,
    STATUSES,
    DEFAULT_STATUS,
    Project,
    Step,
    build_children,
    id_key,
    fmt_id,
    parse_note,
    path_kind,
    project_dir,
    projects_root,
    scan,
    scan_projects,
    split_id,
    steps_dir_of,
    validate,
)

# 双语用到的那几张表，权威定义在 trace_core —— 读侧要按同一张表解析翻译文件，
# 两边各存一份就是双真相源的开端。
#
# 兜底分支只为一种情形存在：trace_core 还没长出这几个名字时本模块仍要能被导入
# （否则整个包连测试都起不来）。core 一旦有了，import 成功，下面这份永远走不到，
# 也就不可能和 core 分道扬镳——绝不能改成「两边都定义，谁先谁赢」。
try:
    from trace_core import (
        DELETED_NAME,
        INSIGHT_NAMES,
        PROJECT_TR_ONLY_KEYS,
        PROJECT_TR_RE,
        TR_ONLY_KEYS,
        TR_RE,
    )
except ImportError:      # pragma: no cover - 仅在 core 尚未加入双语常量时
    INSIGHT_NAMES = {
        "idea":    {"zh": "核心想法", "en": "Ideas"},
        "works":   {"zh": "有效",     "en": "Works"},
        "fails":   {"zh": "无效",     "en": "Doesn't work"},
        "pitfall": {"zh": "坑",       "en": "Pitfalls"},
    }
    DELETED_NAME = {"zh": "已删除", "en": "Deleted"}
    TR_RE = re.compile(r"^note\.([A-Za-z][A-Za-z0-9-]{0,34})\.md$")
    PROJECT_TR_RE = re.compile(r"^project\.([A-Za-z][A-Za-z0-9-]{0,34})\.md$")
    TR_ONLY_KEYS = ("title",)
    PROJECT_TR_ONLY_KEYS = ("name",)

MAX_SLUG = 40
MAX_FILE_BYTES = 32 * 1024 * 1024

BODY_TEMPLATE = """## 为什么

## 做了什么

## 结果

## 结论

## 下一步
"""


class WriteError(Exception):
    """400 类错误：输入不合法。"""


class Conflict(WriteError):
    """409：违反只追加原则。"""


class NotFound(WriteError):
    """404。"""


# ---------------------------------------------------------------- 原子写


def _utf8_bytes(text: str, what: str) -> bytes:
    """把文本编成 UTF-8，编不了就在**动磁盘之前**报错。

    为什么单拎出来：落单代理字符（被截断的 emoji、从 JS 侧 slice 出来的半个代理对）
    在 JSON 里完全合法，一路传到写盘那一刻才炸。如果那时才发现，
    目标文件已经被截断了——留在磁盘上的是空文件而不是旧内容。
    """
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        bad = text[exc.start:exc.start + 1]
        raise WriteError(
            f"{what}里有存不成 UTF-8 的字符（第 {exc.start} 个字符 {bad!r}）。"
            "这类字符通常是被截断的 emoji 或半个 UTF-16 代理对——"
            "服务器没有故障，磁盘上的原文一个字节都没动过，"
            "把这个字符删掉再存一次即可。"
        ) from None


def write_atomic(path: Path, data: str | bytes) -> None:
    """同目录临时文件 → flush + fsync → os.replace。所有 note.md / project.md 的写入都走它。

    为什么不用 write_text：它以 mode='w' 打开，也就是**先把目标截断、再往里写**。
    编码失败、磁盘满、进程被 kill、断电，任何一种都会让那一步的记录变成 0 字节的空文件。
    这一条直接打在 G4 上——删掉全部程序之后还能 grep 出「我当年为什么放弃了 X」，
    靠的就是那份正文；失败之后留下的必须是**旧内容**，宁可这次没写成。

    临时文件必须和目标同目录：os.replace 只在同一个文件系统内是原子的
    （跨盘在 Windows 上直接报错）。文件名以 "." 开头，这样即使写到一半被中断，
    残留物也不会被 scan/list_files/signature 当成步骤或附件（它们都跳过点开头的名字）。
    """
    blob = _utf8_bytes(data, "要写入的内容") if isinstance(data, str) else bytes(data)
    d = path.parent
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())          # 落到盘上，不只是落到页缓存
        os.replace(tmp, path)             # Windows 上覆盖已存在的目标也是允许的
    except BaseException:
        # 失败路径必须自己收尾：残留的临时文件会被 git add -A 扫进去。
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_text_strict(path: Path, what: str) -> str:
    """按 UTF-8 严格读回来；读不干净就拒绝后续的写。

    读侧（core.scan）用的是 errors="replace"，非 UTF-8 的 note.md 会被静默读成一串
    U+FFFD。此时**任何一次修改**都会把这串问号当成原文写回去，原始字节不可逆地没了
    ——cmd.exe 的 `echo … > note.md`（GBK）和 PowerShell 5.1 的 `>`（UTF-16LE）
    都会产出这种文件，而 FORMAT.md 正是鼓励人手写 note.md 的。
    所以写入侧在动手之前先确认自己读到的确实是原文。
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WriteError(f"读不出 {what}：{exc}") from None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WriteError(
            f"{what}（{path.name}）不是 UTF-8 编码（第 {exc.start} 个字节 "
            f"0x{raw[exc.start]:02x} 解不开）。直接改会把原文替换成一串问号，"
            "所以这次写入被拒绝了。请先手工把这个文件转码成 UTF-8（无 BOM 也可以），再来改。"
        ) from None


def digest_of(path: Path) -> str:
    """摘要指纹：文件原始字节的 sha256 前 12 位；文件不存在时是空串。

    乐观并发控制（update_step 的 expect）靠它。公式必须和 core 在 Step.to_dict()
    里暴露的那个逐字一致，否则客户端拿到的指纹永远对不上，冲突检测会变成误报。
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


# ---------------------------------------------------------------- 跨进程锁

# 一把「一个项目」粒度的文件锁。它**不是**中心索引：里面一个字节都没有，
# 删掉它不丢任何信息，重启之后自己重建——所以 P1（纯文件即数据库、不许有中心索引）
# 不禁止它。它只解决一件事：alloc_id 是「扫一遍取 max+1」，两个进程各自扫完再各自
# mkdir，就会分到同一个号；而「网页服务 + 本机 MCP 同时读写同一份文件」正是推荐配置。
#
# 锁文件放在项目目录下的 .trace-lock，理由：
#   * 必须和数据在同一个位置——systemd 单元开了 PrivateTmp，服务的 /tmp 和别的进程
#     不是同一个，放临时目录的锁在真实部署里根本锁不住；
#   * 点开头 ⇒ scan / scan_projects / list_files / signature 全都跳过它，
#     既不会被扫成步骤，也不会让目录指纹变化而触发多余的 SSE 推送；
#   * 仓库 .gitignore 里加了 `.trace-lock`（数据仓如果是另一个 git 仓库，
#     需要在那边也加同一行）。
LOCK_NAME = ".trace-lock"
LOCK_TIMEOUT = 10.0

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_LOCAL = threading.local()


def _lock_key(anchor: Path) -> str:
    # Windows 上路径大小写不敏感，两个进程可能用不同大小写指同一个目录。
    return os.path.normcase(str(anchor.resolve()))


def _acquire(fd: int) -> bool:
    """拿到锁返回 True；超时返回 False（此时退化成今天的无锁行为，而不是让写入彻底失败）。

    两个平台用的都是内核持有的锁：进程被 kill 时由内核自动释放，
    所以不会像「自己造 O_EXCL 锁文件」那样留下永久死锁，也就不需要陈旧锁超时清理。
    """
    deadline = time.monotonic() + LOCK_TIMEOUT
    while True:
        try:
            if msvcrt is not None:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            elif fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:                      # pragma: no cover - 两个模块都没有的平台
                return False
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)


def _release(fd: int) -> None:
    with contextlib.suppress(OSError):
        if msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def project_lock(anchor: Path):
    """锁住一个项目的写入。anchor 是项目目录（步骤操作传 steps_dir.parent）。

    同一个线程重入直接放行（append_body → update_step 就是这种嵌套），
    否则非重入锁会把自己锁死。线程锁在文件锁外面再兜一层：一个进程内的
    多个线程本来也会撞号，而文件锁在某些平台上对同进程的第二个句柄并不总是互斥。
    """
    key = _lock_key(anchor)
    held = getattr(_LOCAL, "held", None)
    if held is None:
        held = _LOCAL.held = set()
    if key in held:
        yield
        return

    with _THREAD_LOCKS_GUARD:
        tl = _THREAD_LOCKS.setdefault(key, threading.Lock())
    with tl:
        held.add(key)
        fd = None
        try:
            fd = os.open(str(anchor / LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o666)
        except OSError:
            # 拿不到锁文件（只读挂载、权限不足）不该让整个系统写不进去，
            # 退化成无锁——这正是加锁之前的行为，不会更糟。
            fd = None
        try:
            ok = _acquire(fd) if fd is not None else False
            try:
                yield
            finally:
                if ok:
                    _release(fd)
        finally:
            held.discard(key)
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)


def today() -> str:
    """服务端本地日期。front-matter 是「机器记录」，日期该自动来，不该指望调用方填。"""
    return datetime.datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 辅助


def slugify(title: str) -> str:
    """目录名后半段。保留中文（Python 的 \\w 是 unicode-aware 的），只是给人和 shell 补全用。"""
    s = unicodedata.normalize("NFKC", title or "").strip().lower()
    s = re.sub(r"[^\w]+", "-", s, flags=re.UNICODE).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if len(s) > MAX_SLUG:
        s = s[:MAX_SLUG].rstrip("-")
    return s or "step"


# ---------------------------------------------------------------- 项目


def resolve_project(root: Path, slug: str) -> Path:
    """把 URL 里的项目名解析成 steps 目录。顺带挡掉路径穿越。"""
    slug = (slug or "").strip().strip("/")
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        raise WriteError(f"非法项目名: {slug!r}")
    d = steps_dir_of(root, slug)
    if not d.parent.is_dir():
        raise NotFound(f"项目 {slug} 不存在")
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_project(root: Path, name: str) -> Project:
    name = _clean_line(name)
    if not name:
        raise WriteError("项目名不能为空")
    projects_root(root).mkdir(parents=True, exist_ok=True)
    stem = slugify(name)
    existing = {p.slug for p in scan_projects(root)}
    slug, n = stem, 2
    while slug in existing:
        slug, n = f"{stem}-{n}", n + 1

    d = project_dir(root, slug)
    if d.exists():
        raise Conflict(f"项目目录已存在: {slug}")
    try:
        # 这里刻意不加锁：锁文件是按「一个项目」建的，建项目本身还没有项目可锁，
        # 而放在 projects/ 下会让那个目录多出一个非项目条目。两个进程同时建同名
        # 项目时靠 mkdir 自己的原子性收口——只是要把裸 FileExistsError 换成 Conflict，
        # 否则它不是 WriteError，服务端接不住会漏成 500。
        (d / "steps").mkdir(parents=True)
    except FileExistsError:
        raise Conflict(f"项目目录已存在: {slug}") from None
    write_atomic(d / PROJECT_NOTE, f"---\nname: {name}\n---\n\n")
    return Project(slug=slug, name=name)


# 项目级的沉淀。它不属于任何单独一步——「回译在这个数据集上一直没用」是三次
# 尝试之后的判断，挂在哪一步都不对。所以放在 project.md 的正文里。
#
# 这里只是把 core 那张封闭词表投影成「kind → 中文小节名」，因为 web/app.js、
# MCP 的内联标准和 FORMAT.md 的对照表都钉着 INSIGHT_SECTIONS 这个名字。
# 投影而不是再抄一份：抄一份就会有一天两边对不上，而对不上的表现是
# trace_insight 往一个新建的空小节里追加，旧洞察还留在原来那个小节里。
INSIGHT_SECTIONS = {k: v["zh"] for k, v in INSIGHT_NAMES.items()}
INSIGHT_TEMPLATE = "\n\n".join(f"## {t}" for t in INSIGHT_SECTIONS.values()) + "\n"
INSIGHT_HEADINGS = frozenset(INSIGHT_SECTIONS.values())

_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")


def _split_sections(body: str) -> list[tuple[str | None, list[str]]]:
    """把正文切成 [(小节名, 该小节的所有行)]；第一个标题之前的部分小节名是 None。"""
    out: list[tuple[str | None, list[str]]] = []
    head: str | None = None
    buf: list[str] = []
    for line in (body or "").split("\n"):
        m = _HEADING_RE.match(line)
        if m:
            if head is not None or any(x.strip() for x in buf):
                out.append((head, buf))
            head, buf = m.group(1), [line]
        else:
            buf.append(line)
    if head is not None or any(x.strip() for x in buf):
        out.append((head, buf))
    return out


def insight_headings(lang: str = "zh") -> frozenset[str] | None:
    """某个语言下「算洞察」的四个小节名；这个语言没有封闭词表时返回 None。

    翻译文件用的是译过的小节名（`## Ideas` 而不是 `## 核心想法`），所以合并
    「哪些小节可以被整体替换」必须按语言查表，否则在 project.en.md 上会一条也
    认不出来，提交的正文被整份丢掉——静默的空写，比覆盖更难发现。
    """
    names = {v[lang] for v in INSIGHT_NAMES.values() if lang in v}
    return frozenset(names) if len(names) == len(INSIGHT_NAMES) else None


def _merge_insights(old_body: str, submitted: str, lang: str = "zh") -> str:
    """整体替换洞察时，只换那四个洞察小节，其余小节逐字保留。

    为什么不能整段覆盖：网页那个「编辑洞察」框是用**打开页面那一刻**的正文预填的，
    而 project.md 的改动根本不进目录指纹、不推 SSE，所以那份预填必然是旧的。
    按提交的文本整段写回去，会把这期间 agent 记的洞察、以及删除步骤时写下的
    「为什么删的」一起抹掉——而后者是目录被删之后 G4 唯一还能 grep 到的证据
    （见 _log_deletion 的说明）。

    合并规则是「磁盘上的非洞察小节永远赢」：提交文本里出现的同名小节（比如用户
    把整段正文连「## 已删除」一起贴回来了）直接丢弃，不参与合并。
    这样不管上层怎么调这个函数，都不可能从这条路毁掉删除记录。

    `lang` 决定按哪套小节名判定，于是同一条保护也罩得住 project.en.md 里手写的
    `## Deleted`。**词表之外的语言**（INSIGHT_NAMES 只封了 zh/en）没法分辨哪一节
    是洞察、哪一节是删除记录，此时按提交的正文整份写：那个语言的文件从来不由
    本模块自己写入任何东西（_log_deletion 只写 project.md），所以没有「系统写下的
    证据」会被吃掉；里面的内容全是写的人自己放进去的，由他自己负责。
    """
    headings = insight_headings(lang)
    if headings is None:
        return "\n\n".join(
            t for t in ("\n".join(blk).strip("\n") for _h, blk in _split_sections(submitted))
            if t.strip())
    keep = [blk for h, blk in _split_sections(old_body)
            if h is not None and h not in headings]
    fresh = [blk for h, blk in _split_sections(submitted)
             if h is None or h in headings]
    chunks = []
    for blk in fresh + keep:
        t = "\n".join(blk).strip("\n")
        if t.strip():
            chunks.append(t)
    return "\n\n".join(chunks)


def _read_project_note(d: Path) -> tuple[dict[str, str], str]:
    """读 project.md。非 UTF-8 就拒绝——继续写下去会把原文替换成一串问号。"""
    note = d / PROJECT_NOTE
    if not note.is_file():
        return {}, ""
    meta, body, _w = parse_note(read_text_strict(note, "项目笔记"))
    return meta, body


def _write_project_note(d: Path, name: str, body: str, lang: str = "") -> None:
    """回写 project.md。

    `lang` 要**原样带回去**：它是这份笔记自己声明的主语言，翻译文件靠它判断
    「哪个语言不需要翻译」。这个函数是全量重写 front-matter 的，不显式带上就等于
    每次改一条洞察都把用户手写的 `lang:` 悄悄删掉一次。
    """
    body = body.strip()
    head = f"---\nname: {name}\n" + (f"lang: {lang}\n" if lang else "") + "---\n\n"
    write_atomic(d / PROJECT_NOTE, _utf8_bytes(head + (f"{body}\n" if body else ""), "项目笔记"))


def update_project(root: Path, slug: str, *, name: str | None = None,
                   insights: str | None = None,
                   add: tuple[str, str] | None = None) -> Project:
    """改项目的显示名和/或洞察正文。

    `add=(kind, text)` 往对应小节追加一条，小节不存在就补出来。
    `insights=…` 整体替换，但**只替换那四个洞察小节**（见 _merge_insights）。
    **目录名（= URL 里的 slug）永远不动**——改了会让所有已发出的链接失效。
    """
    d = project_dir(root, slug)
    if not d.is_dir():
        raise NotFound(f"项目 {slug} 不存在")

    with project_lock(d):
        meta, body = _read_project_note(d)

        final_name = _clean_line(name) if name is not None else (meta.get("name") or slug).strip()
        if not final_name:
            raise WriteError("项目名不能为空")

        if insights is not None:
            body = _merge_insights(
                body, str(insights).replace("\r\n", "\n").replace("\r", "\n"))
        if add is not None:
            kind, text = add
            heading = INSIGHT_SECTIONS.get(kind)
            if heading is None:
                raise WriteError(f"洞察类型必须是 {'/'.join(INSIGHT_SECTIONS)} 之一，收到 {kind!r}")
            text = re.sub(r"\s*\n\s*", " ", str(text or "")).strip()
            if not text:
                raise WriteError("洞察内容不能为空")
            body = _append_under(body, heading, "- " + text)

        _write_project_note(d, final_name, body, (meta.get("lang") or "").strip())
    return Project(slug=slug, name=final_name, body=body.strip())


def _append_under(body: str, heading: str, line: str) -> str:
    """把一行追加到 `## <heading>` 小节末尾；小节不存在就按模板顺序插进去。"""
    lines = (body or "").split("\n")
    start = None
    for i, l in enumerate(lines):
        if re.match(rf"^\s*#{{1,6}}\s+{re.escape(heading)}\s*$", l):
            start = i
            break
    if start is None:
        prefix = lines + ([""] if lines and lines[-1].strip() else [])
        return "\n".join(prefix + [f"## {heading}", line]).strip("\n")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^\s*#{1,6}\s+\S", lines[j]):
            end = j
            break
    block = lines[start:end]
    while block and not block[-1].strip():
        block.pop()
    return "\n".join(lines[:start] + block + [line, ""] + lines[end:]).strip("\n")


def rename_project(root: Path, slug: str, name: str) -> Project:
    """只改显示名。**目录名（= URL 里的 slug）不动**——改了会让所有已发出的链接失效。"""
    name = _clean_line(name)
    if not name:
        raise WriteError("项目名不能为空")
    d = project_dir(root, slug)
    if not d.is_dir():
        raise NotFound(f"项目 {slug} 不存在")
    with project_lock(d):
        meta, body = _read_project_note(d)
        _write_project_note(d, name, body, (meta.get("lang") or "").strip())
    return Project(slug=slug, name=name, body=body)


# ---------------------------------------------------------------- 步骤


def load(steps_dir: Path) -> dict[str, Step]:
    raw, _files, _w = scan(steps_dir, with_files=False)
    by_id, _w2 = validate(raw)
    return by_id


def alloc_id(by_id: dict[str, Step], parent: str | None) -> str:
    """分配一个永不需要重命名的 id。

    规则：
      * 无父，或父尚无子节点 → 全局最大数字 + 1，如 004
      * 父已有子节点         → 取其首个子节点的数字部分，配下一个可用字母 → 004b、004c

    于是 003 分叉后读作 004 / 004b / 004c：兄弟共享数字，一眼看得出兄弟关系，
    而且任何已有 id 都不会因为后来多出一个兄弟而被改名（原规格书的 003a/003b
    方案做不到这一点——加第二个子节点时必须把第一个从 004 改名成 004a）。
    """
    nums: list[int] = []
    for sid in by_id:
        parts = split_id(sid)
        if parts:
            nums.append(int(parts[0]))
    nxt = fmt_id((max(nums) + 1) if nums else 1)

    if not parent:
        return nxt

    kids = sorted((sid for sid, s in by_id.items() if s.parent == parent), key=id_key)
    if not kids:
        return nxt

    first = split_id(kids[0])
    if not first:
        return nxt
    num = first[0]
    used = set()
    for sid in by_id:
        parts = split_id(sid)
        if parts and parts[0] == num:
            used.add(parts[1])
    for letter in "bcdefghijklmnopqrstuvwxyz":
        if letter not in used:
            return f"{num}{letter}"
    return nxt  # 一个父节点有 25 个以上分支时退回新号段


def render_note(step: Step) -> str:
    """序列化成 note.md。键顺序固定，保证同样的数据永远产出同样的字节。"""
    lines = ["---", f"id: {step.id}"]
    if step.parent:
        lines.append(f"parent: {step.parent}")
    lines.append(f"status: {step.status}")
    lines.append(f"title: {step.title}")
    # lang 也要写回去。它是这份 note.md 自己声明的主语言，翻译文件靠它判断
    # 「这个语言不用翻」。render_note 是全量重写 front-matter 的，漏掉一个键
    # 就等于每次在网页上点一下 done 都把用户手写的那一行悄悄删掉。
    # getattr 兜底：core 里还没有这个字段时（或纯手工造的 Step）当没写。
    for k in ("lang", "date", "commit", "author", "key"):
        v = getattr(step, k, "")
        if v:
            lines.append(f"{k}: {v}")
    if step.tags:
        lines.append("tags: " + ", ".join(step.tags))
    for p in step.paths:
        note = p.get("note", "").strip()
        lines.append(f"path: {p['location']}" + (f" | {note}" if note else ""))
    for r in step.repro:
        lines.append("repro: " + " | ".join([r.get("state", "unknown"), r.get("date", ""),
                                             r.get("by", ""), r.get("note", "")]).rstrip(" |"))
    lines.append("---")
    lines.append("")
    lines.append(step.body.rstrip("\n"))
    lines.append("")
    return "\n".join(lines)


def step_dir(steps_dir: Path, by_id: dict[str, Step], sid: str) -> Path:
    if sid not in by_id:
        raise NotFound(f"步骤 {sid} 不存在")
    return steps_dir / by_id[sid].dirname


def _clean_line(v: Any) -> str:
    """front-matter 是行式格式，值里不能有换行。"""
    return re.sub(r"[\r\n]+", " ", str(v or "")).strip()


def norm_paths(raw: Any) -> list[dict[str, str]]:
    """把外部传来的路径规整成 [{location, note, kind}]。

    接受 "位置 | 说明" 这样的字符串，也接受 {"location":…, "note":…} 这样的字典，
    单条也可以不套列表——agent 和人怎么写都能接住。
    kind 是派生的，从位置形状猜出来，不存也不接受外部传入。
    """
    if not raw:
        return []
    items = [raw] if isinstance(raw, (str, dict)) else list(raw)
    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            loc, note = _clean_line(item.get("location")), _clean_line(item.get("note"))
        else:
            head, _, tail = str(item).partition("|")
            loc, note = _clean_line(head), _clean_line(tail)
        if not loc:
            continue
        out.append({"location": loc, "note": note, "kind": path_kind(loc)})
    return out


# ---------------------------------------------------------------- 创建


def create_step(
    steps_dir: Path,
    *,
    parent: str | None = None,
    title: str = "",
    status: str = DEFAULT_STATUS,
    body: str | None = None,
    date: str = "",
    commit: str = "",
    author: str = "",
    key: str = "",
    tags: list[str] | None = None,
    paths: Any = None,
    lang: str = "",
) -> tuple[Step, bool]:
    """新建一步。返回 (step, created)；created=False 表示命中幂等键返回了既有步骤。

    从 load() 到 mkdir() 整段在项目锁里：alloc_id 是「扫一遍取 max+1」，
    只要两个写入者各自的 load() 都在对方 mkdir() 之前完成，就会分到同一个号。
    幂等键的判重和建目录同理——不在一把锁里的话，5 个进程用同一个 key 并发重试，
    会有 4 个撞在 mkdir 上抛裸 FileExistsError（服务端接不住，漏成 500）。
    """
    steps_dir.mkdir(parents=True, exist_ok=True)
    with project_lock(steps_dir.parent):
        by_id = load(steps_dir)

        key = _clean_line(key)
        if key:
            for s in sorted(by_id.values(), key=lambda s: id_key(s.id)):
                if s.key == key:
                    return s, False  # agent 重试不会造出重复步骤

        parent = (parent or "").strip() or None
        if parent and parent not in by_id:
            raise NotFound(f"parent {parent} 不存在")

        status = (status or DEFAULT_STATUS).strip().lower()
        if status not in STATUSES:
            raise WriteError(f"status 必须是 {'/'.join(STATUSES)} 之一，收到 {status!r}")

        title = _clean_line(title)
        if not title:
            raise WriteError("title 不能为空")

        sid = alloc_id(by_id, parent)
        step = Step(
            id=sid,
            parent=parent,
            status=status,
            title=title,
            # 调用方没给日期就用服务端本地日期。FORMAT.md 把 front-matter 定义为
            # 「机器记录」，日期属于机器该自己填的那一类；不填的后果是 agent 建的
            # 步骤（占多数）永远没有时间坐标，「这条线是什么时候走的」就答不了了。
            date=_clean_line(date) or today(),
            commit=_clean_line(commit),
            author=_clean_line(author),
            key=key,
            tags=[_clean_line(t) for t in (tags or []) if _clean_line(t)],
            paths=norm_paths(paths),
            body=(BODY_TEMPLATE if body is None else body),
            dirname=f"{sid}_{slugify(title)}",
            # 主语言是**声明**出来的，不是猜出来的。没有这个参数的时候，
            # 读的一侧只能从小节名反推（封闭词表，还算确定），而网页上人明明
            # 在下拉框里选过「用英文写」——那次选择必须落盘，否则界面就只能
            # 对后来的读者说「这是原文」而说不出是哪种语言的原文。
            lang=norm_lang(lang) if _clean_line(lang) else "",
        )

        text = _utf8_bytes(render_note(step), "正文")
        d = steps_dir / step.dirname
        if d.exists():
            raise Conflict(f"目录已存在: {step.dirname}")
        try:
            d.mkdir(parents=True)
        except FileExistsError:
            raise Conflict(f"目录已存在: {step.dirname}") from None
        write_atomic(d / NOTE_NAME, text)
    return step, True


# ---------------------------------------------------------------- 修改

MUTABLE = ("status", "title", "body", "date", "commit", "author", "tags",
           "paths", "add_paths", "add_repro", "lang")


def norm_repro(raw: Any) -> list[dict[str, str]]:
    """把一条复现记录规整成 {state, date, by, note}。

    只接受追加，不接受整组替换——复现历史是只追加的：
    "去年试过、失败了" 和 "今年试过、成功了" 是两条事实，后者不该抹掉前者。
    """
    from trace_core import REPRO_STATES

    items = [raw] if isinstance(raw, (str, dict)) else list(raw or [])
    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            d = {k: _clean_line(item.get(k)) for k in ("state", "date", "by", "note")}
        else:
            parts = [_clean_line(x) for x in str(item).split("|")]
            d = dict(zip(("state", "date", "by", "note"), parts + [""] * 4))
            if len(parts) > 4:
                d["note"] = " | ".join(parts[3:])
        state = (d.get("state") or "").lower()
        if state not in REPRO_STATES:
            raise WriteError(f"复现结果必须是 {'/'.join(REPRO_STATES)} 之一，收到 {d.get('state')!r}")
        d["state"] = state
        out.append(d)
    return out


def update_step(steps_dir: Path, sid: str, patch: dict[str, Any], *, expect: str = "") -> Step:
    """只允许改 status / title / body / date / commit / author / tags。

    id 和 parent 是只追加系统的地基——任何引用（笔记里写的"见 003b"、论文里的脚注）
    永远有效，靠的就是它们不变。改它们的请求一律 409。

    `expect` 是乐观并发控制：传当前 note.md 的摘要指纹（digest_of），
    对不上就抛 Conflict 而不是照写。不传就是「不检查」，保持老行为。
    """
    if isinstance(patch, dict) and "expect" in patch:
        # 服务端可能把整个请求体当 patch 透传，让 expect 从这里进来也接住，
        # 免得它变成「不支持的字段」而把冲突检测整个绕过去。
        patch = dict(patch)
        carried = str(patch.pop("expect") or "")
        expect = expect or carried
    expect = str(expect or "").strip()

    with project_lock(steps_dir.parent):
        return _update_step_locked(steps_dir, sid, patch, expect)


def _update_step_locked(steps_dir: Path, sid: str, patch: dict[str, Any], expect: str) -> Step:
    by_id = load(steps_dir)
    if sid not in by_id:
        raise NotFound(f"步骤 {sid} 不存在")
    step = by_id[sid]

    if "~" in step.id:
        # `001~dup2` 是 validate() 在发现两个同 id 目录时给后来者贴的**临时显示标签**，
        # 纯派生。照常写下去，render_note 会把它写进 front-matter 的 id: ——
        # 派生信息就变成了存储信息（违反 P1），而且写进去的是个不合法的 id：
        # 它排不进 id 序、parent 关系跟着断、check 的退出码反而从 1 变回 0。
        raise Conflict(
            f"{sid} 不是这一步真正的 id，只是「有两个目录用了同一个 id」时的临时标签。"
            "现在改它会把这个标签写死进 note.md。请先在文件系统里把两个同号目录之一"
            "改成新号（目录名和 note.md 里的 id: 都要改），再来改这一步。")

    note_path = steps_dir / step.dirname / NOTE_NAME
    read_text_strict(note_path, "这一步的 note.md")   # 非 UTF-8 就别动它，见函数说明

    if expect:
        current = digest_of(note_path)
        if current != expect:
            raise Conflict(
                f"这一步在你读到它之后被改过（你拿的是 {expect}，磁盘上现在是 {current}）。"
                "别直接重试——先重新读一遍，把你的改动合到最新内容上再存，"
                "否则会把这期间别人（很可能是你自己的 agent）写进去的整段内容抹掉。")

    for locked in ("id", "parent"):
        if locked in patch and _clean_line(patch[locked]) != _clean_line(getattr(step, locked)):
            raise Conflict(f"{locked} 不可修改（只追加原则）")

    unknown = set(patch) - set(MUTABLE) - {"id", "parent"}
    if unknown:
        raise WriteError("不支持的字段: " + ", ".join(sorted(unknown)))

    if "status" in patch:
        st = _clean_line(patch["status"]).lower()
        if st not in STATUSES:
            raise WriteError(f"status 必须是 {'/'.join(STATUSES)} 之一")
        step.status = st
    if "title" in patch:
        t = _clean_line(patch["title"])
        if not t:
            raise WriteError("title 不能为空")
        step.title = t
    if "body" in patch:
        step.body = str(patch["body"] or "").replace("\r\n", "\n").replace("\r", "\n")
    for k in ("date", "commit", "author"):
        if k in patch:
            setattr(step, k, _clean_line(patch[k]))
    if "tags" in patch:
        raw = patch["tags"]
        if isinstance(raw, str):
            raw = [t for t in re.split(r"[,，]", raw)]
        step.tags = [_clean_line(t) for t in (raw or []) if _clean_line(t)]
    if "paths" in patch:
        step.paths = norm_paths(patch["paths"])            # 整组替换
    if "add_paths" in patch:                                # 追加，位置去重
        seen = {p["location"] for p in step.paths}
        step.paths = step.paths + [p for p in norm_paths(patch["add_paths"]) if p["location"] not in seen]
    if "add_repro" in patch:                                # 只追加：复现历史不覆盖
        step.repro = step.repro + norm_repro(patch["add_repro"])
    if "lang" in patch:
        # 空串是**撤回声明**（回到「原文，语种不详」），不是非法值——
        # 写错了要能改回来，否则一个手滑的 lang: ja 会永久留在那儿。
        raw = _clean_line(patch["lang"])
        step.lang = norm_lang(raw) if raw else ""

    # 目录名不跟着 title 改：目录名里的 id 是给 shell 补全用的，
    # 改名会让所有已经发出去的相对链接失效。
    write_atomic(note_path, _utf8_bytes(render_note(step), "正文"))
    return step


def delete_step(steps_dir: Path, sid: str, reason: str, by: str = "", date: str = "") -> dict[str, Any]:
    """真删：整个目录连同附件一起移除。

    这是对 P2「只追加」的一处**有意的例外**，用来处理"这条记录本身就不该存在"
    ——误建、测试数据、不小心粘进去的令牌。它和 `dead` 是两件事：
    `dead` 是研究结论（此路不通），往里塞垃圾会毁掉这套系统最有价值的信号。

    两个已知代价，是明确接受的：

    1. **id 会被重用。** 分配用的是"现存 id 的最大值 + 1"，删掉最大号之后
       下一步就会拿到同一个号。于是半年前笔记里的「见 002」可能指向另一个东西。
       要避免就得有个"用过哪些号"的中心文件，而那正是 P1 禁止的。
    2. **子步骤会变成孤儿。** 它们的 parent 指向一个不存在的 id，
       validate() 会把它们降级为根并给出警告——构建不会崩，但那条线断了。

    所以返回值里明确告诉调用方这两件事各自发生了多少，让人能当场看见后果。
    """
    reason = _clean_line(reason)
    if not reason:
        raise WriteError(
            "删除必须写原因。目录一删，「为什么删的」就只剩这一句了——"
            "它和「为什么做的」一样，是半年后唯一追得回来的东西。")

    with project_lock(steps_dir.parent):
        by_id = load(steps_dir)
        if sid not in by_id:
            raise NotFound(f"步骤 {sid} 不存在")
        target = by_id[sid]

        children = sorted((k for k, s in by_id.items() if s.parent == sid), key=id_key)
        refs = sorted(k for k, s in by_id.items()
                      if k != sid and re.search(rf"\[\[\s*{re.escape(sid)}\s*\]\]", s.body))

        d = steps_dir / target.dirname
        # rglob 数的是目录里所有文件，所以 note.md、每一份 note.<lang>.md 和全部
        # 附件都算进 files_removed —— 报出去的数字必须和磁盘上真消失的份数对得上，
        # 「删掉一步会连译文一起没」这件事也就自然被说出来了。
        files = sum(1 for _ in d.rglob("*") if _.is_file())

        # **先记原因，再删目录。** 反过来写的话，rmtree 半途失败（Windows 上
        # 文件被 Defender / OneDrive / 预览窗格占用极其常见）会留下最坏的组合：
        # note.md 已经没了、步骤从树上消失、残骸还在，而「为什么删的」一个字都没留。
        # 原因是这次操作唯一的幸存者，它必须第一个落盘。
        entry = _log_deletion(steps_dir.parent, sid, target.title, reason, by,
                              _clean_line(date) or today())
        try:
            shutil.rmtree(d, **_RMTREE_KW)
        except OSError as exc:
            # 不回滚那一行，而是就地标注「删除未完成」。理由是 G4：
            # 目录此刻多半已经被删掉一部分（rmtree 不是原子的），把唯一记下来的
            # 「为什么」再抹掉，就等于既毁了记录又不留痕迹。留着并说明状态，
            # 人 grep 得到「有人想删这一步、原因是什么、没删干净」这三件事。
            _mark_deletion_incomplete(steps_dir.parent, entry, exc)
            raise WriteError(
                f"删除 {sid} 时目录没能删干净：{exc}。"
                "原因已经记进 project.md 并标注为「删除未完成」。"
                "常见于文件被别的程序占用（杀软扫描、云盘同步、预览窗格）——"
                f"关掉占用的程序后重试，或手工删掉残留的 {d.name}/。") from None

    return {"id": sid, "title": target.title, "reason": reason,
            "files_removed": files, "orphaned": children, "dangling_refs": refs}


def _on_rm_error(func, path, _exc) -> None:
    """rmtree 在 Windows 上遇到只读文件会 PermissionError。清掉只读位再试一次。

    签名同时兼容 onerror(func, path, exc_info) 和 3.12 的 onexc(func, path, exc)，
    第三个参数两边都用不上。
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise            # 还是不行就抛回去——半删状态必须让调用方知道


# onerror 在 3.12 起被 onexc 取代（前者会发 DeprecationWarning）。
_RMTREE_KW: dict[str, Any] = ({"onexc": _on_rm_error} if sys.version_info >= (3, 12)
                              else {"onerror": _on_rm_error})


def _log_deletion(project_dir_: Path, sid: str, title: str, reason: str, by: str, date: str) -> str:
    """把删除记进项目的 project.md，返回写下去的那一行（供失败时定位）。

    这**不是**中心索引——没有任何东西靠它重建结构，所以 P1 不破，它也不可能
    "和实际内容不一致"。它只是一份人可读、可 grep 的历史：目录没了之后，
    「006 是什么、为什么删的」只剩这一行。
    """
    meta, body = _read_project_note(project_dir_)
    stamp = " · ".join(x for x in (date.strip(), by.strip()) if x.strip())
    entry = f"- `{sid}` {title}".rstrip() + f" —— {reason}" + (f"（{stamp}）" if stamp else "")
    # 小节名跟着**这份文件自己**的语言走：删除记录必须和它旁边的洞察是同一套标题，
    # 否则一份 `lang: en` 的 project.md 里会同时冒出 `## Deleted` 和 `## 已删除`，
    # 而 G4 那句「grep 得到为什么删的」就得看运气搜对哪一个词。
    body = _append_under(body, DELETED_NAME.get((meta.get("lang") or "").strip(),
                                                DELETED_NAME["zh"]), entry)
    name = (meta.get("name") or project_dir_.name).strip()
    _write_project_note(project_dir_, name, body, (meta.get("lang") or "").strip())
    return entry


def _mark_deletion_incomplete(project_dir_: Path, entry: str, exc: BaseException) -> None:
    """把刚记下的那一行标注成「删除未完成」，并说明卡在哪。"""
    try:
        meta, body = _read_project_note(project_dir_)
        if entry not in body:
            return
        body = body.replace(entry, entry + f"  ⚠ 删除未完成，目录里还有残留：{exc}", 1)
        _write_project_note(project_dir_, (meta.get("name") or project_dir_.name).strip(),
                            body, (meta.get("lang") or "").strip())
    except (WriteError, OSError):
        pass    # 标注失败也不能盖掉真正的删除错误——那条才是调用方要看的


def append_body(steps_dir: Path, sid: str, text: str) -> Step:
    # 读-改-写要整段在锁里，否则两次并发 append 会互相吃掉一段。
    # 锁是同线程可重入的，里面那次 update_step 不会把自己堵死。
    with project_lock(steps_dir.parent):
        by_id = load(steps_dir)
        if sid not in by_id:
            raise NotFound(f"步骤 {sid} 不存在")
        body = by_id[sid].body.rstrip("\n") + "\n\n" + text.strip("\n") + "\n"
        return update_step(steps_dir, sid, {"body": body})


# ---------------------------------------------------------------- 翻译
#
# 存储是双文件：note.md 带结构 + 主语言正文，note.<lang>.md 只带一个 title 和译文。
# 翻译文件里**不许**出现任何结构键（id / parent / status / date / commit / …）。
# 这不是洁癖：上一代系统（ai-training-logbook）的死因就是同一个事实存在两处，
# 写一处漏一处。所以这里从函数形状上杜绝——渲染翻译文件的 _render_translation
# 只接受**一个**键名，调用方连传第二个键的地方都没有；正文里的换行也在
# _clean_line 里被压平，`title: x\nid: 999` 注不进去。
#
# 「还没翻译」是派生状态（文件不存在），绝不存。
#
# ── 翻译要不要进 digest / expect 的冲突控制链？────────────────────────────
#
# 结论：**每个翻译文件有自己的 expect，但翻译不进 note.md 那条链**，也就是说
# 系统不会知道「note.md 改了、note.en.md 过时了」。理由，按重要性排：
#
# 1. 「过时」算不出来。要算就得把翻译当时 note.md 的指纹写进翻译文件——那是把
#    派生关系变成存储字段（P1 明令禁止），而且正是双真相源的形状：FORMAT.md
#    鼓励人直接 `vim note.md`（页面五秒内自己跟上），手改之后那个指纹立刻变成
#    一句谎话，系统却会理直气壮地按它显示「已是最新」。错的状态比没有状态更贵。
# 2. 退而求其次拿 mtime 比更糟：git checkout、rsync、云盘同步、从备份恢复都会
#    重排 mtime，于是「翻译过时」在最常见的几种操作之后集体误报。误报的告警
#    等于没有告警——这正是 check 默认不把内容层缺陷算成失败的同一条理由。
# 3. 反过来让 update_step 在「有翻译」时拦一道或报个错，等于让译文挡住原文的
#    写入路径，方向正好反了：note.md 永远赢，翻译是可选的衍生物。用户明确不要
#    「缺翻译」的警告，「过时」比它更容易变成天天红的一片。
# 4. 真要这个功能，P1 兼容的做法是人自己在译文里写一句（那是可 grep 的文本，
#    G4 成立），或者外部工具同时读两份文件现算——都不需要这个系统存一个状态位。
#
# 于是 expect 只解决同一份文件上的并发覆盖（两个人同时翻同一步），
# 翻译的 expect 对的是**翻译文件自己**的 digest，和 note.md 那条链各管各的。

# 语言码同时是**文件名的一段**，所以它的校验既是格式问题也是路径安全问题。
# 形状和 core 的 TR_RE 一致：字母开头，随后是字母/数字/连字符，总长 ≤ 35。
# 收尾用 \Z 而不是 $：Python 的 `$` 会在**结尾的换行之前**也匹配，于是 "en\n"
# 能通过一条看起来严丝合缝的正则，落到文件名里就是一个带换行的文件（Linux 上合法）。
_LANG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,34}\Z")


def norm_lang(lang: str) -> str:
    """校验并归一化语言码。不合法一律 WriteError，绝不「尽量修好再用」。

    这个字符串是调用方（人或 agent）给的，直接拼进 `note.<lang>.md`。
    上面那条正则一次挡掉一整类东西：路径分隔符、`..`、`C:` 盘符、控制字符、
    空串、超长、以及**尾随空格和点**（Windows 打开文件前会把它们剥掉，于是
    `en.` 和 `en` 是同一个磁盘文件——safe_relpath 里已经为附件踩过这个坑）。

    大小写**归一化而不是拒绝**，理由：NTFS / APFS 上 `note.EN.md` 和 `note.en.md`
    是同一个文件，Linux 上却是两个。放任大小写自由，同一次调用在两个平台上的
    结果不一样：Windows 上是一次静默的别名覆盖（返回的 lang 是 EN，被改写的
    文件却叫 en），Linux 上是同一种语言分裂成 tr["EN"] 和 tr["en"] 两条记录。
    归一化让「一种语言 ⇒ 恰好一个文件名」在所有平台上成立，而且比拒绝友好——
    agent 顺手写个 `EN` 不该报错。归一化按 BCP-47 的惯例来（语言小写、四字母的
    文字段首字母大写、两字母的地区段大写），所以 `zh-hant` 和 `ZH-HANT` 都落到
    `note.zh-Hant.md` 这一个名字上。
    """
    # 只剥空格，不用 str.strip()：后者连 \n 和 \x1c-\x1f 都算空白，于是
    # `"en\n"`、`"en\x1f"` 会被悄悄「修好」成 en。语言码要变成文件名，
    # 收到控制字符只说明调用方那边有 bug，该当场说出来而不是替它擦干净。
    raw = str(lang or "").strip(" ")
    if not _LANG_RE.match(raw):
        raise WriteError(
            f"语言码不合法: {lang!r}。只允许字母开头、由字母/数字/连字符组成、"
            "不超过 35 个字符（en / ja / zh-Hant 这样的短码）。"
            "它会直接变成文件名的一段，所以路径分隔符、..、盘符、"
            "结尾的空格和点都不接受——Windows 会把结尾的空格和点剥掉，"
            "于是它会悄悄改写同目录下的另一个文件。")
    if any(not part for part in raw.split("-")):
        raise WriteError(f"语言码里有空的子段: {lang!r}（`en-` / `zh--Hant` 这种写法）。")

    parts = raw.split("-")
    out = [parts[0].lower()]
    for p in parts[1:]:
        out.append(p.title() if len(p) == 4 else p.upper() if len(p) == 2 else p.lower())
    norm = "-".join(out)

    if norm == "md":
        # note.md.md：既像「note.md 的翻译」又像一个附件，而 TR_RE 会把它读成
        # 一种叫 md 的语言。没有任何语言叫 md，出现它只可能是把扩展名当成了语言码。
        raise WriteError(
            "语言码不能是 md：那会造出 note.md.md，看起来像 note.md 的备份，"
            "实际会被当成一种叫「md」的语言。要写英文版请传 lang=\"en\"。")
    if norm in WIN_RESERVED:
        # 按目前的命名（note.<lang>.md）设备名其实碰不着——Windows 只看第一个点
        # 之前那一段，也就是 note/project。但语言码是调用方完全控制的字符串，
        # 一旦哪天命名方案变了这就变成活的漏洞，而拒掉的代价是零：
        # 只有 con/nul/aux 这类极冷门的 ISO 639-3 码会被误伤。
        raise WriteError(
            f"语言码 {norm!r} 撞上了 Windows 的设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）。"
            "这类名字在文件名里随时会打开设备而不是文件，请换一个。")
    return norm


def _render_translation(key: str, value: str, body: str) -> str:
    """翻译文件的唯一渲染器。**只接受一个键**——这就是「结构键进不来」的实现方式。

    key 取自 core 的 TR_ONLY_KEYS / PROJECT_TR_ONLY_KEYS，value 过 _clean_line
    压掉换行（否则 `title: x\\nid: 999` 就能在 front-matter 里凭空多出一行 id）。
    front-matter 的 `---` 围栏永远写，哪怕没有 title：省掉它会让 parse_note
    报一条 no_front_matter 警告，于是每份只有正文的译文都在页面顶栏挂一条黄字。
    """
    value = _clean_line(value)
    head = "---\n" + (f"{key}: {value}\n" if value else "") + "---\n\n"
    body = str(body or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return head + (f"{body}\n" if body else "")


def _translation_name(stem: str, lang: str) -> str:
    return f"{stem}.{lang}.md"


def _declared_lang(meta: dict[str, str]) -> str:
    """原文自己声明的主语言，读不出合法语言码就当没声明。"""
    try:
        return norm_lang(meta.get("lang", ""))
    except WriteError:
        return ""


def _guard_same_lang(declared: str, lang: str, what: str) -> None:
    """不许给「原文已经是这个语言」的记录再写一份同语言译文。

    那份文件会和 note.md 的正文各说各话，而且两边都会被读侧当成有效内容
    （小节算不算「写了」是 note.md 或任一译文里有它）——同一个事实两处存储，
    正是这套双语设计一开始就要绕开的那个坑。
    """
    if declared and declared == lang:
        raise WriteError(
            f"{what}自己声明的就是 {lang}（front-matter 的 lang:），"
            f"再写一份 {lang} 译文会和正文各说各话。改正文请走正文那条路。")


def write_translation(steps_dir: Path, sid: str, lang: str, *,
                      title: str = "", body: str = "", expect: str = "") -> dict[str, Any]:
    """写 note.<lang>.md。它**只碰翻译文件**，一个字节都到不了 note.md。

    于是「建完步骤马上补翻译」和「过几天再补」是同一条代码路径，只是调用时机不同。

    `expect` 对的是**翻译文件自己**的 digest（不是 note.md 的，理由见本节顶部
    那段长注释）。文件还不存在时 digest 是空串，而空的 expect 一律当「不检查」
    ——和 update_step 的约定一致，所以这个接口表达不了「只在还没有译文时创建」。
    """
    lang = norm_lang(lang)
    expect = str(expect or "").strip()
    if not _clean_line(title) and not str(body or "").strip():
        raise WriteError(
            f"翻译的标题和正文不能同时为空。写一份空的 note.{lang}.md 会让界面认为"
            f"「这一步已经有 {lang} 版了」，于是不再回退到原文，读者看到的是一片空白。"
            "要撤掉某个语言版本请用 drop_translation。")

    with project_lock(steps_dir.parent):
        d, _step = _translation_target(steps_dir, sid)
        target = d / _translation_name("note", lang)
        _guard_same_lang(_declared_lang(_note_meta(d / NOTE_NAME)), lang, f"步骤 {sid}")
        _check_translation_expect(target, expect, "这一步的译文")
        write_atomic(target, _utf8_bytes(
            _render_translation(TR_ONLY_KEYS[0], title, body), f"{lang} 译文"))
    return {"id": sid, "lang": lang, "path": target.name,
            "digest": digest_of(target), "written": True}


def drop_translation(steps_dir: Path, sid: str, lang: str) -> dict[str, Any]:
    """删掉某一个语言版本。删的是译文，note.md 不受影响。

    这不是 P2 的例外：只追加约束的是**记录本身**（id / parent / 结论），
    译文是原文的衍生物，删掉它之后界面自动回退到原文，一个事实都没丢。
    """
    lang = norm_lang(lang)
    with project_lock(steps_dir.parent):
        d, _step = _translation_target(steps_dir, sid)
        target = d / _translation_name("note", lang)
        if not target.is_file():
            raise NotFound(f"步骤 {sid} 没有 {lang} 译文")
        target.unlink()
    return {"id": sid, "lang": lang, "path": target.name, "removed": True}


def write_project_translation(root: Path, slug: str, lang: str, *,
                              name: str = "", body: str = "", expect: str = "") -> dict[str, Any]:
    """写 project.<lang>.md。front-matter 里只有 name，其余结构一律不重复。

    正文走和中文版同一道闸门：**提交的文本只能替换那四个洞察小节**，磁盘上
    其它小节（尤其是手写的 `## Deleted`）逐字保留，提交里同名的那一节直接丢弃。
    删除记录是目录没了之后仅存的证据，中文版护得住、英文版护不住是说不过去的。
    """
    lang = norm_lang(lang)
    expect = str(expect or "").strip()
    d = project_dir(root, slug)
    if not d.is_dir():
        raise NotFound(f"项目 {slug} 不存在")

    with project_lock(d):
        _guard_same_lang(_declared_lang(_note_meta(d / PROJECT_NOTE)), lang, f"项目 {slug}")
        target = d / _translation_name("project", lang)
        _check_translation_expect(target, expect, "这个项目的译文")

        old_meta, old_body = ({}, "")
        if target.is_file():
            old_meta, old_body, _w = parse_note(read_text_strict(target, f"{lang} 项目译文"))
        merged = _merge_insights(old_body, str(body or "").replace("\r\n", "\n").replace("\r", "\n"),
                                 lang)
        final_name = _clean_line(name) or _clean_line(old_meta.get(PROJECT_TR_ONLY_KEYS[0], ""))
        if not final_name and not merged.strip():
            raise WriteError(
                f"项目名和正文不能同时为空。空的 project.{lang}.md 会让界面认为"
                "已经有这个语言版本了，于是不再回退到原文。")
        write_atomic(target, _utf8_bytes(
            _render_translation(PROJECT_TR_ONLY_KEYS[0], final_name, merged), f"{lang} 项目译文"))
    return {"slug": slug, "lang": lang, "path": target.name,
            "digest": digest_of(target), "written": True}


def drop_project_translation(root: Path, slug: str, lang: str) -> dict[str, Any]:
    """删掉项目笔记的某个语言版本。project.md 不受影响。

    和 drop_translation 同一个理由；单独存在只是因为项目笔记不在 steps_dir 下。
    没有它的话，服务端要么让人手工去删文件（那就没有唯一写入路径了），
    要么自己 unlink 一把——那正是这个仓库用结构杜绝的第二条写入路径。
    """
    lang = norm_lang(lang)
    d = project_dir(root, slug)
    if not d.is_dir():
        raise NotFound(f"项目 {slug} 不存在")
    with project_lock(d):
        target = d / _translation_name("project", lang)
        if not target.is_file():
            raise NotFound(f"项目 {slug} 没有 {lang} 译文")
        target.unlink()
    return {"slug": slug, "lang": lang, "path": target.name, "removed": True}


def _translation_target(steps_dir: Path, sid: str) -> tuple[Path, Step]:
    """解析出「该往哪个步骤目录写译文」。调用方必须已经拿着项目锁。"""
    by_id = load(steps_dir)
    if sid not in by_id:
        raise NotFound(f"步骤 {sid} 不存在")
    step = by_id[sid]
    if "~" in step.id:
        # 和 update_step 同一条理由：`001~dup2` 是「两个目录用了同一个 id」时贴的
        # 临时显示标签，纯派生。往它身上挂译文，等于把译文绑在一个下次扫描就可能
        # 换目录的标签上——两个同号目录谁先被 iterdir 读到，谁就是 001。
        raise Conflict(
            f"{sid} 不是这一步真正的 id，只是「有两个目录用了同一个 id」时的临时标签。"
            "请先在文件系统里把两个同号目录之一改成新号，再来写它的译文。")
    return steps_dir / step.dirname, step


def _note_meta(note_path: Path) -> dict[str, str]:
    """只取原文的 front-matter（顺带确认它是 UTF-8）。文件不在就当空的。"""
    if not note_path.is_file():
        return {}
    meta, _body, _w = parse_note(read_text_strict(note_path, note_path.name))
    return meta


def _check_translation_expect(target: Path, expect: str, what: str) -> None:
    """译文文件的乐观并发控制 + 非 UTF-8 拒绝改写。两件事都只看这一个文件。"""
    if target.is_file():
        read_text_strict(target, what)      # 不是 UTF-8 就别动它，等人先转码
    if not expect:
        return
    current = digest_of(target)
    if current != expect:
        raise Conflict(
            f"{what}在你读到它之后被改过（你拿的是 {expect}，磁盘上现在是 {current!r}）。"
            "别直接重试——先重新读一遍，把你的改动合到最新内容上再存，"
            "否则会把这期间别人写进去的整段译文抹掉。")


# ---------------------------------------------------------------- 附件


# Windows 的设备名。这些名字（连同带任意扩展名的形式，CON.txt 也算）不是文件，
# 打开它们会连到设备上：写进去的内容凭空消失，读出来的是设备数据。
WIN_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)


def fs_key(name: str) -> str:
    """把文件名规范化成「文件系统实际会认成同一个文件」的形式，用于比较而不是落盘。

    逐字节比较挡不住 note.md：NTFS 和 macOS 默认的 APFS 大小写不敏感，
    Windows 还会在打开文件前剥掉名字尾部的空格和点，并把 `note.md:foo`
    解释成 note.md 的备用数据流（ADS）。所以 NOTE.MD / Note.Md / "note.md "
    / "note.md." / "note.md:x" 全都指向同一个文件。
    """
    s = name.split(":", 1)[0]        # ADS：冒号后面是流名，落盘的还是冒号前那个文件
    s = s.rstrip(" .")
    return unicodedata.normalize("NFC", s).casefold()


def safe_relpath(relpath: str) -> str:
    """防目录穿越。拒绝绝对路径、盘符、.. 和控制字符，并挡掉会和别的文件撞成
    同一个磁盘文件的别名写法。"""
    rel = (relpath or "").replace("\\", "/")
    if not rel.strip():
        raise WriteError("文件名不能为空")
    # 绝对路径的判断必须在 strip("/") 之前，否则 "/etc/passwd" 会被悄悄
    # 重新解释成相对路径 "etc/passwd" 而不是被拒绝。
    if re.match(r"^[A-Za-z]:", rel) or rel.startswith("/"):
        raise WriteError("不接受绝对路径")
    rel = rel.strip("/")
    parts = []
    for part in rel.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or part.startswith("."):
            raise WriteError("非法路径段: " + part)
        if re.search(r'[\x00-\x1f<>:"|?*]', part):
            raise WriteError("文件名含非法字符: " + part)
        if part != part.rstrip(" ."):
            # `report.txt.` 在 Windows 上落盘就是 report.txt——返回给调用方的 path
            # 和磁盘上真正被改写的文件不是同一个，等于一条静默的别名覆盖通道。
            raise WriteError(
                f"文件名不能以空格或点结尾: {part!r}。"
                "Windows 会把结尾的空格和点剥掉，于是它会悄悄改写同目录下的另一个文件。")
        if fs_key(part).split(".", 1)[0] in WIN_RESERVED:
            raise WriteError(
                f"{part!r} 撞上了 Windows 的设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9，"
                "带扩展名也算）。这类名字打开的是设备而不是文件，请换个名字。")
        parts.append(part)
    if not parts:
        raise WriteError("文件名不能为空")
    out = "/".join(parts)
    if len(parts) == 1 and is_record_filename(parts[0]):
        # 只挡步骤目录顶层的记录文件（子目录里的 note.md 是普通附件，
        # list_files 也只在顶层排除它）。这里必须按规范化之后比较：
        # 逐字节比 "note.md" 的话，NOTE.MD 一路通过，写完整条记录就被上传的字节替换掉了。
        raise WriteError(
            f"{parts[0]!r} 在大小写不敏感的文件系统上就是这一步的记录本身"
            "（note.md 或它的某个语言版本 note.<lang>.md），"
            "上传它会把整条记录替换成文件内容。改记录请用 PATCH /api/steps/{id}，"
            "改译文请用补翻译那条路；如果这真是一个附件，请换个名字。")
    return out


def is_record_filename(name: str) -> bool:
    """这个名字是不是「记录本身」——note.md / note.<lang>.md / project(.<lang>).md。

    附件上传必须挡住它们的**全部别名形式**。老守卫只认 note.md 的别名，于是传一个
    叫 `note.en.md` 的附件照样能顶掉整份英文记录（`NOTE.EN.MD`、`note.en.md.`、
    `note.en.md:x` 同理）。走 fs_key 之后大小写、尾随空格与点、NTFS 备用数据流
    这三种别名一次性收口，再拿 core 的 TR_RE / PROJECT_TR_RE 判形状——
    判定规则和读侧认译文用的是同一张表，不会一边认一边不认。

    project.md 及其译文在步骤目录里其实碰不到（附件只落在步骤目录下），
    一起挡是因为这个函数迟早会被别处复用，而漏一种形状的代价是整份记录。
    """
    key = fs_key(name)
    return (key in (fs_key(NOTE_NAME), fs_key(PROJECT_NOTE))
            or bool(TR_RE.match(key)) or bool(PROJECT_TR_RE.match(key)))


def resolve_attachment(steps_dir: Path, by_id: dict[str, Step], sid: str, relpath: str) -> Path:
    base = step_dir(steps_dir, by_id, sid).resolve()
    target = (base / safe_relpath(relpath)).resolve()
    if target != base and base not in target.parents:
        raise WriteError("路径越界")
    return target


def attach_file(steps_dir: Path, sid: str, relpath: str, data: bytes) -> dict[str, Any]:
    if len(data) > MAX_FILE_BYTES:
        raise WriteError(f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)} MB 上限；大文件请留在仓库外，正文里记路径 + 校验和 + 大小")
    by_id = load(steps_dir)
    target = resolve_attachment(steps_dir, by_id, sid, relpath)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(target, data)      # 半个文件比没有文件更难发现
    return {"path": safe_relpath(relpath), "size": len(data)}


EXT_BY_MIME = {
    "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp",
    "image/svg+xml": "svg", "image/avif": "avif", "image/bmp": "bmp",
    "text/plain": "txt", "text/csv": "csv", "text/tab-separated-values": "tsv",
    "application/json": "json", "application/pdf": "pdf",
}


def attach_auto(steps_dir: Path, sid: str, data: bytes, filename: str = "", mime: str = "") -> dict[str, Any]:
    """服务端定名的附件上传，供网页粘贴/拖拽使用。

    有文件名就用文件名（`train.log` 比一串哈希好读得多），重名且内容不同才加后缀；
    没有文件名（剪贴板里的位图）就用内容哈希命名——于是同一张图粘贴两次只存一份。
    """
    if len(data) > MAX_FILE_BYTES:
        raise WriteError(f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)} MB 上限；大文件请留在仓库外，正文里记路径 + 校验和 + 大小")
    if not data:
        raise WriteError("空文件")

    by_id = load(steps_dir)
    base_dir = step_dir(steps_dir, by_id, sid)
    digest = hashlib.sha1(data).hexdigest()

    name = safe_relpath(filename).rsplit("/", 1)[-1] if filename.strip() else ""
    if not name:
        ext = EXT_BY_MIME.get((mime or "").split(";")[0].strip().lower(), "bin")
        name = f"{'img' if ext in ('png', 'jpg', 'gif', 'webp', 'svg', 'avif', 'bmp') else 'file'}-{digest[:10]}.{ext}"

    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    candidate, n = name, 2
    while True:
        target = base_dir / candidate
        if not target.exists():
            break
        if hashlib.sha1(target.read_bytes()).hexdigest() == digest:
            return {"path": candidate, "size": len(data), "reused": True}  # 同名同内容，直接复用
        candidate = f"{stem}-{n}{('.' + ext) if ext else ''}"
        n += 1

    write_atomic(target, data)
    return {"path": candidate, "size": len(data), "reused": False}


def delete_file(steps_dir: Path, sid: str, relpath: str) -> None:
    by_id = load(steps_dir)
    target = resolve_attachment(steps_dir, by_id, sid, relpath)
    if not target.is_file():
        raise NotFound(f"附件不存在: {relpath}")
    target.unlink()
