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

# front-matter 里可以重复出现的键（其余键重复时后写的覆盖先写的）。
#
# 后三个是「一件事发生了很多次」的记录：数据依赖可以有好几条、代码可以既有 git 又有
# 快照、移动过的步骤会被移动第二次。它们和 path/repro 一样**只追加**，所以必须能重复；
# 换成「后写覆盖先写」等于每记一条就抹掉上一条，那正是只追加要防的事。
MULTI_KEYS = ("path", "repro", "input", "code", "moved")

# path 的 role 词表。**机器字段，不翻译**——它要能被 `grep -r "| output |"` 捞出来，
# 一旦跟着界面语言走，同一个概念在磁盘上就有了两种写法（G4 下 grep 只能捞到一半）。
PATH_ROLES = ("input", "script", "output", "evidence")

# path 属性里认得出来的校验和键。顺序即优先级（派生 checksum 字段取第一个命中的）。
CHECKSUM_KEYS = ("md5", "sha256")

# code 记录的三种形态。git 之外的两种是给「代码不在 git 里」准备的：
# 快照目录 + 逐文件校验和、容器镜像 + digest，在可溯源性上不比 commit 差。
CODE_KINDS = ("git", "snapshot", "container")

# 复现记录的取值。这是**事实**，算不出来，必须存。
# 与之相对，可溯源性等级是**派生**的（从记了什么算出来），绝不存储。
REPRO_STATES = ("failed", "runnable", "verified")

# 可溯源性阶梯。前三级从文件里机械地算出来；后两级要有人真去看过、跑过。
LEVELS = ("L0", "L1", "L2", "L3", "L4")
LEVEL_LABEL = {
    "L0": "不可溯源", "L1": "可读", "L2": "可定位", "L3": "可重跑", "L4": "已复现",
}
# 小节名的中英对照，是全流程**唯一**的一份词表：评级、lint、网页取名字都从这里取。
#
# 为什么必须是一张**封闭**词表，而不是让翻译文件自由起标题：翻译文件也要被解析、
# 被评级，「这一段到底是不是『为什么』」必须机械可判。靠猜（关键词、语义相似度、
# 字符集探测）只要判错一次，一条写全了的记录就会被报成 L0——评级一旦会撒谎，
# 人就不再看它，这比没有评级更糟。
SECTION_NAMES = {
    "why":        {"zh": "为什么",   "en": "Why"},
    "what":       {"zh": "做了什么", "en": "What"},
    "result":     {"zh": "结果",     "en": "Result"},
    "conclusion": {"zh": "结论",     "en": "Conclusion"},
    "next":       {"zh": "下一步",   "en": "Next"},
}
# 中文骨架（FORMAT.md 第 2 节那份示例逐字对着它，test_docs 会核）。
SECTIONS = tuple(v["zh"] for v in SECTION_NAMES.values())
# 标题 → 语义键的反查。给要「认出一个标题是哪一节」的调用方用（网页、写入侧）。
SECTION_KEY_BY_NAME = {n: k for k, names in SECTION_NAMES.items() for n in names.values()}

# 项目洞察的四个小节，以及由系统自己写的「已删除」。同样是封闭词表，理由同上。
INSIGHT_NAMES = {
    "idea":    {"zh": "核心想法", "en": "Ideas"},
    "works":   {"zh": "有效",     "en": "Works"},
    "fails":   {"zh": "无效",     "en": "Doesn't work"},
    "pitfall": {"zh": "坑",       "en": "Pitfalls"},
}
INSIGHT_KEY_BY_NAME = {n: k for k, names in INSIGHT_NAMES.items() for n in names.values()}
DELETED_NAME = {"zh": "已删除", "en": "Deleted"}
# 「这一条取代了那一条」的连接词，同样是封闭词表（理由同 SECTION_NAMES）：
# 一条洞察被取代是**写在取代者身上**的一句话，被取代的那一条什么都不改——
# 「p1 已被取代」是派生的，写第二份就又是双真相源。
SUPERSEDE_NAMES = {"zh": "取代", "en": "supersedes"}
# 同一个对象的第二个名字，不是第二份数据：写入侧按 SUPERSEDES_WORD 从 core 导入这张表。
# 一张封闭词表在两个模块里各写一份迟早会对不上，所以宁可多一个别名，也不让它被抄走。
SUPERSEDES_WORD = SUPERSEDE_NAMES
# 洞察 id 的形状。读侧（本模块）和写侧（trace_write._INSIGHT_LINE_RE）必须是同一条，
# 否则会出现「写得进去、读不回来」的洞察。字母开头，于是 `## 已删除` 里的
# `` `009` `` 不会被当成洞察 id。
INSIGHT_ID_RE = r"[A-Za-z][A-Za-z0-9_-]{0,15}"
INSIGHT_ID_PREFIX = "p"

# ---------------------------------------------------------------- 翻译文件
#
# 存储是**双文件**：note.md 带结构 + 主语言正文，note.<lang>.md 只带 title 和译文。
# 「还没翻译」是派生状态（文件不存在），和 children / files 一样绝不存储。
TR_RE = re.compile(r"^note\.([A-Za-z][A-Za-z0-9-]{0,34})\.md\Z")
PROJECT_TR_RE = re.compile(r"^project\.([A-Za-z][A-Za-z0-9-]{0,34})\.md\Z")
TR_ONLY_KEYS = ("title",)            # 步骤的翻译文件里唯一允许的键
PROJECT_TR_ONLY_KEYS = ("name",)     # 项目的翻译文件里唯一允许的键

# 结构键：只有 note.md / project.md 说了算。翻译文件里写了也一律忽略（见 parse_translation）。
TR_STRUCT_KEYS = ("id", "parent", "status", "date", "commit", "author",
                  "tags", "path", "repro", "key", "input", "code", "moved")

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
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.*?)\s*$")
_PLACEHOLDER_RE = re.compile(r"^[（(].*?[）)]\s*$", re.S)


def _headings(body: str) -> tuple[list[str], list[tuple[int, str, int]]]:
    """正文的行 + 每个标题的 (层级, 标题文字, 内容起始行号)。sections/lint 共用。"""
    lines = (body or "").split("\n")
    heads = [(len(m.group(1)), m.group(2).strip(), i + 1)
             for i, m in ((i, _HEADING_RE.match(l)) for i, l in enumerate(lines)) if m]
    return lines, heads


def sections(body: str) -> dict[str, str]:
    """按标题切正文。**一节的内容包含它下面所有更深的标题及其内容。**

    只有层级**不深于**本节的标题才结束本节，这是 markdown 的常识语义。以前是见到
    任何标题就切一节，于是这样一份写得很全的记录会被判成「什么都没写」→ L0：

        ## 做了什么
        ### 1 · 统计口袋蛋白含量
        用 …… 统计了每个口袋里的残基组成

    `## 做了什么` 在 `### 1 · …` 那一行就被截断，内容是空的。作者只能靠猜发现问题，
    然后补一句废话引言把评级骗上去——评级一旦逼着人写废话，它就开始撒谎了。

    键仍然是**每一个**标题（包括更深的那些），只是内容变长了：这样 `# 一级标题`
    开头的笔记里，`## 为什么` 照样是自己的一个键，不会被吞进那个一级标题。
    同名标题仍然是后写覆盖先写。
    """
    lines, heads = _headings(body)
    out: dict[str, str] = {}
    for k, (lv, name, start) in enumerate(heads):
        end = len(lines)
        for lv2, _n2, s2 in heads[k + 1:]:
            if lv2 <= lv:                # 同级或更浅的标题才结束本节
                end = s2 - 1
                break
        out[name] = "\n".join(lines[start:end]).strip()
    return out


