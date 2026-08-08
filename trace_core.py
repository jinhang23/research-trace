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
PROJECT_NOTE = "project.md"
PROJECTS_DIR = "projects"
STEPS_DIR = "steps"
STATUSES = ("wip", "done", "dead")
DEFAULT_STATUS = "wip"

# 列表视图：行高与轨道宽。前端要用同一组数字，这里是唯一来源。
# 行高必须固定——轨道 SVG 和行文本是两套坐标系，只靠它对齐。
ROW_H = 28
LANE_W = 14

# 图视图：节点卡片尺寸与间距。
NODE_W = 176
NODE_H = 58
H_GAP = 20      # 同层相邻节点
V_GAP = 38      # 层与层之间
TREE_GAP = 56   # 不同的树之间
PAD = 24

_ID_RE = re.compile(r"^(\d+)([a-z]*)$")
_DIRNAME_RE = re.compile(r"^(\d+[a-z]*)_(.*)$")
_WIKILINK_RE = re.compile(r"\[\[\s*([0-9]+[a-z]*)\s*\]\]")

# front-matter 里可以重复出现的键（其余键重复时后写的覆盖先写的）
MULTI_KEYS = ("path", "repro")

# 复现记录的取值。这是**事实**，算不出来，必须存。
# 与之相对，可溯源性等级是**派生**的（从记了什么算出来），绝不存储。
REPRO_STATES = ("failed", "runnable", "verified")

# 可溯源性阶梯。前三级从文件里机械地算出来；后两级要有人真去看过、跑过。
LEVELS = ("L0", "L1", "L2", "L3", "L4")
LEVEL_LABEL = {
    "L0": "不可溯源", "L1": "可读", "L2": "可定位", "L3": "可重跑", "L4": "已复现",
}
SECTIONS = ("为什么", "做了什么", "结果", "结论", "下一步")

_HPC_RE = re.compile(r"^(/blue/|/orange/|/red/|/scratch/|/gpfs/|/lustre/|/work/)", re.I)
_WIN_RE = re.compile(r"^[a-z]:[\\/]", re.I)


def path_kind(location: str) -> str:
    """从位置字符串猜它是什么。纯展示用（决定一个徽章），猜错也不影响任何东西。"""
    s = (location or "").strip()
    low = s.lower()
    if _HPC_RE.match(low):
        return "hpc"                                   # /blue/<组>/<用户>/… 这类超算文件系统
    if "github.com" in low or low.startswith("git@"):
        return "github"
    if "gitlab.com" in low or "bitbucket.org" in low:
        return "git"
    if "dropbox" in low:
        return "dropbox"
    if "drive.google" in low or "docs.google" in low:
        return "drive"
    if low.startswith(("s3://", "gs://", "az://", "oss://", "minio://")):
        return "object"
    if any(k in low for k in ("zenodo.org", "figshare.com", "osf.io", "dataverse")):
        return "archive"
    if any(k in low for k in ("huggingface.co", "wandb.ai", "app.neptune.ai")):
        return "mlhub"
    if low.startswith(("http://", "https://")):
        return "url"
    if _WIN_RE.match(low) or s.startswith("\\\\"):
        return "local"
    return "path"


def parse_repro(raw: str) -> list[dict[str, str]]:
    """每行一次复现尝试：`结果 | 日期 | 谁 | 说明`。只追加，最后一行是当前状态。

    失败的尝试和成功的一样要留着——"试过，跑不起来，因为 checkpoint 被清了"
    本身就是溯源结论。
    """
    out: list[dict[str, str]] = []
    for line in (raw or "").split("\n"):
        parts = [x.strip() for x in line.split("|")]
        if not parts or not parts[0]:
            continue
        state = parts[0].lower()
        if state not in REPRO_STATES:
            state = "unknown"
        out.append({
            "state": state,
            "date": parts[1] if len(parts) > 1 else "",
            "by": parts[2] if len(parts) > 2 else "",
            "note": " | ".join(parts[3:]) if len(parts) > 3 else "",
        })
    return out


# 这两个模式在 sections()/_filled() 里对每一行正文都要试一次，是全流程调用次数
# 最多的正则。预编译掉 re 模块的缓存查找（实测占 compile_forest 的一成以上）。
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*?)\s*$")
_PLACEHOLDER_RE = re.compile(r"^[（(].*?[）)]\s*$", re.S)


