"""trace_write — 唯一写入路径。

CLI、网页表单、agent API 全部调这里，不允许任何一方绕过去直接写文件。
上一代系统的 bug 根源就是存在第二条写入路径（直接 sqlite3 INSERT），
导致父子关系只写进了一半的地方。这里用"只有一个函数会创建 note.md"来杜绝。

只追加原则（P2）在这里强制。要紧的是它到底约束什么：

  **只追加的地基是「不丢历史」，不是「不能改结构」——记下来就不丢。**

  * id 由服务端分配，写下就不再改（笔记里的 `[[003b]]`、论文脚注里的引用要永远
    有效），update_step 里改 id 一律 409；
  * parent **可以改**，但只能走 move_step，而且必须写原因：每次移动都往
    front-matter 追加一条 `moved:` 审计，只追加不覆盖。
    update_step 里改 parent 仍然 409，只是提示改成「请用 move_step 并写原因」。

  为什么给 parent 开这个口子：没有移动能力，逼出来的是更坏的东西。真实用例里
  一整条支挂错了父节点，用户只能把两个节点的**正文对调**才让网站显示正确——
  于是创建日期和内容脱节，而那件事一条审计记录都没留下。能移动 + 必须写原因，
  历史反而更完整。

唯一的例外是 delete_step()：真删目录，用来处理"这条记录本身就不该存在"
（误建、测试数据、粘进去的令牌）。它和 status=dead 是两件事——dead 是研究结论。
代价（id 可能被重用、子步骤变孤儿）在那个函数的说明里写清楚了。

分叉语义（`branch:` / `decision:`）这一版新加，形状和只追加原则不冲突：落盘的**只有**
每个候选自己那一行「我是一个候选」，和分叉点自己那一句「我在决定什么」。
「谁和谁是一组」「选中了哪个」「这个岔路口还没决定」全部是扫出来现算的（core），
写入侧一个判据都不重写，父节点上也绝不存一份孩子清单——存了它，孩子被 move_step
挪走的那一刻就过期，而那正是双真相源最典型的长法。

双语（note.<lang>.md）同样只有一条写入路径：write_translation /
write_project_translation / drop_translation。它们碰不到原文，也写不出结构键——
见文件下半部分「翻译」那一节的长注释。

「开发路径」和「定稿流程」这一版分了家（记录 vs 方法），而落盘的**只有一件推导
不出来的事**：哪一步是成果。它写在 project.md 的 front-matter 里，可重复：

    result: 023 | 主结果：亲和力预测 AUC 0.91

其余全部派生：从每个 result 沿 `input:` 反向做闭包（没写 input: 的退回 parent），
剔掉 dead，剩下的就是定稿流程。**成员清单一个字都不存**——存了它，移动一步、
补一条 input:、把某支标 dead，那份清单当场就过期，而它看起来仍然完全正常。
这正是上一代系统的死法，也是 P1 禁止中心索引的原因。

单步上的例外（`pipeline: include|exclude | 理由`）同样只写在**那一步自己**身上，
和 `branch:` 一个套路：项目上绝不列「哪些步骤要排除」的清单。
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
    BRANCH_KINDS,
    CODE_KINDS,
    DEFAULT_BRANCH,
    NOTE_NAME,
    PATH_ROLES,
    PIPELINE_RULES,
    PROJECT_NOTE,
    STATUSES,
    DEFAULT_STATUS,
    INSIGHT_ID_PREFIX,
    SUPERSEDES_WORD,
    Project,
    Step,
    build_children,
    compute_branch_groups,
    find_insight,
    fmt_id,
    format_branch,
    format_code,
    format_input,
    format_insight,
    format_moved,
    format_path,
    format_pipeline,
    format_result,
    id_key,
    next_insight_id,
    parse_branch,
    parse_code,
    parse_inputs,
    parse_insights,
    parse_moved,
    parse_note,
    parse_paths,
    parse_pipeline,
    parse_results,
    path_kind,
    project_dir,
    projects_root,
    scan,
    scan_projects,
    split_id,
    steps_dir_of,
    validate,
)

# 上面这一串里，`format_* / parse_* / *_insight*` 是这一版新加的**共用**函数：
# path / input / code / moved 四种行和项目洞察的那一行，解析和序列化**都在 core**，
# 写入侧一行都不重写。读侧和写侧共用同一对函数是硬要求——各写一份的话，
# 「写进去的行读不回来」只会在某个属性上悄悄发生，而磁盘上那一行看着完全正常。
# 写入侧在这里只做一件 core 不该管的事：**校验**。core 面对残缺输入要尽量读出
# 东西来（十年后的日志一定是残缺的），写入侧面对不合法输入要当场拒绝。
# SUPERSEDES_WORD 是重导出，MCP / 服务端要拿它拼提示文案。
#
# 分叉语义（`branch:` / `decision:`）走的是同一条规矩：词表 BRANCH_KINDS、
# 这一行的 parse/format、以及**候选组怎么算**（compute_branch_groups）全在 core，
# 写入侧一个判据都不重写。尤其是候选组：写完之后当场告诉人的那句「011 那组现在
# 只剩 012b」和界面上画出来的必须是同一次推导，各写一份的话两边看着都对，
# 只有在某个刚被 move_step 挪走的候选身上才对不上。

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


def _load_project_note(d: Path) -> tuple[dict[str, str], str, list[dict[str, str]]]:
    """读 project.md：front-matter、正文、以及成果声明（`result:`，可重复）。

    成果声明**不能**从 parse_note 给的 meta 里拿：那边只有 MULTI_KEYS 里的键会累积，
    而 `result:` 刻意不在那张表里（那是 note.md 的可重复键表，FORMAT.md 第 2 节逐字
    钉着它）。按 meta 读的话只留得下最后一行，于是第二条成果声明会在下一次改项目名
    的时候被静默删掉——一条 diff 都不像是删除，人只看到自己刚改的名字。
    所以走 core 的 parse_results，它自己扫 front-matter 的原始行。

    非 UTF-8 就拒绝——继续写下去会把原文替换成一串问号。
    """
    note = d / PROJECT_NOTE
    if not note.is_file():
        return {}, "", []
    text = read_text_strict(note, "项目笔记")
    meta, body, _w = parse_note(text)
    return meta, body, parse_results(text)


def _read_project_note(d: Path) -> tuple[dict[str, str], str]:
    """读 project.md 的 front-matter 和正文。成果声明由 _write_project_note 自己保留。"""
    meta, body, _results = _load_project_note(d)
    return meta, body


def _write_project_note(d: Path, name: str, body: str, lang: str = "",
                        results: list[dict[str, str]] | None = None) -> None:
    """回写 project.md。键顺序固定（name → lang → result…），保证逐字节确定（P3）。

    `lang` 要**原样带回去**：它是这份笔记自己声明的主语言，翻译文件靠它判断
    「哪个语言不需要翻译」。这个函数是全量重写 front-matter 的，不显式带上就等于
    每次改一条洞察都把用户手写的 `lang:` 悄悄删掉一次。

    `results` 默认是 None，意思是**从磁盘上原样读回来**，不是「一条都没有」。
    默认值选「保留」而不是「清空」，是因为这个函数有五个调用方（改名、改洞察、
    整体替换、记删除、标注删除未完成），它们没有一个关心成果声明——默认清空的话，
    漏掉一处的表现是「改了个项目名，论文的定稿流程整个没了」，而且不报错。
    lang 当年就是这么漏的（见上一段），这一次让「忘记传」等于「保留」。
    """
    body = body.strip()
    if results is None:
        _m, _b, results = _load_project_note(d)
    head = f"---\nname: {name}\n" + (f"lang: {lang}\n" if lang else "")
    head += "".join(f"result: {format_result(r)}\n" for r in results)
    head += "---\n\n"
    write_atomic(d / PROJECT_NOTE, _utf8_bytes(head + (f"{body}\n" if body else ""), "项目笔记"))


# ---------------------------------------------------------------- 洞察的 id
#
# 一条洞察要能被**重新锚定**（证据从 013 搬到了 013b，那句「见 [[013]]」就得跟着改）
# 和**被取代**（「PDBFixer 误杀 1,099 个」后来查清是 944 个）。两件事都需要能指着
# 某一条说「就是它」，所以每条洞察带一个 id，写在行首的反引号里：
#
#     - `p3` PDBFixer 误杀 944 个带修饰残基，见 [[013b]] · 取代 p1
#     - `p1` PDBFixer 误杀 1,099 个
#
# 三个刻意的选择：
#   * 写法和 `## 已删除` 里的 `` `002` `` 一致 —— 人扫一眼就知道那是个标识符；
#   * 前缀 p 让它和步骤 id（纯数字开头）永远不会撞，`grep -n '`p1`'` 也就只捞洞察；
#   * **「p1 已被取代」是派生的**，只写在取代者那一行上，绝不在 p1 那行上再写一份
#     状态位。被取代的那条**永远不删**：它是当时的判断，删掉就没人知道数字为什么变了。
# 解析（谁是 id、谁取代了谁）和渲染（那一行长什么样）都在 core，这里只管
# **分配、校验、落盘**。读写各写一份解析，迟早会出现「写得进去、读不回来」的洞察。


def alloc_insight_id(*bodies: str) -> str:
    """下一个洞察 id。和 alloc_id 同一个思路：扫一遍现有的，取最大数字 + 1。

    要**跨全部语言版本**一起扫（原文 + 译文）：同一条洞察在 project.md 和
    project.en.md 里是同一个 id，只看一份就会撞号，而撞号之后「· 取代 p2」
    同时指向两条不同的记录，读的人无从分辨。

    取 max+1 而不是「第一个空位」：被取代的洞察不删，但整组替换那条路
    （`insights=…`）仍然可能让某个 id 从文件里消失，复用它会让别处那句
    「· 取代 p2」指到一条完全不相干的记录上。
    """
    top = 0
    pat = re.compile(re.escape(INSIGHT_ID_PREFIX) + r"(\d+)$")
    for b in bodies:
        m = pat.match(next_insight_id(b, INSIGHT_ID_PREFIX))
        if m:
            top = max(top, int(m.group(1)))
    return f"{INSIGHT_ID_PREFIX}{top}"


def _insight_heading(kind: str, lang: str) -> str:
    names = INSIGHT_NAMES.get(kind)
    if names is None:
        raise WriteError(f"洞察类型必须是 {'/'.join(INSIGHT_SECTIONS)} 之一，收到 {kind!r}")
    heading = names.get(lang or "zh")
    if not heading:
        raise WriteError(
            f"语言 {lang!r} 还没有洞察小节的封闭词表（目前只有 zh / en）。"
            "要让这个语言参与，先把它加进 trace_core.INSIGHT_NAMES。")
    return heading


def _norm_supersedes(raw: Any, iid: str, corpus: list[str]) -> list[str]:
    """校验「取代了谁」。指向不存在的 id 直接拒绝——那一行是要被折叠显示的。"""
    if isinstance(raw, str) or raw is None:
        ids = [x for x in re.split(r"[,，\s]+", str(raw or "")) if x]
    else:
        ids = [_clean_line(x) for x in raw if _clean_line(x)]
    if not ids:
        return []
    known = {it["id"] for b in corpus for items in parse_insights(b).values()
             for it in items if it["id"]}
    for x in ids:
        if x == iid:
            raise WriteError(f"{x} 不能取代它自己。")
        if x not in known:
            raise WriteError(
                f"找不到要被取代的洞察 {x!r}。「· 取代 {x}」是给读的人看的指路牌，"
                "指向一条不存在的记录比不写更糟。先确认那条洞察的 id（行首反引号里那个）。")
    return ids


def _clean_insight_text(text: Any) -> str:
    """洞察是**一行**——它要能被 grep 一行捞出来，所以换行一律压成空格。"""
    out = re.sub(r"\s*\n\s*", " ", str(text or "")).strip()
    if not out:
        raise WriteError("洞察内容不能为空")
    return out


def _insight_add(body: str, corpus: list[str], kind: str, text: Any,
                 supersedes: Any, lang: str) -> tuple[str, dict[str, Any]]:
    iid = alloc_insight_id(*corpus)
    line = "- " + format_insight(_clean_insight_text(text), iid,
                                 _norm_supersedes(supersedes, iid, corpus), lang)
    return _append_under(body, _insight_heading(kind, lang), line), {"id": iid, "line": line}


def _insight_edit(body: str, iid: str, text: Any, supersedes: Any,
                  lang: str, corpus: list[str]) -> tuple[str, dict[str, Any]]:
    """就地改写一条既有洞察。**只改这一行**，不换小节、不删别的行。

    重新锚定走的就是这条路：把文本里的 `[[013]]` 改成 `[[013b]]`，id 不变，
    于是别处那句「· 取代 p1」还指得对。
    """
    iid = _clean_line(iid)
    hit = find_insight(body, iid)
    if hit is None:
        raise NotFound(
            f"这个项目的洞察里没有 {iid!r}。id 写在行首的反引号里（`- \\`p1\\` …`），"
            "只有走工具写进去的洞察才有——手写的老条目没有 id，改它请用整体替换那条路。")
    new_text = hit["text"] if text is None else _clean_insight_text(text)
    # supersedes 不给就**保留原样**：改一句措辞不该顺手把「它取代了谁」抹掉。
    new_sup = (hit["supersedes"] if supersedes is None
               else _norm_supersedes(supersedes, iid, corpus))
    lines = (body or "").split("\n")
    # 只换这一行的内容，缩进和 `- ` 之外的东西一概不动。
    indent = re.match(r"^(\s*[-*]\s+)", lines[hit["line"]])
    lines[hit["line"]] = (indent.group(1) if indent else "- ") + format_insight(
        new_text, iid, new_sup, lang)
    return "\n".join(lines), {"id": iid, "line": lines[hit["line"]], "kind": hit["kind"]}


def _project_translation_body(d: Path, lang: str) -> tuple[dict[str, str], str, Path]:
    target = d / _translation_name("project", lang)
    if not target.is_file():
        return {}, "", target
    meta, body, _w = parse_note(read_text_strict(target, f"{lang} 项目译文"))
    return meta, body, target


def _edit_insights(root: Path, slug: str, lang: str, fn) -> dict[str, Any]:
    """洞察类改动的公共外壳：锁、读、语言路由、回写。

    `fn(body, corpus, vocab) -> (新正文, info)`；corpus 是**分配和校验 id 时要看的
    全部正文**，vocab 是小节名和「取代」该用哪套词表的语言。
    译文那一侧要连原文一起看：同一条洞察在 project.md 和 project.en.md 里是同一个 id，
    只在译文里分配就会和原文撞号，而撞号之后「· 取代 p2」就指向两条不同的记录。

    vocab 在写原文时取 `project.md` 自己声明的 `lang:`（一份 `lang: en` 的项目笔记
    里的小节叫 `## Pitfalls`，往里追加中文标题会凭空多出一个空小节），
    写译文时就是译文自己的语言。
    """
    d = project_dir(root, slug)
    if not d.is_dir():
        raise NotFound(f"项目 {slug} 不存在")
    with project_lock(d):
        meta, base = _read_project_note(d)
        if not lang:
            body, info = fn(base, [base], (meta.get("lang") or "").strip() or "zh")
            _write_project_note(d, (meta.get("name") or slug).strip(), body,
                                (meta.get("lang") or "").strip())
            path = PROJECT_NOTE
        else:
            lang = norm_lang(lang)
            _guard_same_lang(_declared_lang(meta), lang, f"项目 {slug}")
            tmeta, tbody, target = _project_translation_body(d, lang)
            body, info = fn(tbody, [base, tbody], lang)
            write_atomic(target, _utf8_bytes(_render_translation(
                PROJECT_TR_ONLY_KEYS[0], tmeta.get(PROJECT_TR_ONLY_KEYS[0], ""), body),
                f"{lang} 项目译文"))
            path = target.name
    return {"slug": slug, "lang": lang, "path": path, **info}


def add_insight(root: Path, slug: str, kind: str, text: str, *,
                supersedes: str = "", lang: str = "") -> dict[str, Any]:
    """追加一条洞察并**分配一个 id**，返回 {"id", "line", …}。

    调用方拿得到 id 这件事是必须的：不知道刚写的那条叫什么，就没法在下一次
    「查清楚了，其实是 944 个」时说出「· 取代 p1」。
    """
    return _edit_insights(root, slug, lang,
                          lambda body, corpus, vocab: _insight_add(body, corpus, kind, text,
                                                                   supersedes, vocab))


def update_insight(root: Path, slug: str, insight_id: str, *, text: str | None = None,
                   supersedes: str | None = None, lang: str = "") -> dict[str, Any]:
    """改写一条既有洞察（重新锚定、更正措辞、补上「取代了谁」）。**不删任何东西。**"""
    return _edit_insights(root, slug, lang,
                          lambda body, corpus, vocab: _insight_edit(body, insight_id, text,
                                                                    supersedes, vocab, corpus))


def update_project(root: Path, slug: str, *, name: str | None = None,
                   insights: str | None = None,
                   add: tuple[str, str] | dict[str, Any] | None = None,
                   insight_id: str = "", supersedes: str = "",
                   lang: str = "") -> Project:
    """改项目的显示名和/或洞察正文。

    `add=(kind, text)`（也接受 `{"kind","text","id","supersedes"}`）往对应小节追加
    一条并分配 id；带 `insight_id=` 时改的是那一条既有洞察，不新增。
    `insights=…` 整体替换，但**只替换那四个洞察小节**（见 _merge_insights）。
    `lang=` 让洞察落在 `project.<lang>.md` 上（显示名和整体替换请走翻译那条路）。
    **目录名（= URL 里的 slug）永远不动**——改了会让所有已发出的链接失效。

    分配到的 id 挂在返回值的 `.insight_id` 上——Project 是 core 的数据类，
    不该为了带回一个写入侧的返回值就往它身上加字段（那会进 to_dict、进 API 契约、
    进静态导出）。要结构化的返回值请直接用 add_insight / update_insight。
    """
    d = project_dir(root, slug)
    if not d.is_dir():
        raise NotFound(f"项目 {slug} 不存在")

    if isinstance(add, dict):
        add_kind = add.get("kind")
        add_text = add.get("text", "")
        insight_id = _clean_line(add.get("id") or insight_id)
        supersedes = add.get("supersedes") or supersedes
    elif add is not None:
        add_kind, add_text = add
    else:
        add_kind = add_text = None

    if lang and (name is not None or insights is not None):
        raise WriteError(
            "lang 只作用于洞察那一条。改译文的项目名或整段洞察请用 write_project_translation"
            "——两条路各自有自己的 expect，混在一起就没法做冲突检测了。")

    info: dict[str, Any] = {}
    if lang:
        # 译文那一侧自己有锁和回写，走同一套 _edit_insights。
        if insight_id:
            info = update_insight(root, slug, insight_id, text=add_text,
                                  supersedes=supersedes or None, lang=lang)
        elif add_kind is not None:
            info = add_insight(root, slug, add_kind, add_text,
                               supersedes=supersedes, lang=lang)
        meta, body = _read_project_note(d)
        p = Project(slug=slug, name=(meta.get("name") or slug).strip(), body=body.strip())
        p.insight_id = info.get("id", "")       # type: ignore[attr-defined]
        return p

    with project_lock(d):
        meta, body = _read_project_note(d)

        final_name = _clean_line(name) if name is not None else (meta.get("name") or slug).strip()
        if not final_name:
            raise WriteError("项目名不能为空")

        if insights is not None:
            body = _merge_insights(
                body, str(insights).replace("\r\n", "\n").replace("\r", "\n"))
        if insight_id:
            body, info = _insight_edit(body, insight_id, add_text, supersedes or None,
                                       (meta.get("lang") or "zh").strip() or "zh", [body])
        elif add_kind is not None:
            body, info = _insight_add(body, [body], add_kind, add_text, supersedes,
                                      (meta.get("lang") or "zh").strip() or "zh")

        _write_project_note(d, final_name, body, (meta.get("lang") or "").strip())
    p = Project(slug=slug, name=final_name, body=body.strip())
    p.insight_id = info.get("id", "")           # type: ignore[attr-defined]
    return p


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


# ---------------------------------------------------------------- 成果声明
#
# 「开发路径」是整棵树（含走不通的、含岔路口），「定稿流程」是真正产出成果的那条
# 链。两者之间**只有一件事推导不出来**：哪一步是成果。所以磁盘上也只多这一行：
#
#     result: 023 | 主结果：亲和力预测 AUC 0.91
#     result: 031 | 图 4 的消融
#
# 流程的成员清单绝不落盘（P1）。它是从每个 result 沿 `input:` 反向做闭包算出来的，
# 于是移动一步、补一条 `input:`、把某支标 dead，流程自己就跟着变了。存一份清单的
# 代价不是「多一个文件」，是那份清单**看起来永远是对的**——它只在你改完结构之后
# 才开始说谎，而那正是你最信它的时候。
#
# 为什么单开一个写入口，而不是给 update_project 加一个参数（三条，各对应一种
# 「加个参数」拦不住的坏结果）：
#
#   1. **它决定的东西比它看起来重得多。**「把 023 定为主结果」重写的是整条定稿
#      流程、论文 Methods 里出现哪几步、导出的那张图长什么样。和「改个标题」走
#      同一个随手的口子，语义上就把它降级成了一个字段。
#   2. **它要校验的东西 update_project 够不着。**成果必须指向一个**真实存在**的
#      步骤：`input:` 指向不存在的 id 是宽容的（读侧报一条警告，那条数据边不参与
#      计算），而悬空的 `result:` 会让整条定稿流程**静默变空**——闭包从一个不存在
#      的起点出发，什么都够不着，页面上不报错，只是那一栏空了。所以这里要 load 一遍
#      步骤，而 update_project 只拿得到项目目录。
#   3. **参数组合会爆炸。**update_project 已经有 name / insights / add / insight_id /
#      supersedes / lang 六个参数和一组互斥规则。再塞三个，最可能的失败是「传了但
#      被忽略」——写入侧最难查的一类 bug。
#
# 明确**不**要的：一句 reason。move_step 的 reason 存在是因为移动**销毁**了一个
# 事实（原来挂在哪），那条审计是它仅存的证据；而声明成果什么都不销毁，它不是一段
# 历史，是一个**当前指针**（「论文现在报的是这一步」）。给指针强制配一句理由，
# 收上来的会是「因为这是主结果」这种仪式性文字，而仪式性文字会训练人连 move_step
# 那句真正要紧的理由一起敷衍过去。更实在的是：「为什么这一步是成果」已经写在那一步
# 自己的 `## 结论` 里了，再写一份就是第二份真相。


def _result_row(sid: str, note: str) -> dict[str, str]:
    """校验并规整一条成果声明，然后**按它将来在磁盘上的样子读回来对一遍**。

    和 _path_row 同一个做法：拼出那一行 → 交给读侧的解析器 → 比对。多花一次解析，
    换「写进去的和读出来的一模一样」。这条边角很窄（id 的形状已经查过、说明里的
    竖线本来就该原样留着），但读写两侧一旦哪天分家，症状是**成果声明写得进去、
    读不回来**，而磁盘上那一行看着完全正常，页面只是安静地少一条流程。
    """
    sid = _clean_line(sid)
    if not sid:
        raise WriteError("要把哪一步定为成果？step 不能为空。")
    if not _STEP_ID_RE.match(sid):
        raise WriteError(
            f"成果指向的步骤 id 形状不对: {sid!r}（应该是 023 / 023b 这样的三位数字加可选字母）。")
    row = {"step": sid, "note": _clean_line(note)}
    line = f"result: {format_result(row)}"
    if parse_results(f"---\n{line}\n---\n") != [row]:
        raise WriteError(f"这条成果声明读回来和写进去的不一样: {line!r}。")
    return row


def set_result(root: Path, slug: str, step: str, note: str = "") -> dict[str, Any]:
    """声明「这一步是成果」，或就地改写它那一行的说明。定稿流程从这里长出来。

    同一个步骤只会有一行：成果声明的身份**就是**那个步骤 id，同一步写两条只会
    产出两个一模一样的闭包，而说明不同的那两句话没人知道该信哪句。所以再写一次
    就是改写那一行，位置不动（顺序是人声明的顺序，也是导出里的顺序）。

    两条硬校验，各挡一种静默失败：

      * **步骤必须存在。**悬空的 result 不像悬空的 input（读侧警告一声、那条边
        不参与计算），它让整条定稿流程从一个不存在的起点出发，结果是空——页面上
        什么都不报，只是那一栏没了。
      * **不能把 dead 的一步定为成果。**「我的主结果是一条我自己判了此路不通的
        线」不是任何人想打出来的话。注意反过来是**允许**的：已经声明成成果的一步
        后来被标 dead 不拦（P4：结论不是错误，而且那多半是真发生的事），它由读侧
        以 warn 级说出来并点名。写入侧挡住新犯的错，读侧说清已经发生的事——
        和 branch 的笔误校验是同一条分工。
    """
    steps_dir = resolve_project(root, slug)
    d = steps_dir.parent
    row = _result_row(step, note)
    with project_lock(d):
        by_id = load(steps_dir)
        target = by_id.get(row["step"])
        if target is None:
            raise NotFound(
                f"步骤 {row['step']} 在这个项目里不存在。成果声明不接受悬空引用："
                "定稿流程是从它反向做闭包算出来的，起点不存在的话整条流程会静默变空，"
                "而页面上一个字都不会报。")
        if target.status == "dead":
            raise WriteError(
                f"{row['step']} 是 dead —— 那是一句结论（此路不通），不该同时是成果。"
                "如果这条路其实走通了，先把它的 status 改掉；如果成果在别的步骤上，"
                "换那一步。（已经声明过的成果后来被标 dead 是允许的，那是真会发生的事，"
                "由读侧点名报出来。）")
        meta, body, results = _load_project_note(d)
        idx = next((i for i, r in enumerate(results) if r["step"] == row["step"]), None)
        created = idx is None
        if created:
            results = results + [row]
        else:
            results = [row if i == idx else r for i, r in enumerate(results)]
        _write_project_note(d, (meta.get("name") or slug).strip(), body,
                            (meta.get("lang") or "").strip(), results)
    return {"slug": slug, "step": row["step"], "note": row["note"], "created": created,
            "line": f"result: {format_result(row)}", "results": results}


def drop_result(root: Path, slug: str, step: str) -> dict[str, Any]:
    """撤掉一条成果声明。

    这不是 P2 的例外：`result:` 不是一段历史，是一个当前指针（「论文现在报的是
    哪一步」）。指针改了就是改了，被撤掉的那一步和它的记录一个字都没动，
    它只是不再是定稿流程的终点——开发路径上它还在原处。
    """
    sid = _clean_line(step)
    d = project_dir(root, slug)
    if not d.is_dir():
        raise NotFound(f"项目 {slug} 不存在")
    with project_lock(d):
        meta, body, results = _load_project_note(d)
        left = [r for r in results if r["step"] != sid]
        if len(left) == len(results):
            raise NotFound(
                f"项目 {slug} 没有把 {sid or '（空）'} 声明成成果。"
                f"现在声明的是: {'、'.join(r['step'] for r in results) or '（一个都没有）'}。")
        _write_project_note(d, (meta.get("name") or slug).strip(), body,
                            (meta.get("lang") or "").strip(), left)
    return {"slug": slug, "step": sid, "removed": True, "results": left}


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


# ------------------------------------------------- 结构化 front-matter 行
#
# path / input / code / moved 四种行的共同形状：一行一条、可重复、竖线分段、
# 人能直接读、`grep -rn "^path:"` 捞得出来（G4）。解析和序列化在 core（见文件顶部
# 那段说明），这里只有写入侧的校验和「怎么排序才确定」。
_ATTR_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STEP_ID_RE = re.compile(r"^\d+[a-z]*$")
# parent 里表示「没有父节点」的几种写法，和 core.build_step 那一份一致。
_ROOT_WORDS = ("", "-", "none", "null")
# 属性的输出顺序。P3 要求同样的输入两次构建逐字节一致，而 core.format_path 是按
# dict 的迭代顺序拼的——于是「顺序固定」这件事落在构造这个 dict 的人身上，也就是
# 这里。表里没有的（半年后有人写的 `nodes=…`）排在后面，按键名字典序。
PATH_ATTR_ORDER = ("n", "size", "md5", "sha256", "checked", "missing")


def _ordered_attrs(attrs: dict[str, Any], what: str) -> dict[str, str]:
    """校验属性并按固定顺序重排。值里有空白或竖线一律拒绝：那会把一行劈开。"""
    clean = {str(k): _clean_line(v) for k, v in (attrs or {}).items()}
    for k, v in clean.items():
        if not _ATTR_KEY_RE.match(k):
            raise WriteError(f"{what}的属性名 {k!r} 不合法：只允许字母开头、字母/数字/下划线/点/连字符。")
        if not v or re.search(r"\s|\|", v):
            raise WriteError(
                f"{what}的属性 {k}={v!r} 里有空白或竖线。这一行是竖线分段的，"
                "带空白的值读回来会变成说明文字，带竖线的会把这一行劈成两半。")
    known = [k for k in PATH_ATTR_ORDER if k in clean]
    return {k: clean[k] for k in known + sorted(k for k in clean if k not in PATH_ATTR_ORDER)}


def _branch_of(step: Step) -> dict[str, str]:
    """从 Step 上取 core 那对 parse/format 用的 {kind, note}。

    getattr 兜底的理由和 lang 那一条一样：纯手工造出来的 Step（测试、演算）
    可能连这两个字段都没有。kind 空 = 没写 = 默认的 extends。
    """
    return {"kind": _clean_line(getattr(step, "branch", "")).lower() or DEFAULT_BRANCH,
            "note": _clean_line(getattr(step, "branch_note", ""))}


def _pipeline_of(step: Step) -> dict[str, str]:
    """这一步对定稿流程的例外声明，取成 core 那对 parse/format 用的 {rule, note}。

    没声明就是空 rule —— 进不进流程由闭包说了算，那是绝大多数步骤的状态。
    getattr 兜底的理由和 branch 那一条一样：纯手工造出来的 Step（测试、演算）
    可能连这两个字段都没有。
    """
    return {"rule": _clean_line(getattr(step, "pipeline", "")).lower(),
            "note": _clean_line(getattr(step, "pipeline_note", ""))}


def render_note(step: Step) -> str:
    """序列化成 note.md。键顺序固定，保证同样的数据永远产出同样的字节。"""
    lines = ["---", f"id: {step.id}"]
    if step.parent:
        lines.append(f"parent: {step.parent}")
    # branch 限定的是**上面那条 parent 边**，所以紧贴着 parent 写：
    # 「parent: 011 / branch: alternative」连着读才是一句完整的话
    # （「我挂在 011 下面，而且是 011 那个岔路口的一个候选」）。
    #
    # 默认值 extends **不落盘**。两条理由：
    #   * 「没写 branch」和「写了 branch: extends」是同一个意思，存后者等于把一个
    #     推导得出来的默认值写成数据；
    #   * 已有的 164 条记录会在下一次编辑时集体多出一行 diff，而那一行什么都没说。
    # 唯一的例外是带了说明的：说明是人写的字，丢了就没了（`branch: extends |
    # 这条是主线，A/B 都试过之后接着走的` 是一句有信息的话）。
    b = _branch_of(step)
    if b["kind"] != DEFAULT_BRANCH or b["note"]:
        lines.append("branch: " + format_branch(b))
    lines.append(f"status: {step.status}")
    lines.append(f"title: {step.title}")
    # decision 说的是这个节点**在决定什么**（「类别不平衡怎么处理？只能选一条走
    # 下去」），限定的是它下面那一组候选。它和 title 一样是人写的自由文本——
    # 「一组候选在决定什么」这句话推导不出来，只能人写——所以挨着 title 放。
    # 注意父节点上**只有这句话**，绝不写孩子清单：那是双真相源，而且孩子一移动
    # 就过期。「谁和谁是一组候选」永远从孩子自己的 branch: 现算。
    decision = _clean_line(getattr(step, "decision", ""))
    if decision:
        lines.append(f"decision: {decision}")
    # pipeline 挨着 decision 放：两行都是**人对结构的判断**（decision 说这个岔路口
    # 在决定什么，pipeline 说这一步进不进最终流程），都推翻不了也推导不出来，
    # 而下面从 lang 开始是机器记录区。没声明就不写这一行——「没写」和
    # 「由闭包说了算」是同一个意思，把默认值存进文件就是把派生写成数据。
    pl = _pipeline_of(step)
    if pl["rule"]:
        lines.append("pipeline: " + format_pipeline(pl))
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
    # getattr 兜底的理由和上面 lang 那一条一样：纯手工造出来的 Step（测试、
    # 演算）可能连字段都没有，而漏掉一个键就等于每改一次状态删掉一次数据。
    for m in getattr(step, "moved", None) or []:
        lines.append("moved: " + format_moved(m))
    for i in getattr(step, "inputs", None) or []:
        lines.append("input: " + format_input(i))
    for p in step.paths:
        lines.append("path: " + format_path(p))
    # `commit:` 上面已经写过了，这里**不再多写一条 `code: git`**：那是同一个事实的
    # 第二份拷贝，正是上一代系统的死因。「commit 等价于一条 code: git」由读侧
    # （core.code_records）现算，所以派生出来的那条（from == "commit"）要跳过。
    for c in getattr(step, "code", None) or []:
        if str(c.get("from", "code")) != "commit":
            lines.append("code: " + format_code(c))
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


def norm_paths(raw: Any) -> list[dict[str, Any]]:
    """把外部传来的路径规整成 [{location, note, role, attrs, kind}]。

    接受三种写法，agent 和人怎么写都能接住：
      * `"位置 | 说明"` 或结构化的一整行 `"位置 | output | 说明 | n=4 size=12"`；
      * `{"location"/"loc", "note"/"desc", "role", "size", "n", "md5", "checked", …}`；
      * 单条不套列表。
    kind 是派生的，从位置形状猜出来，不存也不接受外部传入。
    """
    if not raw:
        return []
    items = [raw] if isinstance(raw, (str, dict)) else list(raw)
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            loc = _clean_line(item.get("location") if item.get("location") is not None
                              else item.get("loc"))
            note = _clean_line(item.get("note") if item.get("note") is not None
                               else item.get("desc"))
            role = _clean_line(item.get("role")).lower()
            attrs = {_clean_line(k): _clean_line(v)
                     for k, v in dict(item.get("attrs") or {}).items()}
            # 已知属性也接受平铺在顶层——`{"loc": …, "size": 620756992}` 是调用方
            # 最自然的写法。未知的顶层键**不**当属性（那样一个拼错的字段会被静默
            # 当成机器字段写进去），要写没见过的属性请显式放进 attrs。
            for k in PATH_ATTR_ORDER:
                if item.get(k) not in (None, ""):
                    attrs[k] = _clean_line(item[k])
        else:
            parsed = parse_paths(str(item).replace("\n", " "))
            if not parsed:
                continue
            loc, note = _clean_line(parsed[0]["location"]), _clean_line(parsed[0]["note"])
            role, attrs = parsed[0]["role"], dict(parsed[0]["attrs"])
        if not loc:
            continue
        if role and role not in PATH_ROLES:
            raise WriteError(f"path 的 role 必须是 {'/'.join(PATH_ROLES)} 之一，收到 {role!r}")
        out.append(_path_row(loc, note, role, attrs))
    return out


def _path_row(loc: str, note: str, role: str, attrs: dict[str, Any]) -> dict[str, Any]:
    """一条结构化 path。**派生字段一律现算**，不接受调用方传进来。

    kind / size / n / checksum / state 都是从 location 和 attrs 推出来的，
    存两份就会有一天对不上（`size=12` 而 size 字段写着 99）。所以这里的做法是：
    校验属性 → 排好序 → 交给 core.parse_paths 重新读一遍，读出来的就是权威形状。
    多花一次解析，换「写侧和读侧看到的一模一样」。
    """
    # 已知属性的值域先查：`size=12 GB` 同时犯了两个错（不是整数、带空白），
    # 而「要是整数、格式化成 592 MB 是显示层的事」才是调用方需要的那句话。
    attrs = {str(k): _clean_line(v) for k, v in (attrs or {}).items()}
    for k in ("size", "n"):
        if k in attrs and not re.fullmatch(r"\d+", attrs[k]):
            raise WriteError(
                f"path 的 {k} 要是非负整数（{'size 是字节数' if k == 'size' else 'n 是条目数'}），"
                f"收到 {attrs[k]!r}。格式化成「592 MB」是显示层的事，存进文件的永远是数。")
    for k in ("checked", "missing"):
        if k in attrs and not _DATE_RE.match(attrs[k]):
            raise WriteError(f"path 的 {k} 要写成 YYYY-MM-DD，收到 {attrs[k]!r}")
    attrs = _ordered_attrs(attrs, "路径")
    row = format_path({"location": loc, "note": note, "role": role, "attrs": attrs})
    got = parse_paths(row)
    if not got or got[0]["location"] != loc or got[0]["note"] != note:
        # 读不回原样只可能是位置或说明里混进了竖线/角色词，那会静默改变语义。
        raise WriteError(
            f"这条路径读回来和写进去的不一样: {row!r}。位置和说明里不要出现竖线，"
            f"说明也不要单独写成 {'/'.join(PATH_ROLES)} 里的一个词——那会被当成 role。")
    return got[0]


def norm_inputs(raw: Any) -> list[dict[str, str]]:
    """数据依赖规整成 [{step, note}]。

    **不检查目标步骤存不存在**，这是想清楚之后的选择：

      * 建立顺序不定。agent 常常先建 016（今天跑的这一步）再回头补 013b，
        要求先有后引会逼着调用方排序，或者干脆不写 input——那就什么都没记下。
      * 和 parent 的既有处理一致。指向不存在的 id 的 parent 在读侧降级为根 + 一条
        `dangling_parent` 警告，从不拒绝写入；「残缺输入产出部分结果」是这套系统的
        明文约定，输入依赖没有理由比父子关系更严。
      * 删掉一步之后引用变悬空是**已经明确接受的代价**（FORMAT.md 第 8 节）。
        写入侧拦不住它，只能让读侧说出来。

    但 id 的**形状**必须查：`input: 十三` 不是悬空引用，是笔误，而且它永远不可能
    指向任何东西。悬空的留给读侧报警告（见报告里的接缝清单）。
    """
    if not raw:
        return []
    items = [raw] if isinstance(raw, (str, dict)) else list(raw)
    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            sid = _clean_line(item.get("step") if item.get("step") is not None else item.get("id"))
            note = _clean_line(item.get("note"))
        else:
            got = parse_inputs(str(item).replace("\n", " "))
            if not got:
                continue
            sid, note = _clean_line(got[0]["step"]), _clean_line(got[0]["note"])
        if not sid:
            continue
        if not _STEP_ID_RE.match(sid):
            raise WriteError(
                f"input 指向的步骤 id 形状不对: {sid!r}（应该是 013 / 013b 这样的三位数字加可选字母）。")
        out.append({"step": sid, "note": note})
    return out


def norm_code(raw: Any) -> list[dict[str, Any]]:
    """代码位置规整成 [{kind, location, note, attrs}]。

    kind 是封闭词表 git / snapshot / container。`commit:` 不走这里、也不会被翻译成
    一条 `code: git` 落盘——同一个事实存两处正是这套系统要杜绝的东西，所以
    读侧派生出来的那条（`from == "commit"`）在这里被直接丢掉：调用方把
    `GET .../steps/{id}` 拿到的 code 原样 PATCH 回来是最自然的写法，不能因此多出一行。
    """
    if not raw:
        return []
    items = [raw] if isinstance(raw, (str, dict)) else list(raw)
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            if str(item.get("from", "")) == "commit":
                continue
            kind = _clean_line(item.get("kind")).lower()
            loc = _clean_line(item.get("location") if item.get("location") is not None
                              else item.get("loc"))
            note = _clean_line(item.get("note") if item.get("note") is not None
                               else item.get("desc"))
            attrs = dict(item.get("attrs") or {})
        else:
            got = parse_code(str(item).replace("\n", " "))
            if not got:
                continue
            kind, loc = got[0]["kind"], _clean_line(got[0]["location"])
            note, attrs = _clean_line(got[0]["note"]), dict(got[0]["attrs"])
        if not kind and not loc:
            continue
        if kind not in CODE_KINDS:
            raise WriteError(
                f"code 的类型必须是 {'/'.join(CODE_KINDS)} 之一，收到 {kind!r}。"
                "代码在 git 里就写 git，是个快照目录就写 snapshot，是镜像就写 container。"
                "（读侧对没见过的 kind 是宽容的——十年后多出一种形态很正常——"
                "但写入侧当场就能问清楚，没有理由让一个笔误落盘。）")
        if not loc:
            raise WriteError("code 要写清位置：仓库地址、快照目录、镜像名——不然它答不了「代码在哪」。")
        out.append({"kind": kind, "location": loc, "note": note,
                    "attrs": _ordered_attrs(attrs, "代码位置"), "from": "code"})
    return out


def norm_branch(raw: Any) -> dict[str, str]:
    """把外部传来的 branch 规整成 core 那对 parse/format 用的 {kind, note}。

    接受 `"alternative"`、`"alternative | 只调采样，不动损失函数"`、
    `{"kind": …, "note": …}`；`None` / 空串 / `"extends"` 都归到默认值，
    也就是**取消候选身份**——标错了要能改回来，否则一次手滑就永久留在那儿了。

    校验放在写入侧而不是读侧：读侧面对十年后的残缺日志要尽量读出东西来（core 对
    未知 kind 是报一声 `bad_branch` 再退回 extends 继续建树），写入侧面对一个笔误
    当场就该问清楚。`branch: alterative` 一旦落盘，它既不算候选也不会在页面上留下
    任何痕迹，只是安静地退回一条普通延伸——而人以为自己标过了。
    """
    if isinstance(raw, dict):
        kind = _clean_line(raw.get("kind")).lower()
        note = _clean_line(raw.get("note") if raw.get("note") is not None else raw.get("desc"))
    else:
        got = parse_branch(_clean_line(raw))
        kind, note = got["kind"], got["note"]
    # 只写了说明没写类别，是「这条普通延伸想顺便说句话」，不是笔误。
    kind = kind or DEFAULT_BRANCH
    if kind not in BRANCH_KINDS:
        raise WriteError(
            f"branch 只能是 {'/'.join(BRANCH_KINDS)} 之一，收到 {kind!r}。"
            "alternative 的意思是「这是同一个问题的一个候选答案，一组里只能选一条"
            "走下去」，其余候选最后标 dead；接着往下做就是 extends（默认值，不用写）。")
    return {"kind": kind, "note": note}


def norm_pipeline(raw: Any) -> dict[str, str]:
    """把外部传来的 pipeline 声明规整成 core 那对 parse/format 用的 {rule, note}。

    这一行是**对派生结果的手工推翻**：定稿流程本来是从 `result:` 沿 `input:` 反向
    做闭包算出来的，`pipeline: exclude` 说「闭包够得到它，但它不该在流程里」，
    `pipeline: include` 说「闭包够不到它，但它确实是流程的一环」。所以：

      * **写空 = 撤销声明**（回到「由闭包说了算」），不是非法值——标错了要能改回来，
        否则一次手滑就永久留在那儿。撤销时说明一起丢掉：一行没有取值的 pipeline
        什么都没说，读侧看不见它，人却以为自己声明过了。
      * **只写说明不写取值直接拒绝**，而不是悄悄当成撤销。那句说明是人写的字，
        默默吃掉它比报错糟得多。
      * **必须写理由。**这是它和 `branch:` 的唯一分歧（那边说明是可选的）：
        候选组在树上看得见，一个 `branch: alternative` 写没写理由，图上都摆着；
        而 pipeline 除了改变一份导出之外**不留任何痕迹**。没有那半句话，半年后
        没人分辨得出这是想清楚的决定还是一次误点，而受害的是论文的 Methods。
        它和 move_step 的 reason 是同一类：推翻推导的动作，必须留下推翻的理由。
    """
    if isinstance(raw, dict):
        # `kind` 也接住：branch 那一侧用的是这个词，调用方（和写代码的人）会串。
        rule = _clean_line(raw.get("rule") if raw.get("rule") is not None
                           else raw.get("kind")).lower()
        note = _clean_line(raw.get("note") if raw.get("note") is not None else raw.get("desc"))
    else:
        got = parse_pipeline(_clean_line(raw))
        rule, note = got["rule"], got["note"]
    if not rule:
        if note:
            raise WriteError(
                f"pipeline 要先说清是 {' 还是 '.join(PIPELINE_RULES)}，只写说明的话这一行没有取值，"
                f"读侧看不见它（收到的说明是 {note!r}）。要撤销声明请传空串。")
        return {"rule": "", "note": ""}
    if rule not in PIPELINE_RULES:
        raise WriteError(
            f"pipeline 只能是 {'/'.join(PIPELINE_RULES)} 之一，收到 {rule!r}。"
            "exclude 的意思是「这一步成功了，但它是探索性的，没进最终流程」，"
            "include 的意思是「闭包够不到它，但它确实是流程的一环」。"
            "不写这一行才是常态——进不进流程默认由闭包算出来。")
    if not note:
        raise WriteError(
            f"pipeline: {rule} 必须写清理由，写成 `{rule} | 为什么`。"
            "这一行推翻的是算出来的结果，而它除了改变导出之外不留任何痕迹——"
            "没有理由的话，半年后没人分得清这是想清楚的决定还是一次误点。")
    return {"rule": rule, "note": note}


def _branch_groups(by_id: dict[str, Step]) -> dict[str, dict[str, Any]]:
    """按分叉点 id 索引的候选组（根之间那一组的键是空串）。

    **判据一条都不在这里**——直接用 core.compute_branch_groups。「谁和谁是一组」
    「选中了谁」「这个岔路口还没决定」全是派生的，写完之后当场说给人听的那句话
    和界面上画出来的必须出自同一次推导；各写一份的话两边平时看着都对，只有在
    某个刚被 move_step 挪走的候选身上才对不上，而那正是最需要它说对的时候。
    """
    return {g["at"]: g for g in compute_branch_groups(by_id, build_children(by_id))}


def _hydrate(step: Step, text: str) -> Step:
    """改写一份既有 note.md 之前，把 **validate() 改写过的字段**从原文读回来。

    render_note 是全量重写 front-matter 的，而 load() 给出的 Step 已经过 validate：
    悬空的 parent 被降级为根、环上的一条边被断开。那是**给渲染用的临时修正**，
    照着它写回磁盘，等于在人还没来得及修好数据之前，先把「这一步本来挂在 014 上」
    这个事实永久删掉了——而删掉之后连「本来挂在哪」都问不出来了。

    残缺输入产出部分结果（读侧）和残缺输入不被悄悄改写（写侧）是同一条原则的两面。
    """
    meta, _body, _w = parse_note(text)
    raw_parent = (meta.get("parent") or "").strip()
    step.parent = None if raw_parent.lower() in _ROOT_WORDS else raw_parent
    # branch 和 parent 是同一类：build_step 会把认不出来的 kind（`alterative` 这种
    # 笔误）**退回 extends** 并报一条 bad_branch 警告——那也是给渲染用的临时修正。
    # 照着它写回磁盘，等于在人还没来得及改好那个词之前，先把「这一步本来是个候选」
    # 这个事实永久删掉，连同那条本来在催人去改的警告一起。所以原文里是什么就读回
    # 什么，校验只管这次**新传进来**的值。
    b = parse_branch(meta.get("branch", ""))
    step.branch = b["kind"]                                # type: ignore[attr-defined]
    step.branch_note = b["note"]                           # type: ignore[attr-defined]
    step.decision = _clean_line(meta.get("decision", ""))  # type: ignore[attr-defined]
    # pipeline 同理**原样读回**，不过 norm_pipeline 的校验：文件里写着
    # `pipeline: exclud | 打错了` 的时候，照校验过的值写回去等于替人把那一行删掉，
    # 而人还以为自己声明过。校验只管这次新传进来的值（见 norm_pipeline 的说明）。
    pl = parse_pipeline(meta.get("pipeline", ""))
    step.pipeline = pl["rule"]                             # type: ignore[attr-defined]
    step.pipeline_note = pl["note"]                        # type: ignore[attr-defined]
    return step


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
    inputs: Any = None,
    code: Any = None,
    lang: str = "",
    branch: Any = "",
    decision: str = "",
    pipeline: Any = "",
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

        # 建的时候就能声明分叉语义。A/B 两条互斥的路多半是同一次想清楚之后一起
        # 建出来的，逼人建完再 PATCH 一次，`decision` 那句话（整件事里唯一推导
        # 不出来的信息）大概率就永远空着了。校验放在 mkdir 之前：非法值一个
        # 目录都不该留下。
        branch_row = norm_branch(branch)
        decision = _clean_line(decision)
        # 建的时候就能声明进不进流程。主要用法仍然是回头再补（update_step），
        # 但「这一步是拿来试试看的，不进最终流程」有时候动手之前就知道，
        # 而逼人建完再 PATCH 一次，那句理由大概率就永远空着了。
        # 校验同样放在 mkdir 之前：非法值一个目录都不该留下。
        pipeline_row = norm_pipeline(pipeline)

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
        # 数据依赖和代码位置：core 里还没有这两个字段时也要能落盘，所以直接挂在
        # 实例上（dataclass 没有 __slots__）。render_note 用 getattr 取它们。
        step.inputs = norm_inputs(inputs)      # type: ignore[attr-defined]
        step.code = norm_code(code)            # type: ignore[attr-defined]
        # 分叉语义同理直接挂在实例上。branch 说的是**这一步和它 parent 之间那条边**
        # 的性质，decision 说的是**这一步下面那个岔路口在决定什么**，两件事，
        # 各写各的一行。
        step.branch = branch_row["kind"]        # type: ignore[attr-defined]
        step.branch_note = branch_row["note"]   # type: ignore[attr-defined]
        step.decision = decision                # type: ignore[attr-defined]
        step.pipeline = pipeline_row["rule"]         # type: ignore[attr-defined]
        step.pipeline_note = pipeline_row["note"]    # type: ignore[attr-defined]

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
           "paths", "add_paths", "add_repro", "lang",
           "inputs", "add_inputs", "code", "add_code",
           # 分叉语义必须可变，而且这才是**主要**用法：两步先各自建出来，过几天
           # 回头才想明白「当时这两条是互斥的，只能选一条」。只在 create 时给，
           # 等于要求人在还没纠结完的时候就说清自己在纠结什么。
           "branch", "decision",
           # 「这一步到底进不进最终流程」天然是回头才定的：先跑完、先看见结果，
           # 才知道它是主线还是一次探索。只能在 create 时给等于要求人预判。
           "pipeline")


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
    """只允许改 status / title / body / date / commit / author / tags / paths / inputs / code。

    id 在这里仍然是硬地基——任何引用（笔记里写的"见 003b"、论文里的脚注）永远有效，
    靠的就是它不变，所以改 id 一律 409。
    parent 也不从这条路走，但理由变了：它**可以**改，只是必须留下审计记录，
    所以请求会被 409 到 move_step 那条路上去（见那个函数的说明）。

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
    # 非 UTF-8 就别动它（见 read_text_strict 的说明）；读到的原文顺手交给 _hydrate，
    # 把 core 还没建模的行和 validate 改写过的 parent 都读回来，免得整组重写时丢掉。
    _hydrate(step, read_text_strict(note_path, "这一步的 note.md"))

    if expect:
        current = digest_of(note_path)
        if current != expect:
            raise Conflict(
                f"这一步在你读到它之后被改过（你拿的是 {expect}，磁盘上现在是 {current}）。"
                "别直接重试——先重新读一遍，把你的改动合到最新内容上再存，"
                "否则会把这期间别人（很可能是你自己的 agent）写进去的整段内容抹掉。")

    if "id" in patch and _clean_line(patch["id"]) != _clean_line(step.id):
        raise Conflict(
            "id 不可修改（只追加原则）。笔记里写的 [[003b]] 和论文脚注里的引用"
            "永远有效，靠的就是 id 写下之后不再动。")
    if "parent" in patch and _clean_line(patch["parent"]) != _clean_line(step.parent):
        # 语义变了：parent 不再是「写下不可改」，而是「可改，但必须留下审计记录」。
        # 一整条支挂错了父节点是真会发生的事；没有移动能力，人会去对调两个节点的
        # 正文，那才是真的毁记录（创建日期和内容脱节，还一条审计都没有）。
        # 但也不能在这条路上放行：update_step 收不到原因，而没有原因的移动，
        # 半年后看到一棵和创建顺序对不上的树就再也解释不了了。
        raise Conflict(
            "parent 不能从这条路改。移动一步请用 move_step（REST 是 "
            'PATCH …/steps/{id} 带 {"parent": …, "reason": …}）并写清原因：'
            "移动会留下一条 moved: 审计，原因是那条审计里唯一无法自动生成的部分。")

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
    if "inputs" in patch:
        step.inputs = norm_inputs(patch["inputs"])          # type: ignore[attr-defined]
    if "add_inputs" in patch:                               # 追加，按 (步骤, 说明) 去重
        seen = {(i["step"], i["note"]) for i in step.inputs}    # type: ignore[attr-defined]
        step.inputs = step.inputs + [                       # type: ignore[attr-defined]
            i for i in norm_inputs(patch["add_inputs"]) if (i["step"], i["note"]) not in seen]
    if "code" in patch:
        step.code = norm_code(patch["code"])                # type: ignore[attr-defined]
    if "add_code" in patch:                                 # 追加，按位置去重
        seen_loc = {c["location"] for c in step.code}       # type: ignore[attr-defined]
        step.code = step.code + [                           # type: ignore[attr-defined]
            c for c in norm_code(patch["add_code"]) if c["location"] not in seen_loc]
    if "add_repro" in patch:                                # 只追加：复现历史不覆盖
        step.repro = step.repro + norm_repro(patch["add_repro"])
    if "lang" in patch:
        # 空串是**撤回声明**（回到「原文，语种不详」），不是非法值——
        # 写错了要能改回来，否则一个手滑的 lang: ja 会永久留在那儿。
        raw = _clean_line(patch["lang"])
        step.lang = norm_lang(raw) if raw else ""
    if "branch" in patch:
        # 空串 / extends 是**取消候选身份**，和 lang 一条理由：标错了要能改回来。
        # 注意这里不查「另一个候选还在不在」——一组里只剩一个候选不是错误，
        # 可能正是本意（「B 我不当候选了，它是独立的一条线」）。P4：结论不是错误。
        row = norm_branch(patch["branch"])
        step.branch = row["kind"]                             # type: ignore[attr-defined]
        step.branch_note = row["note"]                        # type: ignore[attr-defined]
    if "decision" in patch:
        # 分叉点上那句「在决定什么」。空串是撤回（这儿不再是个岔路口了）。
        step.decision = _clean_line(patch["decision"])        # type: ignore[attr-defined]
    if "pipeline" in patch:
        # 对定稿流程的例外声明。空串是撤回（进不进流程重新交给闭包算），
        # 撤回之后 render_note 不写这一行——不留一行空的（见 norm_pipeline）。
        row = norm_pipeline(patch["pipeline"])
        step.pipeline = row["rule"]                           # type: ignore[attr-defined]
        step.pipeline_note = row["note"]                      # type: ignore[attr-defined]

    # 目录名不跟着 title 改：目录名里的 id 是给 shell 补全用的，
    # 改名会让所有已经发出去的相对链接失效。
    write_atomic(note_path, _utf8_bytes(render_note(step), "正文"))
    return step