def _filled(text: str) -> bool:
    """小节有没有实质内容。模板里的占位括号不算。"""
    t = (text or "").strip()
    if not t:
        return False
    t = _PLACEHOLDER_RE.sub("", t).strip()
    return bool(t)


def _pick(sec: dict[str, str], key: str) -> str:
    """在一份切好的小节表里按语义键取内容。**全流程只有这一处做标题 → 语义的映射。**"""
    for name in SECTION_NAMES[key].values():
        t = sec.get(name, "")
        if _filled(t):
            return t
    return ""


def section_text(body: str, key: str) -> str:
    """按**语义键**（why / what / conclusion …）取小节内容，中英标题都认。

    调用方一律走这里而不是 `sections(body)["为什么"]`：note.md 本身也可能是英文写的
    （`lang: en`），硬编码中文标题会把它整篇判成「什么都没写」。
    """
    return _pick(sections(body), key)


# 一个「属性段」里的 token：`size=61203283968`、`md5=7d4e1a9c`、`missing=2026-08-09`。
# 键必须以 ASCII 字母开头——这样「位置=这里」这种中文键不会被当成机器字段。
# 值不含空白（token 本来就是按空白切出来的），允许为空（`missing=` 也解析得出来）。
_ATTR_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]*=\S*$")


def _attrs_of(segment: str) -> dict[str, str] | None:
    """一段是不是「属性段」：所有空白分隔的 token **全部**形如 k=v。不是就返回 None。

    判据故意选得这么严：`lr=3e-4 的那次运行` 里只有一个 token 是 k=v，于是整段落进
    说明——顺手在说明里写个等号不会被误当成机器字段。反过来，一段全是 k=v 就几乎
    不可能是人写给人看的说明。宁可漏认（掉回说明，字一个不少），不可错认（说明被
    吃成属性，人写的那句话就从界面上消失了）。
    """
    toks = segment.split()
    if not toks:                                   # 空段是空说明，不是「零个属性」
        return None
    if not all(_ATTR_TOKEN_RE.match(t) for t in toks):
        return None
    out: dict[str, str] = {}
    for t in toks:
        k, _, v = t.partition("=")
        out[k] = v
    return out


def _int_or_none(v: str) -> int | None:
    """属性值转整数；转不了就当没写（原值仍在 attrs 里，不丢）。"""
    v = (v or "").strip()
    return int(v) if v.isdigit() else None


def _path_state(checked: str, missing: str) -> str:
    """这条路径当前是不是还在。

    `checked=` / `missing=` 都是**存储的事实**（像 repro：某人某天真去看过一眼），
    「现在还在不在」才是派生的。两个都写着时**看日期，晚的说了算**——一条路径先
    被确认存在、后被确认消失是最常见的时间线，反过来（清掉了又被重建）也一样成立。
    同一天两个都写着时判 missing：这一整条需求的来历就是「三个目录已被删除、
    57 GB 那个，本该自动发现」——漏报一次丢失比多报一次警报贵得多。
    """
    if missing and checked:
        return "missing" if missing >= checked else "present"
    if missing:
        return "missing"
    if checked:
        return "present"
    return ""