def sections(body: str) -> dict[str, str]:
    """按 `## 小节名` 切正文。用于判断「为什么」这类小节是不是真的写了东西。"""
    out: dict[str, str] = {}
    name, buf = None, []
    for line in (body or "").split("\n"):
        m = _HEADING_RE.match(line)
        if m:
            if name is not None:
                out[name] = "\n".join(buf).strip()
            name, buf = m.group(1).strip(), []
        elif name is not None:
            buf.append(line)
    if name is not None:
        out[name] = "\n".join(buf).strip()
    return out


def _filled(text: str) -> bool:
    """小节有没有实质内容。模板里的占位括号不算。"""
    t = (text or "").strip()
    if not t:
        return False
    t = _PLACEHOLDER_RE.sub("", t).strip()
    return bool(t)


def parse_paths(raw: str) -> list[dict[str, str]]:
    """每行一条 `<位置> | <说明>`。说明是自由文本，校验和、大小、"哪台机器"都往里写。"""
    out: list[dict[str, str]] = []
    for line in (raw or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        loc, _, note = line.partition("|")
        loc = loc.strip()
        if not loc:
            continue
        out.append({"location": loc, "note": note.strip(), "kind": path_kind(loc)})
    return out

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
    paths: list[dict[str, str]] = field(default_factory=list)
    repro: list[dict[str, str]] = field(default_factory=list)
    body: str = ""
    dirname: str = ""
    # note.md 原始字节的 sha256 前 12 位。乐观并发控制用：客户端把读到的这个值
    # 当 expect 传回来，写侧（trace_write.digest_of）用同一个公式重算，对不上就 409。
    # 公式必须和 trace_write.digest_of 逐字一致，否则冲突检测会变成永久误报。
    # 内存里凭空造出来的 Step（测试、纯函数演算）没有对应文件，留空串。
    digest: str = ""

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
            "paths": [dict(p) for p in self.paths],
            "repro": [dict(r) for r in self.repro],
            "body": self.body,
            "dirname": self.dirname,
            "digest": self.digest,
        }


def warn(level: str, code: str, message: str, where: str = "") -> dict[str, str]:
    return {"level": level, "code": code, "message": message, "where": where}


@dataclass
class Project:
    slug: str
    name: str = ""
    body: str = ""
    created: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"slug": self.slug, "name": self.name or self.slug, "body": self.body, "created": self.created}


# ---------------------------------------------------------------- 项目


def projects_root(root: Path) -> Path:
    return root / PROJECTS_DIR


def project_dir(root: Path, slug: str) -> Path:
    return root / PROJECTS_DIR / slug


def steps_dir_of(root: Path, slug: str) -> Path:
    return root / PROJECTS_DIR / slug / STEPS_DIR


def ensure_layout(root: Path, default_slug: str = "default") -> str | None:
    """把旧的单项目布局（root/steps/）一次性迁移到 root/projects/<slug>/steps/。

    做成一次性迁移而不是"两种布局都支持"，是为了之后只有一条代码路径——
    双路径正是上一代系统出 bug 的形状。
    """
    legacy = root / STEPS_DIR
    base = projects_root(root)
    if legacy.is_dir() and not base.exists():
        target = base / default_slug
        target.mkdir(parents=True)
        legacy.rename(target / STEPS_DIR)
        return default_slug
    base.mkdir(parents=True, exist_ok=True)
    return None