def mark_alternatives(steps_dir: Path, ids: Any, *, decision: str = "",
                      notes: dict[str, str] | None = None) -> dict[str, Any]:
    """把一组兄弟**成组**标成互斥候选，顺手把分叉点那句话写在它们的父节点上。

    为什么值得有这个入口，而不是让调用方对每个孩子各调一次 update_step
    （三条理由，每一条都对应一种「各调一次」拦不住的坏结果）：

      1. **「一组」这件事只有在这里看得见。**候选组是派生的——「parent 底下所有
         branch: alternative 的孩子」。update_step 一次只看得见一个孩子，于是把
         两个**不同父节点**下的步骤各标一次 alternative，得到的是两个各只有一个
         候选的组：一次都不报错，而人以为自己刚记下了一个岔路口。这里在写之前
         就能看见整组，同父校验是这个函数存在的主要理由。
      2. **要么全成、要么全不成。**校验全部前置、整段在同一把项目锁里，中途因为
         一个笔误退出不会留下「两个候选只标了一个」的半成品——而那个半成品恰好
         长得和 core 的「这组只剩一个候选」诊断一模一样，人会收到一条假诊断。
      3. **decision 会被顺手写掉。**「我在决定 X，A 和 B 是它的两个答案」是一句
         完整的话，拆成两次调用，人标完候选就走了，那句唯一无法自动生成的话就
         永远空着。这里让它成为同一个动作的一半。

    它**不是**唯一入口：update_step 照样能单独改一个孩子的 branch（回头补标第三个
    候选、或者把某一个改回普通延伸），两条路最终落盘的都只有孩子自己那一行。

    落盘的东西一个字都没多：每个孩子一行 `branch: alternative`，父节点一行
    `decision:`。**父节点上绝不写孩子清单**——那是双真相源，而且孩子一被
    move_step 挪走就过期。
    """
    want: list[str] = []
    for i in (ids or []):
        s = _clean_line(i)
        if s and s not in want:
            want.append(s)
    if len(want) < 2:
        raise WriteError(
            "成组标候选至少要两步：一个候选的「分叉点」不是分叉点。"
            "只想标一步（比如回头补上第三个候选）请用 update_step 的 branch 字段。")
    notes = {_clean_line(k): _clean_line(v) for k, v in (notes or {}).items()}

    with project_lock(steps_dir.parent):
        by_id = load(steps_dir)
        missing = [i for i in want if i not in by_id]
        if missing:
            raise NotFound("这些步骤不存在: " + ", ".join(missing))
        # 同父校验。互斥的前提是「同一个问题的几个答案」，而「同一个问题」在这棵树上
        # 就是同一个父节点；挂在不同父节点下的两步根本不构成一组候选。
        homes = {i: (by_id[i].parent or "") for i in want}
        if len(set(homes.values())) > 1:
            raise WriteError(
                "互斥候选必须是同一个父节点下的兄弟，收到的这几步挂在不同地方: "
                + "; ".join(f"{i} → {homes[i] or '（根）'}" for i in want)
                + "。候选组是从「父节点底下谁声明了 alternative」现算出来的，"
                  "跨父节点标只会得到几个各含一个候选的组，而且一条错都不报。"
                  "要先把它们挪到一起请用 move_step（原因必填）。")
        parent = by_id[want[0]].parent or None

        decision = _clean_line(decision)
        if decision and parent is None:
            raise WriteError(
                "这几步都是根节点，没有共同的父节点，那句「在决定什么」没有地方写。"
                "decision 写在分叉点**自己**身上，候选是它的孩子。")
        unknown = [i for i in notes if i not in want]
        if unknown:
            raise WriteError("notes 里有不在这一组里的步骤: " + ", ".join(sorted(unknown)))

        # 走的仍然是 update_step 那条路（本模块唯一的写入路径），不另开一条写文件的
        # 代码——上一代系统的 bug 根源就是存在第二条写入路径。锁是线程内可重入的，
        # 所以这里嵌套进来不会自锁。
        done: list[str] = []
        try:
            for sid in want:
                _update_step_locked(
                    steps_dir, sid,
                    {"branch": {"kind": "alternative", "note": notes.get(sid, "")}}, "")
                done.append(sid)
            if decision:
                _update_step_locked(steps_dir, parent, {"decision": decision}, "")
        except Exception as exc:
            # 还一个字节都没写（比如第一步的 note.md 是 GBK）：原样把真正的原因抛出去，
            # 包一层只会把「先去转码」这句可执行的话埋掉。
            if not done:
                raise
            # 真的只写了一半（磁盘满、文件被占用）：说清楚哪几步已经落盘了。
            # 半成品长得和 core 的 lone_alternative 诊断一模一样，不说清楚，
            # 人下一眼看到的是一条自己看不懂的假诊断。
            raise WriteError(
                f"这一组只标到一半就写不下去了（已经落盘的是 {', '.join(done)}）: {exc}。"
                "剩下的请重跑一次——重复标同一组是幂等的。") from exc

        # 标完之后重新扫一遍，把这一组**现在**的样子交回去（谁在组里、还没决定
        # 还是已经定了）。这是 core 算的那一份，不是这里另算的一份。
        group = _branch_groups(load(steps_dir)).get(parent or "")

    return {"parent": parent or "", "decision": decision,
            "marked": list(want), "group": group}


