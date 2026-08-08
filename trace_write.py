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
"""

from __future__ import annotations

import hashlib
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any

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
    path_kind,
    project_dir,
    projects_root,
    scan,
    scan_projects,
    split_id,
    steps_dir_of,
    validate,
)

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
    base = slugify(name)
    existing = {p.slug for p in scan_projects(root)}
    slug, n = base, 2
    while slug in existing:
        slug, n = f"{base}-{n}", n + 1

    d = project_dir(root, slug)
    if d.exists():
        raise Conflict(f"项目目录已存在: {slug}")
    (d / "steps").mkdir(parents=True)
    (d / PROJECT_NOTE).write_text(
        f"---\nname: {name}\n---\n\n", encoding="utf-8", newline="\n"
    )
    return Project(slug=slug, name=name)


# 项目级的沉淀。它不属于任何单独一步——「回译在这个数据集上一直没用」是三次
# 尝试之后的判断，挂在哪一步都不对。所以放在 project.md 的正文里。
INSIGHT_SECTIONS = {
    "idea": "核心想法",
    "works": "有效",
    "fails": "无效",
    "pitfall": "坑",
}
INSIGHT_TEMPLATE = "\n\n".join(f"## {t}" for t in INSIGHT_SECTIONS.values()) + "\n"


def update_project(root: Path, slug: str, *, name: str | None = None,
                   insights: str | None = None,
                   add: tuple[str, str] | None = None) -> Project:
    """改项目的显示名和/或洞察正文。

    `add=(kind, text)` 往对应小节追加一条，小节不存在就补出来。
    **目录名（= URL 里的 slug）永远不动**——改了会让所有已发出的链接失效。
    """
    from trace_core import parse_note

    d = project_dir(root, slug)
    if not d.is_dir():
        raise NotFound(f"项目 {slug} 不存在")
    note = d / PROJECT_NOTE
    meta: dict[str, str] = {}
    body = ""
    if note.is_file():
        meta, body, _w = parse_note(note.read_text(encoding="utf-8", errors="replace"))

    final_name = _clean_line(name) if name is not None else (meta.get("name") or slug).strip()
    if not final_name:
        raise WriteError("项目名不能为空")

    if insights is not None:
        body = str(insights).replace("\r\n", "\n").replace("\r", "\n")
    if add is not None:
        kind, text = add
        heading = INSIGHT_SECTIONS.get(kind)
        if heading is None:
            raise WriteError(f"洞察类型必须是 {'/'.join(INSIGHT_SECTIONS)} 之一，收到 {kind!r}")
        text = re.sub(r"\s*\n\s*", " ", str(text or "")).strip()
        if not text:
            raise WriteError("洞察内容不能为空")
        body = _append_under(body, heading, "- " + text)

    note.write_text(
        f"---\nname: {final_name}\n---\n\n{body.strip()}\n" if body.strip()
        else f"---\nname: {final_name}\n---\n\n",
        encoding="utf-8", newline="\n")
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
    note = d / PROJECT_NOTE
    body = ""
    if note.is_file():
        from trace_core import parse_note

        _meta, body, _w = parse_note(note.read_text(encoding="utf-8", errors="replace"))
    note.write_text(f"---\nname: {name}\n---\n\n{body}\n".rstrip() + "\n", encoding="utf-8", newline="\n")
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
    for k in ("date", "commit", "author", "key"):
        v = getattr(step, k)
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
) -> tuple[Step, bool]:
    """新建一步。返回 (step, created)；created=False 表示命中幂等键返回了既有步骤。"""
    steps_dir.mkdir(parents=True, exist_ok=True)
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
        date=_clean_line(date),
        commit=_clean_line(commit),
        author=_clean_line(author),
        key=key,
        tags=[_clean_line(t) for t in (tags or []) if _clean_line(t)],
        paths=norm_paths(paths),
        body=(BODY_TEMPLATE if body is None else body),
        dirname=f"{sid}_{slugify(title)}",
    )

    d = steps_dir / step.dirname
    if d.exists():
        raise Conflict(f"目录已存在: {step.dirname}")
    d.mkdir(parents=True)
    (d / NOTE_NAME).write_text(render_note(step), encoding="utf-8", newline="\n")
    return step, True


# ---------------------------------------------------------------- 修改

MUTABLE = ("status", "title", "body", "date", "commit", "author", "tags",
           "paths", "add_paths", "add_repro")


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


def update_step(steps_dir: Path, sid: str, patch: dict[str, Any]) -> Step:
    """只允许改 status / title / body / date / commit / author / tags。

    id 和 parent 是只追加系统的地基——任何引用（笔记里写的"见 003b"、论文里的脚注）
    永远有效，靠的就是它们不变。改它们的请求一律 409。
    """
    by_id = load(steps_dir)
    if sid not in by_id:
        raise NotFound(f"步骤 {sid} 不存在")
    step = by_id[sid]

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

    # 目录名不跟着 title 改：目录名里的 id 是给 shell 补全用的，
    # 改名会让所有已经发出去的相对链接失效。
    (steps_dir / step.dirname / NOTE_NAME).write_text(render_note(step), encoding="utf-8", newline="\n")
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

    by_id = load(steps_dir)
    if sid not in by_id:
        raise NotFound(f"步骤 {sid} 不存在")
    target = by_id[sid]

    children = sorted((k for k, s in by_id.items() if s.parent == sid), key=id_key)
    refs = sorted(k for k, s in by_id.items()
                  if k != sid and re.search(rf"\[\[\s*{re.escape(sid)}\s*\]\]", s.body))

    d = steps_dir / target.dirname
    files = sum(1 for _ in d.rglob("*") if _.is_file())
    shutil.rmtree(d)

    _log_deletion(steps_dir.parent, sid, target.title, reason, by, date)
    return {"id": sid, "title": target.title, "reason": reason,
            "files_removed": files, "orphaned": children, "dangling_refs": refs}


def _log_deletion(project_dir_: Path, sid: str, title: str, reason: str, by: str, date: str) -> None:
    """把删除记进项目的 project.md。

    这**不是**中心索引——没有任何东西靠它重建结构，所以 P1 不破，它也不可能
    "和实际内容不一致"。它只是一份人可读、可 grep 的历史：目录没了之后，
    「006 是什么、为什么删的」只剩这一行。
    """
    note = project_dir_ / PROJECT_NOTE
    meta: dict[str, str] = {}
    body = ""
    if note.is_file():
        from trace_core import parse_note

        meta, body, _w = parse_note(note.read_text(encoding="utf-8", errors="replace"))
    stamp = " · ".join(x for x in (date.strip(), by.strip()) if x.strip())
    entry = f"- `{sid}` {title}".rstrip() + f" —— {reason}" + (f"（{stamp}）" if stamp else "")
    body = _append_under(body, "已删除", entry)
    name = (meta.get("name") or project_dir_.name).strip()
    note.write_text(f"---\nname: {name}\n---\n\n{body.strip()}\n", encoding="utf-8", newline="\n")


def append_body(steps_dir: Path, sid: str, text: str) -> Step:
    by_id = load(steps_dir)
    if sid not in by_id:
        raise NotFound(f"步骤 {sid} 不存在")
    body = by_id[sid].body.rstrip("\n") + "\n\n" + text.strip("\n") + "\n"
    return update_step(steps_dir, sid, {"body": body})


# ---------------------------------------------------------------- 附件


def safe_relpath(relpath: str) -> str:
    """防目录穿越。拒绝绝对路径、盘符、.. 和控制字符。"""
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
        parts.append(part)
    if not parts:
        raise WriteError("文件名不能为空")
    out = "/".join(parts)
    if out == NOTE_NAME:
        raise WriteError("note.md 请用 PATCH /api/steps/{id} 修改")
    return out


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
    target.write_bytes(data)
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

    target.write_bytes(data)
    return {"path": candidate, "size": len(data), "reused": False}


def delete_file(steps_dir: Path, sid: str, relpath: str) -> None:
    by_id = load(steps_dir)
    target = resolve_attachment(steps_dir, by_id, sid, relpath)
    if not target.is_file():
        raise NotFound(f"附件不存在: {relpath}")
    target.unlink()