def scan_projects(root: Path) -> list[Project]:
    """项目 = projects/ 下的一个目录。project.md 可有可无。"""
    base = projects_root(root)
    out: list[Project] = []
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir(), key=lambda p: p.name):
        if not d.is_dir() or d.name.startswith("."):
            continue
        meta: dict[str, str] = {}
        body = ""
        note = d / PROJECT_NOTE
        if note.is_file():
            try:
                # 和步骤的 note.md 走同一个解码器：GBK / UTF-16 存的中文不能静默
                # 变成一串 U+FFFD。project.md 装的是项目级沉淀（「回译一直没用」
                # 这种三次尝试后的判断），糊掉了不比糊掉一步的正文轻。
                # decode_note 的第二个返回值是警告，Project 没有承载警告的字段，
                # 所以把它折进 name —— 让人在项目列表上一眼看见，而不是无声无息。
                text, warns = decode_note(note.read_bytes(), f"{d.name}/{PROJECT_NOTE}")
                meta, body, _ = parse_note(text)
                if warns:
                    meta["name"] = (meta.get("name") or d.name).strip() + "  ⚠ 编码有问题"
            except OSError:
                pass
        out.append(
            Project(
                slug=d.name,
                name=(meta.get("name") or d.name).strip(),
                body=body,
                created=(meta.get("created") or "").strip(),
            )
        )
    return out


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
        if not k:
            continue
        if k in MULTI_KEYS and k in meta:
            meta[k] += "\n" + v          # 可重复的键累积；其余键仍然是后写覆盖先写
        else:
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
        paths=parse_paths(meta.get("path", "")),
        repro=parse_repro(meta.get("repro", "")),
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


def decode_note(raw: bytes, where: str = "") -> tuple[str, list[dict[str, str]]]:
    """把 note.md / project.md 的原始字节解成文本，解不干净就**说出来**。

    以前这里直接 errors="replace"：GBK 或 UTF-16 存的中文会静默变成一串 U+FFFD，
    页面上是乱码、warnings 里一个字都没有，而下一次任何写入都会把这些替换字符
    落盘，原始字节再也回不来。所以宁可吵：报一条 error 级警告，让人在 check 和
    网页顶栏立刻看见「这个文件不是 UTF-8，别去改它，先转码」。

    仍然返回替换后的文本而不是抛异常——残缺输入必须还能出图（构建器永不中断）。
    """
    try:
        return raw.decode("utf-8"), []
    except UnicodeDecodeError as exc:
        text = raw.decode("utf-8", errors="replace")
        return text, [warn(
            "error", "not_utf8",
            f"不是合法的 UTF-8（第 {exc.start} 字节起解不出来），已用 � 顶替。"
            f"很可能是 GBK 或 UTF-16 存的（cmd 的 echo>、PowerShell 的 Out-File、"
            f"编辑器「ANSI 另存」都会这样）。请先转成 UTF-8 再编辑，"
            f"否则下一次保存会把 � 写进文件，原文永久丢失",
            where)]


def scan(steps_dir: Path, with_files: bool = True) -> tuple[list[Step], dict[str, list[dict]], list[dict[str, str]]]:
    """读目录，找 note.md。没有 note.md 的目录静默跳过（允许临时目录共存）。

    返回的 files 以**目录名**为键，不是 step.id：id 可能重复（validate 会把后来的
    改名成 `001~dup2`），而目录名在一次扫描里天然唯一。以前用 id 做键时，两个同 id
    的目录会互相覆盖同一个键，结果 001 的附件清单里列的是另一个目录的文件。
    """
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
            raw = note.read_bytes()
        except OSError as exc:
            warnings.append(warn("error", "unreadable", f"无法读取: {exc}", entry.name))
            continue
        text, w0 = decode_note(raw, entry.name)
        meta, body, w1 = parse_note(text)
        step, w2 = build_step(entry.name, meta, body)
        step.digest = hashlib.sha256(raw).hexdigest()[:12]
        for w in w0 + w1 + w2:
            w["where"] = w["where"] or entry.name
        warnings.extend(w0 + w1 + w2)
        steps.append(step)
        files[entry.name] = list_files(entry) if with_files else []
    return steps, files, warnings