def _ancestors(by_id: dict[str, Step], sid: str) -> list[str]:
    """从 sid 沿 parent 链一路向上，返回经过的祖先（不含自己）。

    数据可能是残缺的：parent 指向不存在的 id（人手删了一步）、甚至已经成环
    （两个人分别手改了 note.md）。两种情况都不能让这个函数转不出来，
    所以既查 by_id 里在不在，也用 seen 兜住环——「构建器必须能在残缺输入上
    产出部分结果」这条对写入侧的校验同样适用。
    """
    out: list[str] = []
    seen = {sid}
    cur = by_id[sid].parent if sid in by_id else None
    while cur and cur in by_id and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = by_id[cur].parent
    return out


def move_step(steps_dir: Path, sid: str, new_parent: str | None, reason: str,
              by: str = "", date: str = "", expect: str = "") -> dict[str, Any]:
    """把一步（连同它的整棵子树）改挂到另一个父节点下，并留下审计记录。

    这是 P2 在这一版里的重新定义：**只追加的地基是「不丢历史」，不是「不能改结构」
    ——记下来就不丢。** id 仍然永不可改（引用要一直有效），parent 变成「可改，
    但必须写原因」。

    没有这个函数的时候，人是怎么解决「一整条支挂错了父节点」的：把两个节点的
    **正文对调**。结果是 013b 的创建日期和它现在装的内容对不上号，而那次修改
    一条记录都没留下。能移动 + 强制写原因，历史反而完整。

    落盘的是 front-matter 里追加的一行（可重复，一个节点可以被移动多次）：

        moved: 2026-08-09 | 014 | 013b | human | 补原子的产物从未进过下游计算

    为什么放 front-matter 而不是正文里的「## 已移动」：正文是人的判断区，
    一次编辑就可能整段误删；front-matter 是机器记录区，render_note 全量重写它，
    这一行只会被追加，不会被人顺手改没。

    校验（每一条都能举出它挡住的真实错误）：
      * reason 必填 —— 半年后看到一棵和创建顺序对不上的树，唯一能解释它的就是这句话；
      * 目标必须存在且在**同一个项目**里（steps_dir 天然限定了项目，跨项目的 id
        在这里就是「不存在」）；
      * 不能挂到自己身上，也不能挂到自己的后代下面（那会造出一个脱离森林的环，
        整棵子树从树上消失）；
      * 和现在的 parent 相同直接报错，而不是留一条毫无信息量的审计。

    `expect` 和 update_step 一视同仁：对不上就 409，一个字节都不写。
    """
    reason = _clean_line(reason)
    if not reason:
        raise WriteError(
            "移动必须写原因。移动之后这棵树就和创建顺序对不上了——"
            "「为什么 016 挂在 013b 下面，而它的号比 013b 大」只有这句话答得了。"
            "写清楚是哪条数据依赖决定了新的父子关系，而不是「修正结构」。")
    expect = str(expect or "").strip()
    new_parent = (new_parent or "").strip() or None
    if new_parent and new_parent.lower() in _ROOT_WORDS:
        new_parent = None

    with project_lock(steps_dir.parent):
        by_id = load(steps_dir)
        if sid not in by_id:
            raise NotFound(f"步骤 {sid} 不存在")
        step = by_id[sid]
        if "~" in step.id:
            # 和 update_step 同一条理由：`001~dup2` 是「两个目录用了同一个 id」时
            # 贴的临时显示标签，把它写进 note.md 就是把派生信息变成存储信息。
            raise Conflict(
                f"{sid} 不是这一步真正的 id，只是「有两个目录用了同一个 id」时的临时标签。"
                "请先在文件系统里把两个同号目录之一改成新号，再来移动它。")

        note_path = steps_dir / step.dirname / NOTE_NAME
        _hydrate(step, read_text_strict(note_path, "这一步的 note.md"))
        if expect:
            current = digest_of(note_path)
            if current != expect:
                raise Conflict(
                    f"这一步在你读到它之后被改过（你拿的是 {expect}，磁盘上现在是 {current}）。"
                    "别直接重试——先重新读一遍再决定要不要移动。")

        old_parent = step.parent or None
        subtree = _descendants(by_id, sid)
        if new_parent == old_parent:
            raise WriteError(
                f"{sid} 的 parent 已经是 {new_parent or '（根）'} 了。"
                "这是一次空操作，不该在历史里留下一条什么都没改的移动记录。")
        if new_parent == sid:
            raise WriteError(f"不能把 {sid} 挂到它自己下面。")
        if new_parent is not None:
            if new_parent not in by_id:
                raise NotFound(
                    f"目标父节点 {new_parent} 在这个项目里不存在。"
                    "父子关系只在同一个项目内成立——跨项目请用正文里的 [[引用]]。")
            # 两条判据是同一件事的两个方向（自己在目标的祖先链上 ⟺ 目标在自己的
            # 子树里）。都留着是因为残缺数据里它们可能不一致：parent 链在悬空的
            # 那一环断掉，而 children 索引照样把断点之后的节点算进子树。
            # 移动的代价是整棵子树，宁可多挡一次。
            if sid in _ancestors(by_id, new_parent) or new_parent in subtree:
                raise Conflict(
                    f"{new_parent} 在 {sid} 的子树里，挂过去会成环，"
                    f"整棵子树会从森林上掉下来。要把 {new_parent} 那一支拿出来，"
                    f"请先把它移到别处，再移 {sid}。")

        # 候选组是派生的，所以移动一步**写入侧什么都不用做**：012 从 011 挪到 013
        # 底下，它那一行 `branch: alternative` 一个字不改，含义自动从「011 那个岔路口
        # 的候选」变成「013 那个岔路口的候选」。这正是不在父节点上存孩子清单的收益。
        #
        # 但要不要**当场说一声**「011 那组现在只剩 012b 一个候选了」？要。理由和下面
        # subtree 那条一模一样：那是这次移动的直接后果，事后只能靠重新拉一遍森林才
        # 看得见，而移动的人这会儿正好在做决定。用的是 core 的那一份推导，不另算。
        #
        # 明确**不**做的两件事：不拦、不改数据。只剩一个候选完全可能正是本意
        # （「B 不再是候选了，它是独立的一条线」），而且一组候选做完决定之后本来就
        # 只剩一个不是 dead 的（core 把那叫 decided，不是错误）。拒绝移动只会逼人
        # 先把 branch 那一行删掉再移，留下更差的历史。P4：结论不是错误。
        day = _clean_line(date) or today()
        if not _DATE_RE.match(day):
            raise WriteError(f"移动日期要写成 YYYY-MM-DD，收到 {day!r}")
        entry = {"date": day, "from": old_parent or "", "to": new_parent or "",
                 "by": _clean_line(by), "reason": reason}
        # 只追加：一个节点可以被移动多次，那是一段历史，不是一个当前值。
        step.moved = list(getattr(step, "moved", None) or []) + [entry]   # type: ignore[attr-defined]
        step.parent = new_parent
        write_atomic(note_path, _utf8_bytes(render_note(step), "正文"))
        # 移完再扫一遍：两边候选组**现在**长什么样，才是要说给人听的那句话。
        after = _branch_groups(load(steps_dir))
        alt_left, alt_joined = after.get(old_parent or ""), after.get(new_parent or "")

    return {"id": sid, "old_parent": old_parent, "new_parent": new_parent,
            "reason": reason, "by": entry["by"], "date": entry["date"],
            "moved": "moved: " + format_moved(entry),
            # 移动带走的是整棵子树。调用方要能当场把这件事说给人听——
            # 「你移的是一步」和「你移的是一步和它下面的 9 步」是两个决定。
            "subtree": sorted(subtree, key=id_key),
            # 移动之后，原来那个岔路口和新的那个岔路口各自剩下什么。派生量，
            # 现算，不落盘（见上面那段说明）。两边都可能是 None：那一头压根没有
            # 候选组——「这次移动和任何一个岔路口都无关」也是一句要说清的话。
            "alternatives": {"left": alt_left, "joined": alt_joined},
            "digest": digest_of(note_path)}