def parse_paths(raw: str) -> list[dict[str, Any]]:
    """每行一条 `<位置> | <角色> | <说明> | <k=v …>`，除位置外全部可选、顺序随意。

    按 `|` 切开之后逐段判定：

      * 整段**恰好**是 PATH_ROLES 里的一个词  → 它是 role
      * 整段的空白 token **全部**形如 k=v      → 它们是属性
      * 其余                                   → 拼进说明（多段用 " | " 接回去）

    **向后兼容是硬要求**：现存的 `位置 | 说明` 一个字都不用改——中文说明既不是
    role 也不是纯 k=v，自然落进说明，note 拿到的和以前逐字一样。

    返回的字典在原来的 {location, note, kind} 上**只增不改**：网页和 CLI 都在读那三个键。
    """
    out: list[dict[str, Any]] = []
    for line in (raw or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        segs = line.split("|")
        loc = segs[0].strip()
        if not loc:
            continue
        role = ""
        attrs: dict[str, str] = {}
        desc: list[str] = []
        for seg in segs[1:]:
            s = seg.strip()
            if s in PATH_ROLES:
                role = s
                continue
            a = _attrs_of(s)
            if a is not None:
                attrs.update(a)                    # 同名属性后写覆盖先写
                continue
            desc.append(s)
        checksum = next((f"{k}:{attrs[k]}" for k in CHECKSUM_KEYS if attrs.get(k)), "")
        out.append({
            "location": loc,
            "note": " | ".join(desc),              # 说明里原来就允许有竖线，接回去保持逐字不变
            "kind": path_kind(loc),
            "role": role,
            # 未知属性照样留着。半年后有人写了 `nodes=…`，系统不该把它吃掉——
            # 认不认得出来是程序的事，写下来的东西是人的事。
            "attrs": dict(attrs),
            # size 存的是**字节数**（整数），格式化成「592 MB」是显示层的事。
            "size": _int_or_none(attrs.get("size", "")),
            "n": _int_or_none(attrs.get("n", "")),
            "checksum": checksum,
            "checked": attrs.get("checked", ""),
            "missing": attrs.get("missing", ""),
            "state": _path_state(attrs.get("checked", ""), attrs.get("missing", "")),
        })
    return out


def format_path(p: dict[str, Any]) -> str:
    """把一条结构化 path 还原成 note.md 里的一行（不含 `path: ` 前缀）。

    写入侧回写 front-matter 时必须走这里：只拼 `location | note` 的话，role 和
    size/md5/checked 会在**任何一次无关的编辑**里被静默抹掉——用户在网页上改一下
    标题，刚核对完的 164 条校验和就没了。

    段序归一为 `位置 | role | 说明 | 属性`。再解析一次得到同样的字典（幂等）。
    """
    segs = [str(p.get("location", "")).strip()]
    role = str(p.get("role", "") or "").strip()
    if role in PATH_ROLES:
        segs.append(role)
    note = str(p.get("note", "") or "").strip()
    if note:
        segs.append(note)
    attrs = p.get("attrs") or {}
    if attrs:
        segs.append(" ".join(f"{k}={v}" for k, v in attrs.items()))
    return " | ".join(segs)


def fmt_size(n: Any) -> str:
    """字节数 → 人读的大小。**只给显示用**，存进文件的永远是字节数。"""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return ""
    if v < 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return (f"{int(v)} {unit}" if unit == "B" or v >= 100 or v == int(v)
                    else f"{v:.1f} {unit}")
        v /= 1024
    return ""


def locations_haystack(step: dict[str, Any]) -> str:
    """一步里所有「东西在哪」的文本，拼成一串供搜索用。**只读，不存。**

    收的是 `path:` 和 `code:` 的位置与说明。为什么这也得能搜：
    「/orange/…/run042/best.pt 是哪一步产出的」「谁用了 20260809 那个快照」
    正是这两个键存在的主要用途，而 `grep -rn best.pt projects/` 一秒就答得出。
    G4 的底线是「删掉全部程序，grep 还答得了」——工具比 grep 弱的地方，
    恰好就是 agent 唯一够得到的地方：它拿到「没搜到」，会读成「没记过」。

    `input:` 的说明（消费的是哪份产物文件名）一并收进来，同一个理由。
    校验和与日期这类属性**不收**：搜一串 md5 是核对，不是找东西，
    而把它们拼进干草堆只会让「12」这种短查询命中一堆无关的步骤。

    入参是 Step.to_dict() 或 forest 里的 step（两边形状一样），
    所以服务端和 MCP 两处搜索共用这一份，不会再各写各的。
    """
    bits: list[str] = []
    for p in step.get("paths") or []:
        bits.append(str(p.get("location") or ""))
        bits.append(str(p.get("note") or ""))
    for c in step.get("code") or []:
        bits.append(str(c.get("location") or ""))
        bits.append(str(c.get("note") or ""))
    for i in step.get("inputs") or []:
        bits.append(str(i.get("note") or ""))
    return " ".join(b for b in bits if b)


def parse_inputs(raw: str) -> list[dict[str, str]]:
    """每行一条 `<步骤 id> | <消费的是哪份产物>`。

    **记录派生关系（parent）和数据依赖（input）是两件事。** 森林是单父树，数据流是
    DAG：016 的输入同时来自 013 的 pocket_composition.csv 和 014 的 rmscore_pairs.csv，
    树上只能表达其中一个。以前只能在正文里写一句「本步的输入其实来自 X」，读的人
    得自己拼；现在它是机器读得到的边（并且照样 grep 得到）。
    """
    out: list[dict[str, str]] = []
    for line in (raw or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        sid, _, note = line.partition("|")
        sid = sid.strip()
        if not sid:
            continue
        out.append({"step": sid, "note": note.strip()})
    return out


def format_input(i: dict[str, str]) -> str:
    note = str(i.get("note", "") or "").strip()
    return f"{i.get('step', '')}" + (f" | {note}" if note else "")


def parse_code(raw: str) -> list[dict[str, Any]]:
    """每行一条 `<kind> | <位置> | <k=v …>`，kind ∈ CODE_KINDS。

    为什么不止 `commit:`：代码不在 git 里的时候（超算上直接改脚本、跑完打个快照目录
    留一份逐文件校验和）「代码在这里、逐文件校验和在这里」在可溯源性上不比 commit 差。
    只认 commit 等于逼着这类记录永远停在 L1，而它们其实是找得回来的。

    未知 kind 不丢也不报错：十年后多出一种形态（`nix`、`ipfs`）是完全可能的，
    系统的本分是把人写下的东西原样留着，而不是把它判成不存在。
    """
    out: list[dict[str, Any]] = []
    for line in (raw or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        segs = [s.strip() for s in line.split("|")]
        kind = segs[0].lower()
        if not kind:
            continue
        loc = segs[1] if len(segs) > 1 else ""
        attrs: dict[str, str] = {}
        note: list[str] = []
        for seg in segs[2:]:
            a = _attrs_of(seg)
            if a is not None:
                attrs.update(a)
            elif seg:
                note.append(seg)
        out.append({"kind": kind, "location": loc, "attrs": dict(attrs),
                    "note": " | ".join(note), "from": "code"})
    return out


def format_code(c: dict[str, Any]) -> str:
    """还原成 note.md 里的一行（不含 `code: ` 前缀）。

    `from == "commit"` 的那条是**派生**的（由 `commit:` 折算出来），写入侧要跳过它，
    否则同一个事实在文件里存两份——这正是上一代系统的死因。
    """
    segs = [str(c.get("kind", "") or "").strip(), str(c.get("location", "") or "").strip()]
    note = str(c.get("note", "") or "").strip()
    if note:
        segs.append(note)
    attrs = c.get("attrs") or {}
    if attrs:
        segs.append(" ".join(f"{k}={v}" for k, v in attrs.items()))
    while len(segs) > 1 and not segs[-1]:
        segs.pop()
    return " | ".join(segs)


def parse_moved(raw: str) -> list[dict[str, str]]:
    """每行一次搬家：`日期 | 原 parent | 新 parent | 谁 | 为什么`。

    P2 的地基是「不丢历史」，不是「不能改结构」——**记下来就不丢**。于是 parent 从
    「写下不可改」变成「可改，但必须留下审计记录」，而 id 仍然不可改（笔记里的
    `[[003b]]`、论文脚注里的引用要永远有效）。

    这一行放在 front-matter 而不是正文的「## 已移动」：正文是人的判断区，一次编辑
    就可能把它误删；front-matter 是机器记录区，回写时整段重建，不会被顺手删掉。
    **顺序即历史，只追加。**
    """
    out: list[dict[str, str]] = []
    for line in (raw or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split("|")]
        if not parts[0]:
            continue
        out.append({
            "date": parts[0],
            "from": parts[1] if len(parts) > 1 else "",
            "to": parts[2] if len(parts) > 2 else "",
            "by": parts[3] if len(parts) > 3 else "",
            # 原因里再有竖线也没关系，并进原因（和 repro 的说明同一个规矩）
            "reason": " | ".join(parts[4:]) if len(parts) > 4 else "",
        })
    return out


def format_moved(m: dict[str, str]) -> str:
    return " | ".join([m.get("date", ""), m.get("from", ""), m.get("to", ""),
                       m.get("by", ""), m.get("reason", "")]).rstrip(" |")


def _copy_row(d: dict[str, Any]) -> dict[str, Any]:
    """浅拷一条记录，顺带把嵌套的 attrs 也拷一份。

    产物是只读的派生数据，但共享一个可变 dict 迟早会有人往里塞东西，而那一塞
    改的是解析结果本身（下一个读者拿到的就是被改过的「文件内容」）。一层拷贝很便宜。
    """
    out = dict(d)
    a = out.get("attrs")
    if isinstance(a, dict):
        out["attrs"] = dict(a)
    return out


def code_records(step: "Step") -> list[dict[str, Any]]:
    """这一步记下的代码位置，**含 `commit:` 折算出来的那一条**。

    `commit: c1d2e3f` 等价于 `code: git | | commit=c1d2e3f`，但它是**派生**的：
    文件里仍然只有 `commit:` 一份，绝不再写一行 `code: git`。带 `from` 字段就是为了
    让写入侧一眼分得出哪些是文件里真有的（`code`）、哪些是算出来的（`commit`）。
    """
    # getattr 兜底：写入侧会拿手工造的 Step 调 to_dict（测试、纯演算），
    # 少一个字段就抛 AttributeError，而这里的本分是「读得下来」。
    out = [_copy_row(c) for c in (getattr(step, "code", None) or [])]
    commit = (getattr(step, "commit", "") or "").strip()
    if commit and not any(c.get("kind") == "git" and (c.get("attrs") or {}).get("commit") == commit
                          for c in out):
        out.append({"kind": "git", "location": "", "attrs": {"commit": commit},
                    "note": "", "from": "commit"})
    return out


def _code_locates(rec: dict[str, Any]) -> bool:
    """这一条 code 记录到底有没有回答「代码在哪」。

    * git       —— 有 commit / rev / tag，或者记了仓库位置
    * snapshot  —— 有目录位置（`manifest=` / 校验和让它更强，但**不额外分级**，理由见 traceability）
    * container —— 有镜像位置或 digest
    * 其它 kind —— 只要记了位置就算。认不出的形态不代表它没定位到东西
    """
    kind = str(rec.get("kind", "")).lower()
    attrs = rec.get("attrs") or {}
    loc = str(rec.get("location", "") or "").strip()
    if kind == "git":
        return bool(loc or attrs.get("commit") or attrs.get("rev") or attrs.get("tag"))
    if kind == "container":
        return bool(loc or attrs.get("digest"))
    return bool(loc)


def code_located(step: "Step") -> bool:
    """L2 的「代码找得回来」判据：任何一条 code 记录定位得到就算数。"""
    return any(_code_locates(r) for r in code_records(step))

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
    paths: list[dict[str, Any]] = field(default_factory=list)
    repro: list[dict[str, str]] = field(default_factory=list)
    # 数据依赖（`input:`）。和 parent 是两件事：parent 是**记录的派生关系**（单父树），
    # inputs 是**数据流**（DAG）。一步的输入可以同时来自 013 和 014，树上只能挂一个。
    inputs: list[dict[str, str]] = field(default_factory=list)
    # 代码在哪（`code:`）。**只装文件里真有的那几行**——`commit:` 折算出来的那条是
    # 派生的，由 code_records() 现算，绝不写进这里，更不写进文件（双真相源）。
    code: list[dict[str, Any]] = field(default_factory=list)
    # parent 的移动审计（`moved:`）。顺序即历史，只追加。
    moved: list[dict[str, str]] = field(default_factory=list)
    body: str = ""
    dirname: str = ""
    # note.md 自己声明的语言（front-matter 的 `lang:`）。**没声明就是空串**，
    # 界面照实说「原文」——绝不去猜（字符集探测、语种识别）：猜错时界面会对读者
    # 撒谎（说「这是中文原文」而它其实是英文），而读者没有任何办法发现。
    lang: str = ""
    # 所有翻译，按语言码：{"en": {"title": ..., "body": ...}}。
    # 派生自 note.<lang>.md 是否存在，绝不写进 note.md（P1：文件即数据库）。
    tr: dict[str, dict[str, str]] = field(default_factory=dict)
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
            "paths": [_copy_row(p) for p in self.paths],
            "repro": [dict(r) for r in self.repro],
            "inputs": [dict(i) for i in self.inputs],
            # **派生**：含 `commit:` 折算出来的那条（带 from: "commit"）。写回文件时
            # 只许写 from == "code" 的那些，否则同一个事实在磁盘上有两份。
            "code": [_copy_row(c) for c in code_records(self)],
            "moved": [dict(m) for m in self.moved],
            "body": self.body,
            "dirname": self.dirname,
            "digest": self.digest,
            "lang": self.lang,
            # 按语言码排序输出：静态导出要求逐字节确定，扫描顺序不能漏进产物。
            "tr": {k: dict(self.tr[k]) for k in sorted(self.tr)},
        }


def warn(level: str, code: str, message: str, where: str = "",
         vars: dict[str, str] | None = None) -> dict[str, Any]:
    """一条警告。`message` 永远是中文原句，`vars` 是同一句话里那几个**变量**。

    为什么要多带一份 vars：界面要说使用者的语言，可 message 是拼好的中文。
    没有 vars 的时候，web/app.js 只能拿正则去那句中文里把 {id} / {chain} 抠回来
    ——那条正则脆得离谱：这里改一个字，英文界面上就原样漏出一句中文。
    所以凡是句子里嵌了值的警告，都把值**结构化地**给出来一份；
    抠正则退化成认不出 vars 时的退路，而不是唯一的路。

    只有真带变量的警告才会有这个键：给每条都塞一个空 dict 会让静态导出里
    几十条警告各多出四个字节，而它们一个变量都没有。
    """
    out: dict[str, Any] = {"level": level, "code": code, "message": message, "where": where}
    if vars:
        # 值一律转成字符串：JSON 里出现 int 会让前端的 "" + v 和 t() 的占位符替换
        # 走两条不同的路（一个补零一个不补），而这里没有任何数值需要参与计算。
        out["vars"] = {k: str(v) for k, v in vars.items()}
    return out


@dataclass
class Project:
    slug: str
    name: str = ""
    body: str = ""
    created: str = ""
    lang: str = ""                                              # project.md 声明的语言，同样不猜
    tr: dict[str, dict[str, str]] = field(default_factory=dict)  # {"en": {"name":…, "body":…}}

    def to_dict(self) -> dict[str, Any]:
        return {"slug": self.slug, "name": self.name or self.slug, "body": self.body,
                "created": self.created, "lang": self.lang,
                "tr": {k: dict(self.tr[k]) for k in sorted(self.tr)}}


# ---------------------------------------------------------------- 项目洞察
#
# 洞察在磁盘上就是 project.md 里的一行 `- …`，本节的函数只是**读**它。
# 洞察不单独存成结构（P1：文件即数据库），也不额外存一份「谁被谁取代了」。

_INSIGHT_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
# 行首反引号里的 id，和 `## 已删除` 里 `` `002` `` 的写法一致（那一节是系统自己写的，
# 人已经在读它了，洞察沿用同一个形状就不用再学一遍）。形状用共享的 INSIGHT_ID_RE，
# 和写侧同一条——读写各写一个正则，就会有「写得进去、读不回来」的洞察。
#
# 已知代价：`` - `fp16` 会 NaN `` 这种以行内代码开头的行，id 会被认成 fp16。
# 可以接受，因为**一个字都没丢**：raw 里是原样的整行，界面把 id 当徽章显示出来
# 也仍然读得通；而收窄形状会让写侧发出去的 id 有一部分读不回来，那是真的坏。
_INSIGHT_ID_RE = re.compile(r"^`(%s)`\s*(.*)$" % INSIGHT_ID_RE, re.S)
# `· 取代 p1` / `· supersedes p1`。只写在**取代者**身上——「p1 已被取代」是派生的。
_SUPERSEDE_RE = re.compile(
    r"\s*·\s*(?:%s)\s+([A-Za-z][A-Za-z0-9_,\s-]*)$" % "|".join(
        re.escape(v) for v in SUPERSEDES_WORD.values()))


def _parse_insight_line(text: str) -> dict[str, Any]:
    sup: list[str] = []
    m = _SUPERSEDE_RE.search(text)
    if m:
        sup = [x for x in re.split(r"[,\s]+", m.group(1).strip()) if x]
        text = text[: m.start()].rstrip()
    iid = ""
    m2 = _INSIGHT_ID_RE.match(text)
    if m2:
        iid, text = m2.group(1), m2.group(2).strip()
    return {"id": iid, "text": text, "supersedes": sup, "superseded_by": []}


def parse_insights(body: str) -> dict[str, list[dict[str, Any]]]:
    """把项目笔记的四个洞察小节读成结构。中英两套小节名都认（封闭词表）。

    每条洞察是 `- ` 开头的一行：

        - `p3` PDBFixer 误杀 944 个带修饰残基，见 [[013b]] · 取代 p1
        - `p1` PDBFixer 误杀 1,099 个

    返回 {kind: [{id, text, supersedes, superseded_by, line, raw}]}，`line` 是它在
    body 里的行号（写入侧按 id 就地改一行要用）。`superseded_by` 是**派生**的：
    磁盘上只有取代者身上那半句话，被取代的那一条一个字都不改——写第二份就又是
    双真相源，而且它一定会先漂移（人只会去改自己正在写的那一行）。

    id 在整个 project.md 内唯一（不分小节）：洞察会从「坑」变成「无效」，
    id 跟着小节走就等于换一个 id，而笔记里 `见 p3` 这样的引用要一直有效。
    没有 id 的旧行（现存数据全是）照常工作，id 为空串。
    """
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in INSIGHT_NAMES}
    lines = (body or "").split("\n")
    kind: str | None = None
    level = 0
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            lv, name = len(m.group(1)), m.group(2).strip()
            k = INSIGHT_KEY_BY_NAME.get(name)
            if k is not None:
                kind, level = k, lv
            elif kind is not None and lv <= level:
                kind = None            # 更深的子标题不结束本节（和 sections() 同一套语义）
            continue
        if kind is None:
            continue
        b = _INSIGHT_BULLET_RE.match(line)
        if b:
            item = _parse_insight_line(b.group(1))
            item["line"] = i
            item["raw"] = b.group(1)
            out[kind].append(item)
    by_id: dict[str, dict[str, Any]] = {}
    for items in out.values():
        for it in items:
            if it["id"]:
                by_id.setdefault(it["id"], it)
    for items in out.values():
        for it in items:
            for target in it["supersedes"]:
                t = by_id.get(target)
                if t is not None and t is not it:
                    t["superseded_by"].append(it["id"] or it["text"][:20])
    return out


def find_insight(body: str, iid: str) -> dict[str, Any] | None:
    """按 id 找一条洞察，返回它自己加上 `kind`。找不到返回 None。"""
    for kind, items in parse_insights(body).items():
        for it in items:
            if it["id"] and it["id"] == iid:
                return {**it, "kind": kind}
    return None


def next_insight_id(body: str, prefix: str = INSIGHT_ID_PREFIX) -> str:
    """下一个可用的洞察 id。**只增不重用**——`p1` 被取代之后它的号也不许再发出去，
    否则笔记里那句「见 p1」半年后指向的是另一条结论。"""
    used = 0
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    for items in parse_insights(body).values():
        for it in items:
            m = pat.match(it["id"] or "")
            if m:
                used = max(used, int(m.group(1)))
    return f"{prefix}{used + 1}"


def format_insight(text: str, iid: str = "", supersedes: Any = (),
                   lang: str = "zh") -> str:
    """拼出一条洞察的行内容（**不含**行首的 `- `，写入侧自己加）。

    词表之外的语言退回英文的 `supersedes`：宁可用一个读得懂的词，也不要造一个
    解析器认不回来的写法——认不回来就等于这条取代关系不存在。

    `supersedes` 收列表也收 `"p1, p2"` 这样的字符串：写入侧校验完之后手里是一个
    字符串，读侧解析出来的是一个列表，两边都会原样递到这里。只收列表的话，
    传字符串会被逐字符迭代成 `p, 1, ,, …` —— 静默产出一行谁也解析不回来的垃圾。
    """
    body = " ".join(str(text or "").split())
    head = f"`{iid}` " if iid else ""
    if isinstance(supersedes, str):
        supersedes = [x for x in re.split(r"[,，\s]+", supersedes) if x]
    ids = [str(x).strip() for x in (supersedes or ()) if str(x).strip()]
    tail = ""
    if ids:
        tail = " · " + SUPERSEDES_WORD.get(lang, SUPERSEDES_WORD["en"]) + " " + ", ".join(ids)
    return head + body + tail


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
        # 项目的译文（project.<lang>.md，只准带 name）。
        # 这里丢掉 scan_translations 的警告，是因为 scan_projects 的签名
        # （-> list[Project]）被 CLI / server / MCP / write 四处依赖，加不了警告通道；
        # 而**忽略结构键**这条硬规矩由 parse_translation 保证，和能不能报警无关。
        # 需要在写入时告诉用户「这个键会被丢掉」的话，直接调 parse_translation。
        tr, _w = scan_translations(d, PROJECT_TR_RE, PROJECT_TR_ONLY_KEYS, d.name, PROJECT_NOTE)
        out.append(
            Project(
                slug=d.name,
                name=(meta.get("name") or d.name).strip(),
                body=body,
                created=(meta.get("created") or "").strip(),
                lang=(meta.get("lang") or "").strip(),
                tr=tr,
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


def parse_translation(
    text: str, only_keys: tuple[str, ...] = TR_ONLY_KEYS, where: str = "",
    source: str = NOTE_NAME,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """解析一份翻译文件（note.<lang>.md / project.<lang>.md）。

    只取 `only_keys`（步骤是 `title`，项目是 `name`）加正文，**结构键一律丢掉**。

    这是整个双语功能最要紧的一道防线。上一代系统（ai-training-logbook）的死因是
    双真相源：父子关系同时存在于两个地方，写了一处漏另一处，页面上永远有一半是错的。
    翻译文件里的 `parent: 006` 若能生效，同一个错误原样回来——而且更隐蔽，因为
    两份值平时看着都对，只有改了其中一份才炸。所以这里不是「note.md 优先」这种
    合并策略（合并策略意味着两边都是来源），而是**读都不读**，并且吵一声让人删掉它。
    """
    meta, body, warns = parse_note(text)
    data: dict[str, str] = {k: (meta.get(k) or "").strip() for k in only_keys}
    for k in TR_STRUCT_KEYS:
        if k in meta:
            warns.append(warn(
                "warn", "translation_structural_key",
                f"翻译文件里的 `{k}:` 已被忽略——这个键在 {source} 里已经有了，"
                f"写两份就是双真相源（改一处漏一处，两边永远不知道谁对）。"
                f"结构只认 {source}，翻译文件里请只留 "
                f"{'/'.join(only_keys)} 和正文",
                where))
    data["body"] = body
    for w in warns:
        w["where"] = w["where"] or where
    return data, warns


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
        inputs=parse_inputs(meta.get("input", "")),
        code=parse_code(meta.get("code", "")),
        moved=parse_moved(meta.get("moved", "")),
        body=body,
        dirname=dirname,
        # 没写就是空串。**不做任何猜测**：字符集探测能把一段引用了中文论文标题的
        # 英文笔记判成中文，界面于是对读者说「这是原文」——错的元信息比没有元信息坏。
        lang=(meta.get("lang") or "").strip(),
    )
    return step, warnings


# ---------------------------------------------------------------- scan


def list_files(step_dir: Path) -> list[dict[str, Any]]:
    """该目录下的附件清单（递归，排除本层的 note.md / note.<lang>.md 与点开头的文件）。

    必须是派生字段——一旦写进 note 就会和实际目录漂移。

    翻译文件和 note.md 一样是**记录本身**，不是附件：漏排除的话 note.en.md 会出现在
    附件区，被当成可以下载、可以删的文件，删掉就等于悄悄删了英文版正文。
    只排本层——嵌套目录里的 note.en.md 是别人的文件（既有断言钉着 sub/note.md 要在清单里）。
    """
    out: list[dict[str, Any]] = []
    root_str = str(step_dir)
    for root, dirs, names in os.walk(step_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for n in sorted(names):
            if n.startswith("."):
                continue
            if root == root_str and (n == NOTE_NAME or TR_RE.match(n)):
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


def scan_translations(
    d: Path, pattern: re.Pattern[str], only_keys: tuple[str, ...],
    where_prefix: str = "", source: str = NOTE_NAME,
) -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    """收一个目录下的翻译文件，返回 {语言码: {title/name, body}} + 警告。

    只看**本层**，和 note.md 一样：嵌套目录里的 note.en.md 是附件，不是这一步的译文。
    语言码原样取自文件名（`zh-Hant` 的大小写有意义，不做归一化）。
    """
    out: dict[str, dict[str, str]] = {}
    warnings: list[dict[str, str]] = []
    try:
        with os.scandir(d) as it:
            # 排序是为了产物确定：目录项的原始顺序由文件系统决定，同一份数据在两台
            # 机器上可以不一样，而 P3 要求两次编译逐字节一致。
            entries = sorted((e.name for e in it if e.is_file()))
    except OSError:
        return out, warnings
    for name in entries:
        m = pattern.match(name)
        if not m:
            continue
        where = f"{where_prefix}/{name}" if where_prefix else name
        try:
            raw = (d / name).read_bytes()
        except OSError as exc:
            warnings.append(warn("error", "unreadable", f"无法读取: {exc}", where))
            continue
        text, w0 = decode_note(raw, where)
        data, w1 = parse_translation(text, only_keys, where, source)
        # 每份译文自己的摘要。**不是**为了记「译文是不是过时了」——那要存
        # note.md 当时的指纹，是把派生关系变成存储字段（P1 禁止），而且人手工
        # vim 一次 note.md 那个数就变成谎话。这里的 digest 只回答一个问题：
        # 「我读到的这一份，和我要覆盖的那一份，是同一份吗」——也就是 expect。
        # 没有它，网页首屏拿不到译文的 expect，译文的保存就退化成「谁最后按谁赢」。
        data["digest"] = hashlib.sha256(raw).hexdigest()[:12]
        out[m.group(1)] = data
        warnings.extend(w0 + w1)
    return out, warnings


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
        # 翻译和 note.md 一起扫出来，不管 with_files——译文是内容，不是附件。
        step.tr, w3 = scan_translations(entry, TR_RE, TR_ONLY_KEYS, entry.name)
        for w in w0 + w1 + w2:
            w["where"] = w["where"] or entry.name
        warnings.extend(w0 + w1 + w2 + w3)
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
    # project.md 和它的译文 project.<lang>.md 都在 steps/ 的上一级。步骤那边的
    # note.<lang>.md 不用单列——os.walk 本来就把步骤目录里的每个文件都算进去了。
    has_project_note = False
    tr_names: list[str] = []
    try:
        with os.scandir(steps_dir.parent) as it:
            tr_names = sorted(e.name for e in it if e.is_file() and PROJECT_TR_RE.match(e.name))
    except OSError:
        pass
    for name in [PROJECT_NOTE] + tr_names:
        try:
            st = (steps_dir.parent / name).stat()
        except OSError:
            continue
        h.update(name.encode("utf-8"))
        h.update(f"{st.st_mtime_ns}:{st.st_size}".encode("ascii"))
        has_project_note = True
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


def dep_edges(by_id: dict[str, Step], sid: str) -> list[str]:
    """这一步依赖谁：**记录派生**（parent）在前，**数据依赖**（inputs）在后，去重。

    顺序是产物的一部分（平局时靠前的赢，见 _worst_of），所以固定成「parent 优先」：
    树是这套系统的主干，同级别的两个最弱环里，指树上那个更容易让人找到位置。
    """
    s = by_id.get(sid)
    if s is None:
        return []
    out: list[str] = []
    for cand in ([s.parent] if s.parent else []) + [i["step"] for i in s.inputs]:
        if cand and cand != sid and cand in by_id and cand not in out:
            out.append(cand)
    return out


def compute_consumers(by_id: dict[str, Step]) -> dict[str, list[str]]:
    """谁消费了本步的产物。**派生的反向边**，和 backlinks 一个套路，绝不存储。

    正向的 `input: 013 | pocket_composition.csv` 写在消费者身上（写的人当时就知道
    自己在用谁的东西）；「013 的产物被谁用了」是扫出来的——写第二份就是双真相源。
    """
    out: dict[str, list[str]] = {sid: [] for sid in by_id}
    for sid in sorted(by_id, key=id_key):
        for target in dict.fromkeys(i["step"] for i in by_id[sid].inputs):
            if target in out and target != sid:
                out[target].append(sid)
    return out


def validate_inputs(by_id: dict[str, Step]) -> list[dict[str, str]]:
    """数据依赖的三条检查。**全部只报警，绝不中断构建**——十年后的日志一定是残缺的。

    和 dangling_parent 同一个处理方式：说出来，然后继续画图。指向不存在的 id 的
    `input:` 那一行仍然留在 inputs 里（文本一个字不改），只是不参与图。
    """
    warnings: list[dict[str, str]] = []
    for sid in sorted(by_id, key=id_key):
        s = by_id[sid]
        for i in s.inputs:
            t = i["step"]
            if t == sid:
                warnings.append(warn("warn", "self_input",
                                     f"input 指向自己（{sid}）", s.dirname, {"id": sid}))
            elif t not in by_id:
                warnings.append(warn("warn", "dangling_input",
                                     f"input 指向不存在的步骤 {t}", s.dirname, {"id": t}))

    # 环。parent 的环已经被 validate() 断掉了，所以这里报出来的环必然有 input 边参与。
    # 数据流成环是没意义的（A 的输入来自 B、B 的输入来自 A），但它同样不该让构建停下。
    color: dict[str, int] = {}
    for start in sorted(by_id, key=id_key):
        if color.get(start):
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            sid, k = stack.pop()
            if k == 0:
                if color.get(sid) == 2:
                    continue
                if color.get(sid) == 1:
                    cycle = path[path.index(sid):] if sid in path else [sid]
                    chain = " → ".join(cycle + [cycle[0]])
                    warnings.append(warn(
                        "warn", "input_cycle",
                        "数据依赖成环: " + chain
                        + "——A 的输入来自 B、B 的输入又来自 A，两边都说不清谁先算出来的",
                        by_id[sid].dirname, {"chain": chain}))
                    continue
                color[sid] = 1
                path.append(sid)
                stack.append((sid, 1))
                for d in reversed(dep_edges(by_id, sid)):
                    stack.append((d, 0))
            else:
                color[sid] = 2
                path.pop()
    # 同一个环会被每个入口各报一次，去重后仍然确定（sorted 遍历 + 固定的边序）。
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for w in warnings:
        k = (w["code"], w["message"])
        if k not in seen:
            seen.add(k)
            out.append(w)
    return out


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


def note_bodies(step: Step) -> list[tuple[str, str]]:
    """这一步的全部正文：note.md 一份，每个翻译文件一份。返回 [(文件名, 正文)]。

    顺序固定（note.md 在前，其余按语言码升序），因为它会决定警告的先后，
    而警告要进静态导出产物（P3 要求逐字节确定）。
    """
    out = [(NOTE_NAME, step.body)]
    out += [(f"note.{lang}.md", (step.tr.get(lang) or {}).get("body", ""))
            for lang in sorted(step.tr)]
    return out


def _lint_figures(step: Step) -> list[dict[str, str]]:
    """图片必须有图注。理由是这个系统有两类读者——

      * 人：半年后看到一张没有说明的曲线，认不出画的是什么；
      * agent：只读得到 `![](loss_curve.png)` 这一行，图里的信息对它是黑洞。

    图注是这张图对文本读者唯一的信息来源，所以它不是装饰，是内容。

    **图注是逐个文件独立判的**，这一点和小节检查（「任一语言写了就算写了」）相反，
    差别值得说清楚：小节问的是「这个判断有没有被记下来」——记在中文版里，它就存在，
    英文读者看不懂只是语言问题，信息没丢。图注问的是「这张图对**正在读这一份文件的
    读者**说了什么」——中文版写了图注、英文版只有一个光秃秃的 `![](loss.png)`，
    那个读英文版的人（和只被喂了英文版的 agent）拿到的就是零信息。同一张图，
    两份文件里各自是一次独立的信息传递，所以各判各的。

    单独拆出来是因为 traceability() 要用它判 captions，而 lint_body() 已经
    扩到「所有内容层缺陷」——traceability 若调 lint_body 就会把「没写结论」
    也算成图注问题，而且两者会互相递归。
    """
    out: list[dict[str, str]] = []
    for fname, body in note_bodies(step):
        where = step.dirname if fname == NOTE_NAME else f"{step.dirname}/{fname}"
        for alt, angle, bare, title in _IMG_IN_BODY.findall(body):
            src = angle or bare
            if not (alt.strip() or title.strip()):
                out.append(
                    warn("warn", "figure_without_caption",
                         f'{fname} 里的图片 {src} 没有图注。写成 ![](……  "这张图说明了什么") —— '
                         f"没有图注的话，图里的结论对文本读者和 agent 都是丢失的",
                         where)
                )
    return out


_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_IMG_ONLY_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")


def _prose_map(lines: list[str]) -> tuple[list[bool], list[bool], list[bool]]:
    """逐行判定：这一行是散文吗 / 是表格行吗 / 是围栏代码块吗。

    「散文」= 人写给人看的句子，列表项和引用都算（`- 输入改为 [CLS] …` 就是说明）。
    标题、表格行、代码块、**没有图注**的图片都不算——它们正是需要被说明的东西。

    带图注的图片**算**说明：图注本来就是一句解释性文字，而且是这一节里质量最高的
    那一句（FORMAT.md 要求它写结论）。不算的话，「一张指标表 + 一张带图注的曲线」
    这个 FORMAT.md 自己推荐的写法会被报成「没有说明」——在推荐写法上误报，
    是让人从此忽略警告最快的办法。
    """
    prose = [False] * len(lines)
    table = [False] * len(lines)
    fence = [False] * len(lines)
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            fence[i] = True
            in_fence = not in_fence
            continue
        if in_fence:
            fence[i] = True
            continue
        s = line.strip()
        if not s or _HEADING_RE.match(line):
            continue
        if _IMG_ONLY_RE.match(line):
            if any(alt.strip() or title.strip() for alt, _a, _b, title in _IMG_IN_BODY.findall(line)):
                prose[i] = True
            continue
        if s.startswith("|"):
            table[i] = True
            continue
        prose[i] = True
    return prose, table, fence


def _section_groups(lines: list[str], heads: list[tuple[int, str, int]]) -> list[int]:
    """每一行属于哪个「已知小节」（SECTION_NAMES 里的那五个），不在任何一个里就是 -1。

    分组按**最近的已知小节**而不是最近的标题：`## 结果` 下面先写一句话、再分几个
    `###` 各摆一张表，是完全正常的写法，按标题分组会对每张表各报一次「没有说明」。
    宁可少报。
    """
    group = [-1] * len(lines)
    cur, level = -1, 0
    for idx, (lv, name, start) in enumerate(heads):
        if name in SECTION_KEY_BY_NAME:
            cur, level = idx, lv
        elif cur >= 0 and lv <= level:
            cur = -1
        end = heads[idx + 1][2] - 1 if idx + 1 < len(heads) else len(lines)
        for i in range(start, end):
            group[i] = cur
    return group


def _lint_prose(step: "Step") -> list[dict[str, str]]:
    """三条**只提示、不降级**的写法诊断。

    为什么不降级：L0–L4 是「这个结果追不追得到」，而这三条问的是「读起来顺不顺」。
    把风格问题塞进等级会让等级变成风格分，人就开始为了分数写废话——这一整轮的
    起因就是有人被迫在小节下补一句引言来骗过评级。

      1. `## 做了什么` 下面只有子标题、一个字散文都没有。现在不再判 L0（那是 bug，
         已在 sections() 里修掉），但作者多半是漏写了，所以直说。
      2. 表格前后没有任何说明文字。
      3. 代码块前后没有任何说明文字。

    后两条对齐图注那条规矩的**动机**（这张表/这段命令说明了什么），但**只到提示为止**：
    图注是「图对文本读者的唯一信息来源」（不写就是零信息），而表格和代码块本身
    LLM 读得到，缺的只是一句结论。
    """
    out: list[dict[str, str]] = []
    for fname, body in note_bodies(step):
        where = step.dirname if fname == NOTE_NAME else f"{step.dirname}/{fname}"
        lines, heads = _headings(body)
        prose, table, fence = _prose_map(lines)

        for idx, (lv, name, start) in enumerate(heads):
            if name not in SECTION_KEY_BY_NAME:
                continue
            end = len(lines)
            for lv2, _n2, s2 in heads[idx + 1:]:
                if lv2 <= lv:
                    end = s2 - 1
                    break
            inner = list(range(start, end))
            has_sub = any(_HEADING_RE.match(lines[i]) for i in inner)
            has_text = any(lines[i].strip() and not _HEADING_RE.match(lines[i]) for i in inner)
            if has_sub and not has_text:
                out.append(warn(
                    "warn", "section_without_prose",
                    f"{fname} 的「{'#' * lv} {name}」下面没有正文，只有子标题——"
                    f"子标题里的内容算这一节的内容（不再判 L0），但这一节大概率是漏写了",
                    where, {"section": name}))

        group = _section_groups(lines, heads)
        explained = {group[i] for i in range(len(lines)) if prose[i]}
        seen: set[tuple[int, str]] = set()
        for i in range(len(lines)):
            kind = "table" if table[i] else ("code" if fence[i] else "")
            if not kind or group[i] in explained:
                continue
            g = group[i]
            if (g, kind) in seen:
                continue
            seen.add((g, kind))
            what = "表格" if kind == "table" else "代码块"
            hint = "这张表说明了什么" if kind == "table" else "这段命令在干什么"
            out.append(warn(
                "warn", f"{kind}_without_explanation",
                f"{fname} 里有{what}，但同一节里没有任何说明文字——"
                f"一句「{hint}」就够（只是提示，不影响 L0–L4）",
                where))
    return out


# 内容层缺陷：小节的**语义键** → (警告 code, 为什么这条缺了要紧)。
# 存语义键而不是中文标题，这样同一条检查对中文版和英文版同时成立（见 SECTION_NAMES）。
# 只对 done / dead 生效——wip 是"还在写"，对着一个刚建出来的空模板报警只会训练
# 大家忽略警告；而一旦作者宣布这一步有结果了（done）或者放弃了（dead），
# 这条记录就是最终形态，删掉全部程序之后能不能读懂它，此刻定生死（G4）。
_CONTENT_CHECKS = (
    ("why", "missing_why", "为什么做这一步——这是唯一无法从代码和数据里自动生成的字段，丢了就永远补不回来"),
    ("what", "missing_what", "做了什么——重跑要靠它，只有标题的话别人（和半年后的你）无从下手"),
    ("conclusion", "missing_conclusion", "结论——假设到底成不成立"),
)


def _all_sections(step: Step) -> list[dict[str, str]]:
    """note.md 和每个译文各切一份小节表。切好一次给所有检查用（正文解析是热点）。"""
    return [sections(body) for _, body in note_bodies(step)]


def _wrote(secs: list[dict[str, str]], key: str) -> bool:
    """这一节写了没有：**任何一种语言里写了就算写了**。

    L0–L4 问的是「这个结果追不追得到」，不是「翻译全不全」。只写了中文的记录
    照样是可溯源的；反过来，只写了英文的也是。把「没翻译」算成缺陷会得到一个
    每加一门语言就集体掉级的评级，那等于惩罚翻译。
    """
    return any(_pick(s, key) for s in secs)


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
    out = _lint_figures(step) + _lint_prose(step)
    if step.status in ("done", "dead"):
        secs = _all_sections(step)
        for key, code, why in _CONTENT_CHECKS:
            # 只有**所有**语言都缺这一节才报。中文版写了结论、英文版还没翻，
            # 结论并没有丢——报一条「没写结论」是假警报，而假警报会让人连真的一起忽略。
            if not _wrote(secs, key):
                out.append(
                    warn("warn", code,
                         f"状态是 {step.status} 却没写「{SECTION_NAMES[key]['zh']}」：{why}",
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
    secs = _all_sections(step)
    checks = {
        # 小节：任一语言写了就算写了（见 _wrote）。
        "why": _wrote(secs, "why"),
        "what": _wrote(secs, "what"),
        "conclusion": step.status == "wip" or _wrote(secs, "conclusion"),
        # 图注：逐个文件独立（见 _lint_figures）。译文里漏了图注会真的把等级压下去，
        # 这是有意的——对读英文版的人来说，那张图确实什么都没说。补一句图注的成本
        # 是一行，而缺了就是整张图的信息对一半读者不存在。
        "captions": not _lint_figures(step),
        # 「代码找得回来」而不是「记了 commit」。代码不在 git 里的时候（超算上直接改
        # 脚本、跑完打一个快照目录 + 逐文件校验和）「代码在这里、校验和在这里」在
        # 可溯源性上不比 commit 差，卡着不给 L2 只会让这类记录永远显示成追不到底。
        #
        # **有没有 manifest / 校验和不再分一级**：L2 的语义是「可定位」——东西在哪
        # 记下来了。快照目录的路径本身就回答了这个问题；逐文件校验和回答的是另一个
        # 问题（拿到的还是不是当时那一份），那属于「有人真去看过、跑过」的 L3/L4。
        # 硬塞进 L2 会造出一个机械判不清的半级，而阶梯一旦开始撒谎就没人看了。
        "code": code_located(step),
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
    if not checks["code"]:
        # 文案里留着 "commit" 这个词是有原因的：网页按一段稳定的判别子串把这些中文
        # 句子认回 i18n 的 key（web/i18n.js 的 MISSING_MATCH），认不出就原样显示中文。
        missing.append("没记 commit / 代码快照——找不回当时的代码")
    if not checks["paths"]:
        missing.append("没记产物位置——数据和权重在哪不知道")

    if not (checks["why"] and checks["what"]):
        level = "L0"
    elif not (checks["conclusion"] and checks["captions"]):
        level = "L0"
    elif checks["code"] and checks["paths"]:
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
                lineage_entries: list[dict[str, str]], via: str = "self") -> dict[str, Any]:
    """组装 trace 字段。键的顺序是产物的一部分（静态导出要逐字节一致），别动。

    新键只许**追加在末尾**，同样是因为逐字节确定这条。
    """
    return {
        "self": self_t["level"],
        "chain": worst_level,
        "weakest": worst_id,
        "missing": self_t["missing"],
        "repro": self_t["repro"],
        # lineage 是**记录派生**那条路（根 → 自己），也就是面包屑那一串。
        # 它刻意不包含数据依赖上的祖先：那是一张 DAG，摊不成一条链，硬摊出来
        # 面包屑就不再是面包屑了。链级可能低于 lineage 里的任何一环，此时
        # weakest 指的是数据依赖上的某一步，via 会说是从哪条边过去的。
        "lineage": lineage_entries,
        # 最弱一环是从哪条边找到的：self / parent（记录派生）/ input（数据依赖）。
        "via": via,
    }


def _worst_via(by_id: dict[str, Step], sid: str, worst_id: str) -> str:
    if worst_id == sid:
        return "self"
    return "parent" if worst_id in lineage(by_id, sid) else "input"


def _weakest(per: dict[str, dict[str, Any]], by_id: dict[str, Step],
             starts: Iterable[str]) -> dict[str, str]:
    """每个节点在它的**依赖闭包**（parent ∪ inputs）里最弱的那一环。

    为什么数据依赖要参与：「这一步的输入来自哪一步」正是溯源在问的那件事。
    016 的口袋组成来自 013，013 那一步连产物在哪都没记，那么 016 这个结论就是
    追不到底的——哪怕树上 016 挂在写得很全的 013b 底下。最弱一环沿着**数据**走，
    才是「这个数字是怎么来的」这条问题的答案。

    递推关系和原来一样，只是「父」换成了「所有依赖」：

        worst(n) = 各依赖的 worst 与 n 自己之中等级最低的那个

    平局规则也一字不改：**依赖赢过自己**（原来 min() 的第二关键字 chain.index 就是
    这个意思，祖先在链里下标更小），依赖之间靠前的赢（dep_edges 定死了 parent 在前）。
    在没有任何 input 的项目里，这两条合起来和旧实现逐字等价。

    inputs 可能有环（validate_inputs 会报），所以这里是三色迭代 DFS：踩到正在栈上的
    节点就跳过它的贡献——不能因为一条脏边就让整棵树算不出等级。
    """
    worst: dict[str, str] = {}
    state: dict[str, int] = {}
    for start in starts:
        if state.get(start):
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            sid, phase = stack.pop()
            if phase == 0:
                if state.get(sid):
                    continue
                state[sid] = 1
                stack.append((sid, 1))
                for d in reversed(dep_edges(by_id, sid)):
                    if not state.get(d):
                        stack.append((d, 0))
                continue
            best: str | None = None
            for d in dep_edges(by_id, sid):
                w = worst.get(d)             # 环上的节点还没算完，跳过它的贡献
                if w is None:
                    continue
                if best is None or LEVELS.index(per[w]["level"]) < LEVELS.index(per[best]["level"]):
                    best = w
            if best is None or LEVELS.index(per[sid]["level"]) < LEVELS.index(per[best]["level"]):
                best = sid
            worst[sid] = best
            state[sid] = 2
    return worst


def chain_traceability(by_id: dict[str, Step], sid: str) -> dict[str, Any]:
    """整条链的可溯源性 = 依赖闭包里最弱的一环。

    这是这套评级真正有用的地方：001 没记数据在哪，004 就算自己写得再全，
    「004 这个结论是怎么来的」依然追不到底。

    只问一个节点用这个；要问整棵树用 compute_traces()，别在循环里调本函数。
    """
    chain = lineage(by_id, sid)
    closure: list[str] = []
    stack = [sid]
    while stack:                       # 闭包 = parent ∪ inputs 一路上溯（去重，防环）
        cur = stack.pop()
        if cur in closure:
            continue
        closure.append(cur)
        stack.extend(dep_edges(by_id, cur))
    per = {i: traceability(by_id[i]) for i in closure}
    worst_id = _weakest(per, by_id, [sid])[sid]
    return _trace_dict(per[sid], worst_id, per[worst_id]["level"],
                       [{"id": i, "level": per[i]["level"]} for i in chain],
                       _worst_via(by_id, sid, worst_id))


def compute_traces(by_id: dict[str, Step], order: list[str]) -> dict[str, dict[str, Any]]:
    """一次算出所有步骤的链路可溯源性。输出与逐个调 chain_traceability 完全一致。

    为什么要有批量版本：chain_traceability 每次都把整条祖先链的 traceability
    重算一遍，深链上就是 n²/2 次正文解析（1000 步实测 17 秒，其中 500500 次
    traceability 调用占了绝大部分）。最弱一环是可递推的（见 _weakest），
    一遍 DFS 就够，每条边只走一次。

    lineage 列表用「父的列表 + 自己」增量拼出来，且各条链共享同一批条目对象：
    产物是只读的派生数据，共享不改变任何一次比较或序列化的结果，却把深链上的
    对象数从 n²/2 降到 n。
    """
    per = {sid: traceability(by_id[sid]) for sid in order}
    entry = {sid: {"id": sid, "level": per[sid]["level"]} for sid in order}
    worst = _weakest(per, by_id, order)

    lines: dict[str, list[dict[str, str]]] = {}
    out: dict[str, dict[str, Any]] = {}
    for sid in order:
        p = by_id[sid].parent
        if p is None or p not in lines:   # 根；p 不在 lines 里说明链断了，按根处理
            lines[sid] = [entry[sid]]
        else:
            lines[sid] = lines[p] + [entry[sid]]
        w = worst[sid]
        via = "self" if w == sid else ("parent" if any(e["id"] == w for e in lines[sid]) else "input")
        out[sid] = _trace_dict(per[sid], w, per[w]["level"], lines[sid], via)
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
    consumers = compute_consumers(by_id)              # 派生，不存储
    w_inputs = validate_inputs(by_id)

    traces = compute_traces(by_id, order)             # 派生，不存储

    w_lint: list[dict[str, str]] = []
    steps_out = []
    for sid in order:
        step = by_id[sid]
        w_lint.extend(lint_body(step))
        d = step.to_dict()
        d["children"] = children.get(sid, [])
        d["backlinks"] = back.get(sid, [])
        # 「谁用了本步的产物」——inputs 的反向边，和 backlinks 一样现算。
        d["consumers"] = consumers.get(sid, [])
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
        "warnings": w_scan + w_val + w_inputs + w_lint,
        "row_h": ROW_H,
        "lane_w": LANE_W,
    }
