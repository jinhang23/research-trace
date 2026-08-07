"""trace_write — 唯一写入路径。

CLI、网页表单、agent API 全部调这里，不允许任何一方绕过去直接写文件。
上一代系统的 bug 根源就是存在第二条写入路径（直接 sqlite3 INSERT），
导致父子关系只写进了一半的地方。这里用"只有一个函数会创建 note.md"来杜绝。

只追加原则（P2）在这里强制：
  * id 由服务端分配，永不重编号；
  * parent 一旦写下就不可改（update_step 直接抛 Conflict）；
  * 没有删除步骤的 API。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from trace_core import (
    NOTE_NAME,
    STATUSES,
    DEFAULT_STATUS,
    Step,
    build_children,
    id_key,
    fmt_id,
    scan,
    split_id,
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

MUTABLE = ("status", "title", "body", "date", "commit", "author", "tags")


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

    # 目录名不跟着 title 改：目录名里的 id 是给 shell 补全用的，
    # 改名会让所有已经发出去的相对链接失效。
    (steps_dir / step.dirname / NOTE_NAME).write_text(render_note(step), encoding="utf-8", newline="\n")
    return step


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


def delete_file(steps_dir: Path, sid: str, relpath: str) -> None:
    by_id = load(steps_dir)
    target = resolve_attachment(steps_dir, by_id, sid, relpath)
    if not target.is_file():
        raise NotFound(f"附件不存在: {relpath}")
    target.unlink()