def _descendants(by_id: dict[str, Step], sid: str) -> set[str]:
    """sid 的整棵子树（不含自己）。广度优先 + seen，残缺数据里的环也转得出来。"""
    kids: dict[str, list[str]] = {}
    for k, s in by_id.items():
        if s.parent:
            kids.setdefault(s.parent, []).append(k)
    out: set[str] = set()
    stack = list(kids.get(sid, []))
    while stack:
        cur = stack.pop()
        if cur in out or cur == sid:
            continue
        out.add(cur)
        stack.extend(kids.get(cur, []))
    return out


def record_path_check(steps_dir: Path, sid: str, loc: str, *, exists: bool,
                      date: str = "", size: int | None = None,
                      n: int | None = None) -> dict[str, Any]:
    """记一次「这条路径还在不在」的核对结果。**只动机器字段**。

    用户这次逐条核对 164 条外部路径时发现有三个目录已经被删了（其中一个 57 GB），
    这本该由机器自己发现。所以有了这个入口：服务端或 agent 定期 stat 一遍，
    把结果写回属性——

        path: /blue/lab/cif_files | input | 原始 CIF | size=61203283968 missing=2026-08-09

    三条硬约定：
      * **不删记录。** 路径没了是溯源结论（「这份数据当年在这儿，现在没了」），
        和 dead 一样有价值。删掉它等于抹掉线索。
      * **不动 role 和说明**，那两样是人写的判断，机器没有资格改。
      * checked 和 missing 互斥：确认存在就清掉 missing，反之亦然。最后一次核对
        是哪种结果，这条路径现在就是哪种状态。size / n 即使在 missing 时也保留——
        「没了的那个有 57 GB」正是要留下来的信息。
    """
    loc = _clean_line(loc)
    if not loc:
        raise WriteError("要核对哪条路径？loc 不能为空。")
    day = _clean_line(date) or today()
    if not _DATE_RE.match(day):
        raise WriteError(f"核对日期要写成 YYYY-MM-DD，收到 {day!r}")

    with project_lock(steps_dir.parent):
        by_id = load(steps_dir)
        if sid not in by_id:
            raise NotFound(f"步骤 {sid} 不存在")
        step = by_id[sid]
        note_path = steps_dir / step.dirname / NOTE_NAME
        _hydrate(step, read_text_strict(note_path, "这一步的 note.md"))

        idx = next((i for i, p in enumerate(step.paths) if p["location"] == loc), None)
        if idx is None:
            raise NotFound(
                f"步骤 {sid} 上没有记着 {loc} 这条路径。核对结果只写在已经记下来的路径上——"
                "凭空多出一条会让「这一步产出了什么」变成机器说了算。")
        hit = step.paths[idx]
        attrs = dict(hit.get("attrs") or {})
        # checked 和 missing 互斥：留着上一次的相反结论，读侧就得靠比日期猜现在的状态。
        attrs.pop("missing" if exists else "checked", None)
        attrs["checked" if exists else "missing"] = day
        if size is not None:
            attrs["size"] = str(int(size))
        if n is not None:
            attrs["n"] = str(int(n))
        row = _path_row(hit["location"], hit.get("note", ""), hit.get("role", ""), attrs)
        step.paths[idx] = row
        write_atomic(note_path, _utf8_bytes(render_note(step), "正文"))

    return {"id": sid, "location": loc, "exists": bool(exists), "date": day,
            "path": row, "line": "path: " + format_path(row),
            "digest": digest_of(note_path)}