def signature(steps_dir: Path) -> str:
    """目录内容指纹，用于判断是否需要重新编译。不进入静态导出产物。

    传项目的 **steps 目录**（core.steps_dir_of(root, slug)）。同级的 project.md
    由本函数自己带上，调用方不需要多传一个路径：指纹的语义是「这个项目在磁盘上
    变了没有」，而项目洞察（需求 20）就存在 project.md 里。漏掉它的后果是
    改一条洞察不涨 version、SSE 不推送，另一台机器上的洞察面板一直是旧的。
    """
    h = hashlib.sha1()
    note = steps_dir.parent / PROJECT_NOTE
    try:
        st = note.stat()
        h.update(PROJECT_NOTE.encode("utf-8"))
        h.update(f"{st.st_mtime_ns}:{st.st_size}".encode("ascii"))
        has_project_note = True
    except OSError:
        has_project_note = False
    if not steps_dir.is_dir():
        # 没有 steps 目录时仍要区分「project.md 也没有」和「只有 project.md」，
        # 否则新建项目后写洞察照样不涨版本。
        return "empty" if not has_project_note else h.hexdigest()[:16]
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

      row(n)    = n 在 order 中的下标
      end(n)    = n 子树中最大的行号（用来判断轨道什么时候空出来）
      height(n) = n 子树的高度：叶子为 0，否则 1 + max(height(子))
      heir(p)   = children(p) 中 height 最大者，平局取 id 序最小者

      L1 heir 继承父的轨道
      L2 其余子节点取编号最小且 busy[l] < row(n) 的空闲轨道，没有就新开
      L3 分配后 busy[lane] = max(busy[lane], end(n))

    用**高度**选主线而不是"第一个子节点"：最早尝试的那条支往往是后来废掉的，
    用高度选等于让实际走下去的那条线留在主干，死胡同自然被甩到旁边。

    这里刻意不能用 `end(n) - row(n)`。O2 保证子树占连续行区间，所以那个差值恒等于
    「子树节点数 − 1」，是子树的**大小**，只在链状子树上才碰巧等于高度。两者一旦
    分道扬镳，选出来的恰好是反的：一条死胡同底下挂着 5 个一次性小试验（大小 6、
    高度 1）会赢过真正往下走的 4 步链（大小 5、高度 4），主线被甩到 lane 1。
    """
    row = {sid: i for i, sid in enumerate(order)}

    end: dict[str, int] = {}
    height: dict[str, int] = {}
    for sid in reversed(order):  # order 是前序，倒着走 = 子先于父
        e = row[sid]
        h = 0
        for c in children.get(sid, ()):
            if end[c] > e:
                e = end[c]
            if height[c] + 1 > h:
                h = height[c] + 1
        end[sid] = e
        height[sid] = h

    heir: dict[str, str] = {}
    for p, kids in children.items():
        if not kids:
            continue
        best, best_h = None, -1
        for c in kids:
            h = height[c]
            if h > best_h or (h == best_h and id_key(c) < id_key(best)):
                best, best_h = c, h
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


# ---------------------------------------------------------------- 树布局


def compute_tree(
    by_id: dict[str, Step], children: dict[str, list[str]], order: list[str]
) -> dict[str, Any]:
    """Reingold–Tilford 紧凑树布局，自上而下。纯函数，可直接对着期望坐标写断言。

    做法是经典的两件事：
      1) 后序遍历。叶子放在本层下一个空位；内部节点居中于它的子节点。
      2) 如果居中后的位置会撞上本层左边已有的节点，就把**整棵子树**右移，
         而不是只挪父节点——只挪父节点会让它不再居中于子节点。

    `next_x[d]` 记录第 d 层下一个可用的左边界，这是"不重叠"的唯一保证。
    不同的树之间留 TREE_GAP，并且在所有层上都隔开，避免两棵树互相穿插。
    """
    if not order:
        return {"nodes": {}, "w": PAD * 2, "h": PAD * 2, "node_w": NODE_W, "node_h": NODE_H,
                "h_gap": H_GAP, "v_gap": V_GAP, "pad": PAD}

    depth: dict[str, int] = {}
    for sid in order:  # order 是前序，父一定排在子之前
        p = by_id[sid].parent
        depth[sid] = 0 if p is None else depth[p] + 1

    x: dict[str, float] = {}
    next_x: dict[int, float] = {}
    # 当前这棵树在**每一层**上的公共左边界。前一棵树排完后抬高它，这样后一棵树
    # 即使更深、深到前面从没有过的层，也仍然从前一棵树的右边起排。
    tree_floor = float(PAD)

    def shift_subtree(sid: str, delta: float) -> None:
        stack = list(children.get(sid, ()))
        while stack:
            n = stack.pop()
            x[n] += delta
            d = depth[n]
            next_x[d] = max(next_x.get(d, tree_floor), x[n] + NODE_W + H_GAP)
            stack.extend(children.get(n, ()))

    def place(sid: str) -> None:
        d = depth[sid]
        floor_ = next_x.get(d, tree_floor)
        kids = children.get(sid, [])
        if not kids:
            x[sid] = floor_
        else:
            desired = (x[kids[0]] + x[kids[-1]]) / 2
            if desired >= floor_:
                x[sid] = desired
            else:
                x[sid] = floor_
                shift_subtree(sid, floor_ - desired)  # 整棵子树右移，父仍居中于子
        next_x[d] = x[sid] + NODE_W + H_GAP

    roots = [sid for sid in order if by_id[sid].parent is None]
    for root in roots:
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:  # 迭代后序，避免深链撞上 Python 递归上限
            sid, done = stack.pop()
            if done:
                place(sid)
            else:
                stack.append((sid, True))
                for c in reversed(children.get(sid, ())):
                    stack.append((c, False))
        # 让下一棵树在所有层上都躲开这一棵，两棵树不会互相穿插。
        # 注意不能只把 next_x 里**已有的键**推到 edge：那些键只是前面几棵树到达过的
        # 层，后一棵树若更深，多出来的那些层在 next_x 里没有键，会退回 PAD，
        # 从画布最左边排起，正好钻到前一棵树的下方（删除产生孤儿后多根很常见）。
        # 所以改成抬高公共下限、清空 next_x —— 对所有层一视同仁，包括还没出现过的层。
        tree_floor = max(next_x.values()) + TREE_GAP - H_GAP
        next_x.clear()

    max_depth = max(depth.values())
    nodes = {sid: {"x": round(x[sid], 2), "y": PAD + depth[sid] * (NODE_H + V_GAP), "depth": depth[sid]} for sid in order}
    return {
        "nodes": nodes,
        "w": round(max(v["x"] for v in nodes.values()) + NODE_W + PAD, 2),
        "h": PAD * 2 + (max_depth + 1) * NODE_H + max_depth * V_GAP,
        "node_w": NODE_W,
        "node_h": NODE_H,
        "h_gap": H_GAP,
        "v_gap": V_GAP,
        "pad": PAD,
    }


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


# 两种目标写法都要认：裸路径，以及 CommonMark 的 <...>。
# 后者是渲染器为了支持 `loss curve (run 42).png` 这类带空格的文件名才接的——
# 只认裸路径的话，恰恰是最容易漏图注的那批图（文件名带空格、从别处拷来的）
# 全部逃过检查：md.js 照常渲染出一张没有说明的图，check 却报一切正常。
_IMG_IN_BODY = re.compile(r'!\[([^\]]*)\]\(\s*(?:<([^>]*)>|([^)\s]+))(?:\s+"([^"]*)")?\s*\)')


def _lint_figures(step: Step) -> list[dict[str, str]]:
    """图片必须有图注。理由是这个系统有两类读者——

      * 人：半年后看到一张没有说明的曲线，认不出画的是什么；
      * agent：只读得到 `![](loss_curve.png)` 这一行，图里的信息对它是黑洞。

    图注是这张图对文本读者唯一的信息来源，所以它不是装饰，是内容。

    单独拆出来是因为 traceability() 要用它判 captions，而 lint_body() 已经
    扩到「所有内容层缺陷」——traceability 若调 lint_body 就会把「没写结论」
    也算成图注问题，而且两者会互相递归。
    """
    out: list[dict[str, str]] = []
    for alt, angle, bare, title in _IMG_IN_BODY.findall(step.body):
        src = angle or bare
        if not (alt.strip() or title.strip()):
            out.append(
                warn("warn", "figure_without_caption",
                     f'图片 {src} 没有图注。写成 ![](……  "这张图说明了什么") —— '
                     f"没有图注的话，图里的结论对文本读者和 agent 都是丢失的",
                     step.dirname)
            )
    return out


# 内容层缺陷：小节名 → (警告 code, 为什么这条缺了要紧)。
# 只对 done / dead 生效——wip 是"还在写"，对着一个刚建出来的空模板报警只会训练
# 大家忽略警告；而一旦作者宣布这一步有结果了（done）或者放弃了（dead），
# 这条记录就是最终形态，删掉全部程序之后能不能读懂它，此刻定生死（G4）。
_CONTENT_CHECKS = (
    ("为什么", "missing_why", "为什么做这一步——这是唯一无法从代码和数据里自动生成的字段，丢了就永远补不回来"),
    ("做了什么", "missing_what", "做了什么——重跑要靠它，只有标题的话别人（和半年后的你）无从下手"),
    ("结论", "missing_conclusion", "结论——假设到底成不成立"),
)


def lint_body(step: Step) -> list[dict[str, str]]:
    """内容层的提醒（不是结构不变量，永远只是 warn 级）。

    两类：图片缺图注，以及**已经收尾的步骤缺关键小节**。

    后者是 G4 的执法点。traceability() 早就把「没写结论」算进 missing 了，但那
    只在 agent 逐步读 MCP 详情时才露出来；check 和网页读的是 warnings，于是
    「点一下按钮标成 dead、一个字理由都不写」全流程零提示。半年后删掉程序、
    grep 这个目录，只剩下 `status: dead` 和一个标题，答不出「我当年为什么放弃了 X」
    ——正是 G4 要防的那一件事。所以这里把它抬进 warnings。

    级别用 warn 不用 error：这不是结构错误（树照样能建、图照样能画），
    是记录质量问题。error 留给「构建被迫改动数据」的情形（重复 id、环）。
    """
    out = _lint_figures(step)
    if step.status in ("done", "dead"):
        sec = sections(step.body)
        for name, code, why in _CONTENT_CHECKS:
            if not _filled(sec.get(name, "")):
                out.append(
                    warn("warn", code,
                         f"状态是 {step.status} 却没写「{name}」：{why}",
                         step.dirname)
                )
    return out


def traceability(step: Step) -> dict[str, Any]:
    """这一步自己的可溯源性。**派生字段，绝不存储**（P1）。

    前三级是机械可判的：

      L0 不可溯源  连「为什么」或「做了什么」都没写
      L1 可读      两者都写了、图都有图注、有结论（wip 除外，它本来就还没有结论）
      L2 可定位    L1 + 记了 commit + 记了产物位置

    再往上算不出来，必须有人真去看过、跑过，所以由 `repro:` 记录抬上去：

      L3 可重跑    有人确认过命令/环境/种子齐全（repro: runnable）
      L4 已复现    真跑过，数字对上了（repro: verified）

    `repro: failed` 不降级——"试过，跑不起来"不改变记录本身的完整度，
    但它是最该被看见的一条，所以单独作为一个状态返回。
    """
    sec = sections(step.body)
    checks = {
        "why": _filled(sec.get("为什么", "")),
        "what": _filled(sec.get("做了什么", "")),
        "conclusion": step.status == "wip" or _filled(sec.get("结论", "")),
        "captions": not _lint_figures(step),
        "commit": bool(step.commit.strip()),
        "paths": bool(step.paths),
    }
    missing = []
    if not checks["why"]:
        missing.append("没写「为什么」——这是唯一无法自动生成的字段")
    if not checks["what"]:
        missing.append("没写「做了什么」——重跑要靠它")
    if not checks["conclusion"]:
        missing.append("没写「结论」——假设到底成不成立")
    if not checks["captions"]:
        missing.append("有图没写图注——图里的信息对文本读者是黑洞")
    if not checks["commit"]:
        missing.append("没记 commit——找不回当时的代码")
    if not checks["paths"]:
        missing.append("没记产物位置——数据和权重在哪不知道")

    if not (checks["why"] and checks["what"]):
        level = "L0"
    elif not (checks["conclusion"] and checks["captions"]):
        level = "L0"
    elif checks["commit"] and checks["paths"]:
        level = "L2"
    else:
        level = "L1"

    latest = step.repro[-1] if step.repro else None
    if latest and latest["state"] == "verified":
        level = "L4"
    elif latest and latest["state"] == "runnable" and level == "L2":
        level = "L3"

    return {"level": level, "missing": missing, "checks": checks,
            "repro": dict(latest) if latest else None}


def _trace_dict(self_t: dict[str, Any], worst_id: str, worst_level: str,
                lineage_entries: list[dict[str, str]]) -> dict[str, Any]:
    """组装 trace 字段。键的顺序是产物的一部分（静态导出要逐字节一致），别动。"""
    return {
        "self": self_t["level"],
        "chain": worst_level,
        "weakest": worst_id,
        "missing": self_t["missing"],
        "repro": self_t["repro"],
        "lineage": lineage_entries,
    }


def chain_traceability(by_id: dict[str, Step], sid: str) -> dict[str, Any]:
    """整条链的可溯源性 = 链上最弱的一环。

    这是这套评级真正有用的地方：001 没记数据在哪，004 就算自己写得再全，
    「004 这个结论是怎么来的」依然追不到底。

    只问一个节点用这个；要问整棵树用 compute_traces()，别在循环里调本函数。
    """
    chain = lineage(by_id, sid)
    per = {i: traceability(by_id[i]) for i in chain}
    worst_id = min(chain, key=lambda i: (LEVELS.index(per[i]["level"]), chain.index(i)))
    return _trace_dict(per[sid], worst_id, per[worst_id]["level"],
                       [{"id": i, "level": per[i]["level"]} for i in chain])


def compute_traces(by_id: dict[str, Step], order: list[str]) -> dict[str, dict[str, Any]]:
    """一次算出所有步骤的链路可溯源性。输出与逐个调 chain_traceability 完全一致。

    为什么要有批量版本：chain_traceability 每次都把整条祖先链的 traceability
    重算一遍，深链上就是 n²/2 次正文解析（1000 步实测 17 秒，其中 500500 次
    traceability 调用占了绝大部分）。而「链上最弱一环」是可递推的：

        worst(n) = worst(parent(n)) 与 n 自己之中等级更低者，平局取更靠近根的那个

    平局取祖先，正是原来 min() 的第二关键字 chain.index —— 祖先在链里下标更小。
    order 是前序（父一定排在子前面），所以自顶向下扫一遍就够，每步 O(1)。

    lineage 列表用「父的列表 + 自己」增量拼出来，且各条链共享同一批条目对象：
    产物是只读的派生数据，共享不改变任何一次比较或序列化的结果，却把深链上的
    对象数从 n²/2 降到 n。
    """
    per = {sid: traceability(by_id[sid]) for sid in order}
    entry = {sid: {"id": sid, "level": per[sid]["level"]} for sid in order}

    worst: dict[str, str] = {}
    lines: dict[str, list[dict[str, str]]] = {}
    out: dict[str, dict[str, Any]] = {}
    for sid in order:
        p = by_id[sid].parent
        if p is None or p not in lines:   # 根；p 不在 lines 里说明链断了，按根处理
            worst[sid] = sid
            lines[sid] = [entry[sid]]
        else:
            wp = worst[p]
            worst[sid] = wp if LEVELS.index(per[wp]["level"]) <= LEVELS.index(per[sid]["level"]) else sid
            lines[sid] = lines[p] + [entry[sid]]
        out[sid] = _trace_dict(per[sid], worst[sid], per[worst[sid]]["level"], lines[sid])
    return out


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

    traces = compute_traces(by_id, order)             # 派生，不存储

    w_lint: list[dict[str, str]] = []
    steps_out = []
    for sid in order:
        step = by_id[sid]
        w_lint.extend(lint_body(step))
        d = step.to_dict()
        d["children"] = children.get(sid, [])
        d["backlinks"] = back.get(sid, [])
        # 用目录名取附件，不用 id：两个目录写了同一个 id 时，validate 只改得动
        # 后一个的 id（→ 001~dup2），而附件是按目录扫出来的，用 id 做键会把
        # 001 的清单换成 001~dup2 那个目录的文件（点开 404，自己的附件消失）。
        d["files"] = files.get(step.dirname, [])
        d["lane"] = lane[sid]
        d["row"] = len(steps_out)
        d["trace"] = traces[sid]
        steps_out.append(d)

    return {
        "steps": steps_out,
        "order": order,
        "lanes": {sid: lane[sid] for sid in order},
        "lane_count": (max(lane.values()) + 1) if lane else 0,
        "tree": compute_tree(by_id, children, order),
        "warnings": w_scan + w_val + w_lint,
        "row_h": ROW_H,
        "lane_w": LANE_W,
    }
