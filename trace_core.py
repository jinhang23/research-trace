"""trace_core — 纯函数内核。

    scan → parse → validate → order → lanes → compile

约束（不可妥协）：
  * 除标准库外零依赖；
  * 除 scan/signature 读盘外无副作用；
  * 同样的输入永远产出同样的输出（静态导出要求逐字节一致）；
  * 派生字段（files / children / backlinks / lineage）一律计算得到，绝不存储。

布局算法（order / lanes）是最容易写错的部分，因此被写成不碰 IO 的纯函数，
可以直接对着期望结果写断言，不需要跑渲染。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

NOTE_NAME = "note.md"
STATUSES = ("wip", "done", "dead")
DEFAULT_STATUS = "wip"

# 行高与轨道宽在前端也要用到同一组数字，放在这里作为唯一来源。
ROW_H = 28
LANE_W = 14

_ID_RE = re.compile(r"^(\d+)([a-z]*)$")
_DIRNAME_RE = re.compile(r"^(\d+[a-z]*)_(.*)$")
_WIKILINK_RE = re.compile(r"\[\[\s*([0-9]+[a-z]*)\s*\]\]")

# ---------------------------------------------------------------- id 工具


def id_key(sid: str) -> tuple[int, str]:
    """id 排序键。

    字符串序下 "9" > "10"，所以数字部分必须按 int 比。
    分叉后缀为空串时天然排在字母前面（"" < "b"），正好符合
    "004 是主线、004b/004c 是后来分出去的" 这个直觉。
    不合规范的 id 一律排到最后，但仍然是确定性的。
    """
    m = _ID_RE.match(sid)
    if not m:
        return (10**9, sid)
    return (int(m.group(1)), m.group(2))


def fmt_id(n: int) -> str:
    """固定三位宽；超过 999 自然溢出成四位，排序仍然正确。"""
    return f"{n:03d}"


def split_id(sid: str) -> tuple[str, str] | None:
    """拆成 (数字部分保留前导零, 字母后缀)。不合规范返回 None。"""
    m = _ID_RE.match(sid)
    return (m.group(1), m.group(2)) if m else None


# ---------------------------------------------------------------- 数据结构


@dataclass
class Step:
    id: str
    parent: str | None = None
    status: str = DEFAULT_STATUS
    title: str = ""
    date: str = ""
    commit: str = ""
    author: str = ""
    key: str = ""
    tags: list[str] = field(default_factory=list)
    body: str = ""
    dirname: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent": self.parent,
            "status": self.status,
            "title": self.title,
            "date": self.date,
            "commit": self.commit,
            "author": self.author,
            "key": self.key,
            "tags": list(self.tags),
            "body": self.body,
            "dirname": self.dirname,
        }


def warn(level: str, code: str, message: str, where: str = "") -> dict[str, str]:
    return {"level": level, "code": code, "message": message, "where": where}


# ---------------------------------------------------------------- parse


def parse_note(text: str) -> tuple[dict[str, str], str, list[dict[str, str]]]:
    """拆 front-matter 和正文。

    刻意不用 YAML：`title: 试了 3:1 采样` 这种标题在 YAML 里是语法错误，
    而这类标题在科研记录里非常常见。这里的规则是"冒号左边是键、右边整行是值"，
    对本用途更健壮，且零依赖。
    """
    warnings: list[dict[str, str]] = []
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    if not lines or lines[0].strip() != "---":
        if text.strip():
            warnings.append(warn("warn", "no_front_matter", "缺少 front-matter，全部内容当作正文"))
        return {}, text.strip("\n"), warnings

    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close = i
            break
    if close is None:
        warnings.append(warn("warn", "unclosed_front_matter", "front-matter 没有闭合的 ---，全部内容当作正文"))
        return {}, text.strip("\n"), warnings

    meta: dict[str, str] = {}
    for raw in lines[1:close]:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if ":" not in s:
            warnings.append(warn("warn", "bad_front_matter_line", f"忽略无法解析的行: {s!r}"))
            continue
        k, _, v = s.partition(":")
        k = k.strip().lower()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k:
            meta[k] = v

    body = "\n".join(lines[close + 1 :]).strip("\n")
    return meta, body, warnings


def _parse_tags(raw: str) -> list[str]:
    raw = raw.strip().strip("[]")
    return [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]


def build_step(dirname: str, meta: dict[str, str], body: str) -> tuple[Step, list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    dm = _DIRNAME_RE.match(dirname)
    dir_id = dm.group(1) if dm else ""

    sid = (meta.get("id") or "").strip()
    if not sid:
        if dir_id:
            sid = dir_id
            warnings.append(warn("warn", "id_from_dirname", "front-matter 缺 id，回退到目录名", dirname))
        else:
            sid = dirname
            warnings.append(warn("error", "no_id", "front-matter 缺 id 且目录名不含 id", dirname))
    elif dir_id and dir_id != sid:
        # 规格书第 8 节：以 front-matter 为准 + 警告。
        warnings.append(
            warn("warn", "id_mismatch", f"目录名 id ({dir_id}) 与 front-matter id ({sid}) 不一致，以后者为准", dirname)
        )

    status = (meta.get("status") or "").strip().lower() or DEFAULT_STATUS
    if status not in STATUSES:
        warnings.append(warn("warn", "bad_status", f"未知 status {status!r}，回退到 {DEFAULT_STATUS}", dirname))
        status = DEFAULT_STATUS

    parent = (meta.get("parent") or "").strip()
    if parent.lower() in ("", "none", "null", "-"):
        parent = None

    step = Step(
        id=sid,
        parent=parent,
        status=status,
        title=(meta.get("title") or "").strip(),
        date=(meta.get("date") or "").strip(),
        commit=(meta.get("commit") or "").strip(),
        author=(meta.get("author") or "").strip(),
        key=(meta.get("key") or "").strip(),
        tags=_parse_tags(meta.get("tags", "")),
        body=body,
        dirname=dirname,
    )
    return step, warnings


# ---------------------------------------------------------------- scan


def list_files(step_dir: Path) -> list[dict[str, Any]]:
    """该目录下的附件清单（递归，排除 note.md 与点开头的文件）。

    必须是派生字段——一旦写进 note 就会和实际目录漂移。
    """
    out: list[dict[str, Any]] = []
    root_str = str(step_dir)
    for root, dirs, names in os.walk(step_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for n in sorted(names):
            if n.startswith("."):
                continue
            if root == root_str and n == NOTE_NAME:
                continue
            p = Path(root) / n
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            out.append({"path": p.relative_to(step_dir).as_posix(), "size": size})
    out.sort(key=lambda f: f["path"])  # os.walk 的顺序是深度优先，全局排序更好读也更显然确定
    return out


def scan(steps_dir: Path, with_files: bool = True) -> tuple[list[Step], dict[str, list[dict]], list[dict[str, str]]]:
    """读目录，找 note.md。没有 note.md 的目录静默跳过（允许临时目录共存）。"""
    steps: list[Step] = []
    files: dict[str, list[dict]] = {}
    warnings: list[dict[str, str]] = []
    if not steps_dir.is_dir():
        return steps, files, [warn("warn", "no_steps_dir", f"目录不存在: {steps_dir}")]

    for entry in sorted(steps_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        note = entry / NOTE_NAME
        if not note.is_file():
            continue
        try:
            text = note.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(warn("error", "unreadable", f"无法读取: {exc}", entry.name))
            continue
        meta, body, w1 = parse_note(text)
        step, w2 = build_step(entry.name, meta, body)
        for w in w1 + w2:
            w["where"] = w["where"] or entry.name
        warnings.extend(w1 + w2)
        steps.append(step)
        files[step.id] = list_files(entry) if with_files else []
    return steps, files, warnings


def signature(steps_dir: Path) -> str:
    """目录内容指纹，用于判断是否需要重新编译。不进入静态导出产物。"""
    h = hashlib.sha1()
    if not steps_dir.is_dir():
        return "empty"
    for root, dirs, names in os.walk(steps_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for n in sorted(names):
            if n.startswith("."):
                continue
            p = Path(root) / n
            try:
                st = p.stat()
            except OSError:
                continue
            h.update(str(p.relative_to(steps_dir).as_posix()).encode("utf-8"))
            h.update(f"{st.st_mtime_ns}:{st.st_size}".encode("ascii"))
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- validate


def validate(steps: Iterable[Step]) -> tuple[dict[str, Step], list[dict[str, str]]]:
    """强制不变量 I1~I4，但**永不中断构建**。

    十年后的日志一定是残缺的，构建器必须能在残缺输入上产出部分结果。
    因此环也是"报错 + 断开 + 继续"，而不是拒绝工作。
    """
    warnings: list[dict[str, str]] = []
    by_id: dict[str, Step] = {}

    # I1 · id 全局唯一。重复的重新挂一个可见的 id，而不是丢掉数据。
    for s in steps:
        if s.id not in by_id:
            by_id[s.id] = s
            continue
        n = 2
        while f"{s.id}~dup{n}" in by_id:
            n += 1
        new_id = f"{s.id}~dup{n}"
        warnings.append(
            warn("error", "duplicate_id",
                 f"id {s.id} 重复（已存在于 {by_id[s.id].dirname}），本步骤临时显示为 {new_id}，请手工改掉",
                 s.dirname)
        )
        s.id = new_id
        by_id[new_id] = s

    # I2 · parent 必须存在，否则降级为根。
    for s in by_id.values():
        if s.parent and s.parent not in by_id:
            warnings.append(
                warn("warn", "dangling_parent", f"parent {s.parent} 不存在，本步骤降级为根", s.dirname)
            )
            s.parent = None

    # I3 · 无环。断开环上 id 序最小的那条边。
    color: dict[str, int] = {}  # 0 未访问 1 在栈上 2 已完成
    for start in sorted(by_id, key=id_key):
        if color.get(start, 0):
            continue
        path: list[str] = []
        cur: str | None = start
        while cur is not None and color.get(cur, 0) == 0:
            color[cur] = 1
            path.append(cur)
            cur = by_id[cur].parent
        if cur is not None and color.get(cur) == 1:
            cycle = path[path.index(cur):]
            victim = min(cycle, key=id_key)
            warnings.append(
                warn("error", "cycle",
                     "检测到环: " + " → ".join(cycle + [cycle[0]]) + f"；已断开 {victim} 的 parent 以便继续构建",
                     by_id[victim].dirname)
            )
            by_id[victim].parent = None
        for sid in path:
            color[sid] = 2

    # I4 · 单父由数据模型保证（parent 是标量字段），无需检查。
    return by_id, warnings


def build_children(by_id: dict[str, Step]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {sid: [] for sid in by_id}
    for sid, s in by_id.items():
        if s.parent:
            children[s.parent].append(sid)
    for kids in children.values():
        kids.sort(key=id_key)
    return children


# ---------------------------------------------------------------- order


def compute_order(by_id: dict[str, Step], children: dict[str, list[str]]) -> list[str]:
    """前序 DFS：根按 id 升序，子按 id 升序。

    保证：
      O1 父的行号严格小于所有子的行号
      O2 一棵子树占据连续的行区间（将来加"折叠分支"几乎零成本）
    """
    roots = sorted((sid for sid, s in by_id.items() if not s.parent), key=id_key)
    order: list[str] = []
    stack = list(reversed(roots))
    while stack:
        sid = stack.pop()
        order.append(sid)
        stack.extend(reversed(children.get(sid, [])))
    return order


# ---------------------------------------------------------------- lanes


def compute_lanes(
    by_id: dict[str, Step], children: dict[str, list[str]], order: list[str]
) -> tuple[dict[str, int], dict[str, int]]:
    """git graph 那套轨道分配。返回 (lane, end)。

      row(n)   = n 在 order 中的下标
      end(n)   = n 子树中最大的行号
      depth(n) = end(n) - row(n)
      heir(p)  = children(p) 中 depth 最大者，平局取 id 序最小者

      L1 heir 继承父的轨道
      L2 其余子节点取编号最小且 busy[l] < row(n) 的空闲轨道，没有就新开
      L3 分配后 busy[lane] = max(busy[lane], end(n))

    用 depth 选主线而不是"第一个子节点"：最早尝试的那条支往往是后来废掉的，
    用 depth 选等于让实际走下去的那条线留在主干，死胡同自然被甩到旁边。
    """
    row = {sid: i for i, sid in enumerate(order)}

    end: dict[str, int] = {}
    for sid in reversed(order):
        e = row[sid]
        for c in children.get(sid, ()):
            if end[c] > e:
                e = end[c]
        end[sid] = e

    heir: dict[str, str] = {}
    for p, kids in children.items():
        if not kids:
            continue
        best, best_depth = None, -1
        for c in kids:
            d = end[c] - row[c]
            if d > best_depth or (d == best_depth and id_key(c) < id_key(best)):
                best, best_depth = c, d
        heir[p] = best

    lane: dict[str, int] = {}
    busy: list[int] = []  # busy[l] = 该轨道已占用到的行号

    def take_lane(sid: str) -> int:
        for l in range(len(busy)):
            if busy[l] < row[sid]:
                busy[l] = end[sid]
                return l
        busy.append(end[sid])
        return len(busy) - 1

    for sid in order:
        p = by_id[sid].parent
        if p is not None and heir.get(p) == sid:
            l = lane[p]
            lane[sid] = l
            if end[sid] > busy[l]:
                busy[l] = end[sid]
        else:
            lane[sid] = take_lane(sid)
    return lane, end


# ---------------------------------------------------------------- backlinks


def compute_backlinks(by_id: dict[str, Step]) -> dict[str, list[str]]:
    """正文里的 [[007]] → 在 007 页面显示"被这些步骤引用"。

    这是"多父 DAG"的廉价替代品：想表达"本步综合了 A 线和 B 线"，
    在正文写一句 [[003b]] 即可，不必把森林升级成 DAG。
    """
    back: dict[str, list[str]] = {sid: [] for sid in by_id}
    for sid in sorted(by_id, key=id_key):
        for target in dict.fromkeys(_WIKILINK_RE.findall(by_id[sid].body)):
            if target in back and target != sid:
                back[target].append(sid)
    return back


def lineage(by_id: dict[str, Step], sid: str) -> list[str]:
    """从根到 sid 的 id 序列。派生，不存储。"""
    chain: list[str] = []
    seen: set[str] = set()
    cur: str | None = sid
    while cur and cur in by_id and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = by_id[cur].parent
    chain.reverse()
    return chain


# ---------------------------------------------------------------- compile


def compile_forest(steps_dir: Path, with_files: bool = True) -> dict[str, Any]:
    """完整管线。输出是确定性的——同样的目录内容永远得到同样的 dict。"""
    raw, files, w_scan = scan(steps_dir, with_files=with_files)
    by_id, w_val = validate(raw)
    children = build_children(by_id)
    order = compute_order(by_id, children)
    lane, end = compute_lanes(by_id, children, order)
    back = compute_backlinks(by_id)

    steps_out = []
    for sid in order:
        d = by_id[sid].to_dict()
        d["children"] = children.get(sid, [])
        d["backlinks"] = back.get(sid, [])
        d["files"] = files.get(sid, [])
        d["lane"] = lane[sid]
        d["row"] = len(steps_out)
        steps_out.append(d)

    return {
        "steps": steps_out,
        "order": order,
        "lanes": {sid: lane[sid] for sid in order},
        "lane_count": (max(lane.values()) + 1) if lane else 0,
        "warnings": w_scan + w_val,
        "row_h": ROW_H,
        "lane_w": LANE_W,
    }