def delete_step(steps_dir: Path, sid: str, reason: str, by: str = "", date: str = "") -> dict[str, Any]:
    """真删：整个目录连同附件一起移除。

    这是对 P2「只追加」的一处**有意的例外**，用来处理"这条记录本身就不该存在"
    ——误建、测试数据、不小心粘进去的令牌。它和 `dead` 是两件事：
    `dead` 是研究结论（此路不通），往里塞垃圾会毁掉这套系统最有价值的信号。

    三个已知代价，是明确接受的：

    1. **id 会被重用。** 分配用的是"现存 id 的最大值 + 1"，删掉最大号之后
       下一步就会拿到同一个号。于是半年前笔记里的「见 002」可能指向另一个东西。
       要避免就得有个"用过哪些号"的中心文件，而那正是 P1 禁止的。
    2. **子步骤会变成孤儿。** 它们的 parent 指向一个不存在的 id，
       validate() 会把它们降级为根并给出警告——构建不会崩，但那条线断了。
    3. **别的步骤的 `input:` 会指空。** 数据依赖是这一版新加的边，和 `[[002]]`
       那种正文引用一样会被删除打断，但后果更重：`[[002]]` 是给人读的一句话，
       而 `input: 002 | train.jsonl` 是「这些字节从哪来」的**声明**，可溯源性
       正沿着它上溯。再叠上第 1 条（id 会被重用），下一个拿到 002 的步骤会
       **静悄悄地**接手这些边——那时它指向的是一个完全不相干的实验，而没有
       任何一处会报错。所以它必须和孤儿一样，删的时候当场报出来。

    4. **project.md 里的 `result:` 会指空。** 后果比第 3 条更重：定稿流程是从成果
       反向做闭包算出来的，起点没了，整条流程**静默变空**（页面上不报错，只是那一栏
       没了）；再叠上第 1 条，下一个拿到这个号的步骤会无声地变成论文的主结果。
       这里**不替人撤掉那一行**——声明是人写的，机器代撤之后「定稿流程曾经指向一步
       被删的记录」就再也没人看得见了。报出来，让人自己决定改指哪一步。

    所以返回值里明确告诉调用方这四件事各自发生了多少，让人能当场看见后果。
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
        # 谁声明过「我读了这一步的产物」。这条边一样会被删除打断，见上面第 3 条。
        eaters = sorted((k for k, s in by_id.items()
                         if k != sid and any(i["step"] == sid for i in s.inputs)), key=id_key)
        # 这一步被声明成成果了没有。报的是那一行的原文，人一眼看得出要去改什么。
        declared = [f"result: {format_result(r)}"
                    for r in _load_project_note(steps_dir.parent)[2] if r["step"] == sid]

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
            "files_removed": files, "orphaned": children, "dangling_refs": refs,
            "dangling_inputs": eaters, "dangling_results": declared}


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
