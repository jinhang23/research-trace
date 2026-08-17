"""trace_core — 纯函数内核。

    scan → parse → validate → order → lanes → compile

约束（不可妥协）：
  * 除标准库外零依赖；
  * 除 scan/signature 读盘外无副作用；
  * 同样的输入永远产出同样的输出（静态导出要求逐字节一致）；
  * 派生字段（files / children / backlinks / lineage）一律计算得到，绝不存储。

布局算法（order / lanes）是最容易写错的部分，因此被写成不碰 IO 的纯函数，
可以直接对着期望结果写断言，不需要跑渲染。

定稿流程的**派生**（compute_pipeline）在这里；它的三个**导出**
（Methods 草稿 / SVG 图 / 独立页面）**不在这里**，在 trace_mcp.py 的「三个导出」
那一节，理由写在那儿。要改导出请去那一份——CLI、服务端、静态导出都调它，
在这里再写一份就等于让论文里那张图和网页上那张图各活各的。
"""

from __future__ import annotations

import hashlib
import heapq
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

# 一条父子边**是什么意思**。树上所有边长得一样，但它们表达的关系不同：
#   extends      我接着上一步往下做（绝大多数，默认，不写就是它）
#   alternative  我是同一个问题的一个候选，和兄弟里其他 alternative **只能选一条走下去**
#
# 为什么写在**孩子**身上而不是在兄弟之间互相登记（`alt: 012b` 那种）：互斥是一组
# 关系，登记在兄弟之间就要写 N×(N−1) 份，改一处漏一处——同一个事实存在多处正是
# 上一代系统的死因。每个候选只声明「我是一个候选」，**这一组有谁**是扫父节点的
# 孩子现算出来的（compute_branch_groups），绝不存储。
BRANCH_KINDS = ("extends", "alternative")
DEFAULT_BRANCH = "extends"

# 一组候选的三种结局。**由 status 派生**，不另存一个「选中了谁」的字段：
# 「选了 A」写下来就是「B 标 dead」，两处都写就是双真相源。
#   decided    只剩一个非 dead 的候选 —— 就是它了
#   abandoned  一个不剩 —— 整条路都走不通，这**是结论不是错误**（P4）
#   open       还有两个以上活着 —— 这个岔路口还没做决定
BRANCH_STATES = ("open", "decided", "abandoned")

# 定稿流程上，**某一步自己**的例外声明（`pipeline:` 写在 note.md 里）：
#   include  闭包够不到它，但它确实是流程的一环
#   exclude  探索性的、成功了但没进最终流程
#
# 和 `branch:` 同一个套路：声明写在**这一步自己**身上，绝不在项目上列一份成员清单。
# 清单一旦落盘就是一份会漂移的中心索引（P1 禁止）——移动一步、补一条 `input:`、
# 把某支标 dead，那份清单立刻过期，而且没有任何机制会告诉你它过期了。
PIPELINE_RULES = ("include", "exclude")

# 章节（`chapter:` 写在开启那条线的步骤上，**沿树继承**）。
#
# 项目本来就是森林，多个根已经给了「独立的探索路径」；章节做的只是给它们**起名字**，
# 于是「主实验」「消融实验」各自能当一个单元来看、各自有自己的定稿流程和可溯源等级。
#
# **没有词表**：章节名是人起的名字，不是枚举。所以这里没有 CHAPTER_KINDS 这种常量
# ——一旦收窄成词表，「消融实验」这种名字就得先注册再用，而注册表就是中心索引（P1 禁止）。
#
# 两件**刻意不做**的事，写在这里免得后来人以为是漏了：
#   1. **id 不按章节重编号。**消融不从 001 重新开始。id 是分配顺序不是章节内序号，
#      而 `[[007]]` 和论文脚注要在整个项目里唯一——这是只追加的地基。
#   2. **章节不嵌套。**名字里可以写 `主实验/数据准备`，显示时按 `/` 分组，
#      但语义上仍然是一层：真正的树形章节要么变成第二棵树（和步骤树打架），
#      要么逼出一份父子关系表（又是中心索引）。
CHAPTER_SEP = "/"

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
                  "tags", "path", "repro", "key", "input", "code", "moved",
                  # branch / decision 是**结构**（这条边什么意思、这个岔路口在问什么），
                  # 不是正文。译文里写一份，页面就会按不同的边画同一棵树的两个版本。
                  "branch", "decision",
                  # result / pipeline 同理，而且后果更重：它们决定**哪些步骤进定稿流程**。
                  # 译文里写一份，中文页面和英文页面会导出两条不同的 Methods，
                  # 而两边看着都像对的——正是双真相源最难查的那一种。
                  "result", "pipeline",
                  # chapter 是结构里最能悄悄分家的一个：它沿树继承，所以译文里多写
                  # 一行不是「这一步换了个章节」，是**整条子树**在英文页面上换了归属，
                  # 于是同一个项目按两种分法各导出一份 Methods。章节名要 grep 得到
                  # （G4），译名想有的话是界面的事，不是文件里的第二份声明。
                  "chapter")

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


def fork_haystack(step: dict[str, Any]) -> str:
    """一步里所有和分叉有关的**人写的散文**，拼成一串供搜索用。**只读，不存。**

    收两样：`decision:`（这个岔路口在决定什么）和 `branch:` 竖线右边那句说明
    （这个候选自己的角度）。它们和标题、正文一样是人写的自然语言，不是取值——
    `alternative` / `extends` 这两个**取值**故意不收，否则搜任何一个词都会命中
    半棵树。

    为什么这也得能搜：`grep -rn "类别不平衡" projects/` 一秒就能答出「当年是在哪个
    岔路口纠结这件事」，而站内搜索答不出就等于比 grep 弱——G4 的底线正是「删掉
    全部程序，grep 还答得了」。更要命的是 agent 只够得到工具那一侧：它拿到「没
    搜到」会读成「没记过」，然后重新纠结一遍同一个已经做过的决定。

    `decision:` 尤其不能漏：它是整套东西里唯一推导不出来、只能人写的一句话
    （候选有谁、选中了谁都算得出来）。唯一那份靠人的信息搜不到，是最亏的一种。

    入参是 Step.to_dict() 或 forest 里的 step（两边形状一样），
    服务端 / MCP / 网页三处搜索共用这一份，不会再各写各的。
    """
    bits = [str(step.get("decision") or ""), str(step.get("branch_note") or "")]
    return " ".join(b for b in bits if b)


def chapter_haystack(step: dict[str, Any]) -> str:
    """这一步**自己写下的**那一行 `chapter:`（名字 + 那句说明），供搜索用。**只读，不存。**

    收的是「写在这一步身上的那几个字」，不是它继承来的归属。判据就是 grep：
    `grep -rn "消融实验" projects/` 命中的是**声明它的那一步**那一个文件，
    继承来的二十步文件里一个「消融」都没有。站内搜索照着 grep 来，于是搜「消融」
    得到的是「这条线从哪儿开始的」那一条，而不是二十条一模一样的命中——
    后者会把真正的答案埋掉，人下次就不再用搜索框。

    章节名和它那句说明是**人写的散文**（不像 branch 的取值来自词表），
    和标题、`decision:` 同一档：这一块当年是怎么想的，只写在那半句话里。

    入参是 forest 里的 step（`chapter` 是 `{name, declared, note}`）或
    Step.to_dict()（**没有** `chapter` 键 —— 那个字段刻意不进 to_dict，
    见 Step.chapter 的注释）。拿不到就回空串：没有章节的项目搜索行为一个字不变。
    """
    ch = step.get("chapter")
    if not isinstance(ch, dict):
        return ""
    # `declared` 才是「这一行写在这一步身上」；`name` 是归属（多半是继承来的）。
    # 拿 name 当判据，一个章节的二十个成员会集体命中——那是二十条噪音。
    bits = [str(ch.get("name") or "") if ch.get("declared") else "",
            str(ch.get("note") or "")]
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


def parse_branch(raw: str) -> dict[str, str]:
    """`branch: alternative | 说明` → `{"kind": "alternative", "note": "说明"}`。

    沿用整个 front-matter 的 `位置 | 说明` 惯例：竖线左边是机器读的取值，右边是
    这个候选自己的角度（「先试最便宜的那条」）。说明里再有竖线一律留着不动——
    那是人写的字，重新拼装等于替人改文案。

    **不判合法性**：未知取值要报出是哪个步骤写的（这里拿不到 dirname），
    所以校验留给 build_step，和 `status:` 走同一条路——报一声、退回默认值、
    继续建树。一个拼错的词不该让这一步从图上消失。
    """
    kind, _, note = str(raw or "").partition("|")
    return {"kind": kind.strip().lower(), "note": note.strip()}


def format_branch(b: dict[str, str]) -> str:
    """还原成 note.md 里的一行（不含 `branch: ` 前缀）。

    是否**省略**默认值那一行由写入侧决定（`branch: extends` 和不写是同一个意思，
    存后者等于把一个派生默认值写死进文件）；这里只管拼字符串。
    """
    kind = str(b.get("kind") or "").strip()
    note = str(b.get("note") or "").strip()
    return f"{kind} | {note}" if note else kind


def parse_pipeline(raw: str) -> dict[str, str]:
    """`pipeline: exclude | 探索性的，成功了但没进最终流程` → `{"rule", "note"}`。

    和 `branch:` 逐字同一个套路（竖线左边给机器、右边给人），因为它们回答的是同一类
    问题：**这一步自己**声明一件只有它说得清的事。区别只在于问的是哪件事——
    `branch:` 说「我和兄弟只能选一条」，`pipeline:` 说「我进不进最终那条流程」。

    **不判合法性**：未知取值要报出是哪个步骤写的（这里拿不到 dirname），
    留给 build_step，和 `status:` / `branch:` 同一条路。
    """
    rule, _, note = str(raw or "").partition("|")
    return {"rule": rule.strip().lower(), "note": note.strip()}


def format_pipeline(p: dict[str, str]) -> str:
    """还原成 note.md 里的一行（不含 `pipeline: ` 前缀）。"""
    rule = str(p.get("rule") or "").strip()
    note = str(p.get("note") or "").strip()
    return f"{rule} | {note}" if note else rule


def parse_chapter(raw: str) -> dict[str, str]:
    """`chapter: 消融实验 | 逐个拿掉模块，对着主实验的 023 比` → `{"name", "note"}`。

    竖线右边是**这个章节**的说明，不是这一步的（这一步的说明是 title 和正文）。
    多处声明同一个章节时该信哪一句由 compute_chapters 裁决（按 id 序最早的那句），
    这里只管切字符串。

    和 `branch:` / `pipeline:` 的**唯一分歧：名字不转小写、不做任何归一化。**
    那两个是词表里的取值（机器读），章节名是人起的名字：`grep -r "chapter: 消融实验"`
    要能原样捞到写下去的那几个字（G4）。归一化一次，磁盘上的字和程序认的字就分了家。

    空名字 = 没声明（`chapter:` 独占一行、或者只写了竖线右边的说明）。
    「写了说明却没写名字」由 build_step 报一声——那半句话是人写的字，
    静静吞掉它比报错更坏。
    """
    name, _, note = str(raw or "").partition("|")
    return {"name": name.strip(), "note": note.strip()}


def format_chapter(c: dict[str, str]) -> str:
    """还原成 note.md 里的一行（不含 `chapter: ` 前缀）。

    名字空、只有说明时给的是 `| 说明` 而不是 ` | 说明`：那是**写坏的那一行**
    （build_step 会为它报 bad_chapter），而写入侧要能把它原样写回去——多一个
    前导空格，人写的那一行就在下一次保存后变了样，`grep -r "chapter: | 逐个"`
    也就再也捞不到它。
    """
    name = str(c.get("name") or "").strip()
    note = str(c.get("note") or "").strip()
    if not note:
        return name
    return f"{name} | {note}" if name else f"| {note}"


def parse_results(text: str) -> list[dict[str, str]]:
    """从 **project.md 全文**里取出每一行 `result: <步骤 id> | <这是什么成果>`。

    「哪一步是成果」是整个定稿流程里唯一推导不出来的事，所以它是全项目唯一要人写
    的一行；成员清单一个字都不存（存了就是会漂移的中心索引，P1 禁止）。

    为什么不走 `parse_note` 拿 `meta["result"]`：那边只有 MULTI_KEYS 里的键会累积，
    而 MULTI_KEYS 是 **note.md** 的「可以重复的键」那张表（FORMAT.md 第 2 节逐字钉着
    它）。`result:` 是 project.md 的键，混进那张表会让文档和实现说两件不同的事。
    所以这里自己扫 front-matter 的原始行——切法和 parse_note 共用 `_split_front_matter`，
    不会在「BOM 算不算」这种细节上和它分家。
    """
    out: list[dict[str, str]] = []
    for raw in _split_front_matter(text)[0]:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        k, sep, v = s.partition(":")
        if not sep or k.strip().lower() != "result":
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        # `id | 说明` 的语法和 `input:` 逐字相同，所以直接借它的解析器：
        # 两处各写一遍，迟早会在「说明里再有竖线怎么办」上分家。
        out.extend(parse_inputs(v))
    return out


def format_result(r: dict[str, str]) -> str:
    """还原成 project.md 里的一行（不含 `result: ` 前缀）。"""
    return format_input(r)


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
    # 这条**父子边**是什么意思（`branch:`）：extends 普通延伸 / alternative 互斥候选。
    # 声明在孩子身上，因为「我是不是一个候选」只有我自己说得清；「这一组候选有谁」
    # 是扫兄弟现算的（compute_branch_groups），绝不存储。
    branch: str = DEFAULT_BRANCH
    branch_note: str = ""
    # 这个节点底下的分叉**在决定什么**（`decision:`）。和「为什么」一样是这套系统里
    # 少数无法自动生成的字段之一：候选有哪些、选中了谁都算得出来，唯独「当时在纠结
    # 什么」推导不出来。半年后看到两条并排的支线，没有这句话就只剩猜。
    decision: str = ""
    # 这一步在**定稿流程**上的例外（`pipeline:`）：include 拉进来 / exclude 剔出去 /
    # 空串就是不声明（绝大多数）。默认那条路是算出来的（从 `result:` 沿 input 反向做
    # 闭包），这两个值只在算错的时候才写——所以它和 `branch:` 一样声明在自己身上，
    # 项目那边永远不出现一份成员清单。
    #
    # **刻意不进 to_dict()**：一个 result 都没声明的项目（现存全部如此）
    # 不该因为这一轮改动多出一个字段值。要看这一步在不在流程里，读
    # compile_forest 给出的 step["pipeline"]（只在项目声明了成果时才有）。
    pipeline: str = ""
    pipeline_note: str = ""
    # 这一步开启的**章节**（`chapter:`）：主实验 / 消融实验 / …，空串就是没声明。
    # 只装**这一步自己写的那个名字**——「我属于哪个章节」是沿 parent 继承出来的
    # （resolve_chapters），绝不存储：写进每一步就是 20 份会漂移的拷贝，
    # 而移动一步之后它们会集体过期且没人发现。
    #
    # `chapter_note` 是**这个章节**的说明（不是这一步的）。同一个章节在好几处声明
    # 时，只有 id 序最早的那句会生效（compute_chapters），但每一处写的字都留着——
    # 那是人写的字，程序不替人合并。
    #
    # **刻意不进 to_dict()**，理由和 pipeline 那两行一字不差：一个 `chapter:`
    # 都没写的项目（现存全部如此）不该因为这一轮改动多出一个字段值。要问
    # 「这一步属于哪个章节」，读 compile_forest 给出的 step["chapter"]。
    chapter: str = ""
    chapter_note: str = ""
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
            "branch": self.branch,
            "branch_note": self.branch_note,
            "decision": self.decision,
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


def _split_front_matter(text: str) -> tuple[list[str], str, str]:
    """切出 (front-matter 的原始行, 正文, 切不干净时的原因码)。原因码为空即切好了。

    单独抽出来是因为有**两个**读者：`parse_note` 要的是「键 → 值」，而 `parse_results`
    要的是「所有 `result:` 行」（同一个键可以重复，折成一个值就丢了一半）。两处各写
    一遍切法，迟早会在「BOM 算不算」「\\r\\n 算不算」上分家——而分家之后是
    **某些文件里的 `result:` 读不出来**，静默少几步，比报错难查得多。
    """
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return [], text.strip("\n"), ("no_front_matter" if text.strip() else "")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:]).strip("\n"), ""
    return [], text.strip("\n"), "unclosed_front_matter"


def parse_note(text: str) -> tuple[dict[str, str], str, list[dict[str, str]]]:
    """拆 front-matter 和正文。

    刻意不用 YAML：`title: 试了 3:1 采样` 这种标题在 YAML 里是语法错误，
    而这类标题在科研记录里非常常见。这里的规则是"冒号左边是键、右边整行是值"，
    对本用途更健壮，且零依赖。
    """
    warnings: list[dict[str, str]] = []
    raw_lines, body, bad = _split_front_matter(text)
    if bad == "no_front_matter":
        warnings.append(warn("warn", "no_front_matter", "缺少 front-matter，全部内容当作正文"))
        return {}, body, warnings
    if bad == "unclosed_front_matter":
        warnings.append(warn("warn", "unclosed_front_matter", "front-matter 没有闭合的 ---，全部内容当作正文"))
        return {}, body, warnings

    meta: dict[str, str] = {}
    for raw in raw_lines:
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

    # `branch:` 和 `status:` 走同一条路：未知取值报一声、退回默认值、继续建树。
    # 拼错一个词（`alternatives` / `alt`）不该让这一步从图上消失，也不该让整份
    # 记录变成解析失败——记录留着、边照画，只是暂时按普通延伸画。
    b = parse_branch(meta.get("branch", ""))
    branch, branch_note = b["kind"], b["note"]
    if not branch:
        branch = DEFAULT_BRANCH
    elif branch not in BRANCH_KINDS:
        warnings.append(warn("warn", "bad_branch",
                             f"未知 branch {branch!r}，回退到 {DEFAULT_BRANCH}（可用取值："
                             + " / ".join(BRANCH_KINDS) + "）",
                             dirname, {"branch": branch}))
        branch = DEFAULT_BRANCH

    # `pipeline:` 同样走「报一声、当没写、继续建树」那条路。当没写的后果是这一步
    # 按默认规则参与流程（在闭包里就进、是 dead 就剔），而不是从流程里消失——
    # 一个拼错的词不该悄悄改掉论文 Methods 里有哪几步。
    # 说明（竖线右边）**留着**：那是人写的字，取值拼错了也不该跟着丢。
    pl = parse_pipeline(meta.get("pipeline", ""))
    pipeline, pipeline_note = pl["rule"], pl["note"]
    if pipeline and pipeline not in PIPELINE_RULES:
        warnings.append(warn("warn", "bad_pipeline",
                             f"未知 pipeline {pipeline!r}，当没写（可用取值："
                             + " / ".join(PIPELINE_RULES) + "）",
                             dirname, {"pipeline": pipeline}))
        pipeline = ""

    # `chapter:` 没有词表可校验（名字是人起的），唯一认得出来的写坏法是
    # **只写了说明、没写名字**：`chapter: | 逐个拿掉模块`。那一行看着像声明了章节，
    # 实际上一个字都不生效，而这一步会静静继承 parent 的章节——人以为自己开了一条
    # 新线，页面上它却仍在主实验里。所以报一声（和 bad_branch / bad_pipeline 同一条路：
    # 报一声、当没写、继续建树），说明**原样留在文件里**，不动。
    ch = parse_chapter(meta.get("chapter", ""))
    chapter, chapter_note = ch["name"], ch["note"]
    if not chapter and chapter_note:
        warnings.append(warn("warn", "bad_chapter",
                             "`chapter:` 只写了竖线右边的说明、没写章节名，这一行不算声明"
                             "（这一步仍然继承 parent 的章节）。写法是 "
                             "`chapter: <章节名> | <这个章节是干什么的>`",
                             dirname, {"note": chapter_note}))

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
        branch=branch,
        branch_note=branch_note,
        # 自由文本，不做任何词表约束：「在决定什么」是人话，不是枚举。
        decision=(meta.get("decision") or "").strip(),
        pipeline=pipeline,
        pipeline_note=pipeline_note,
        chapter=chapter,
        chapter_note=chapter_note,
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


# -------------------------------------------------- 分叉：候选组与汇回边
#
# 树上的父子边有三种含义，这一节把后两种从「长得都一样」里分出来：
#   1. 普通延伸  branch: extends（默认）—— 什么都不用算
#   2. 互斥候选  同一个父节点底下所有 branch: alternative 的孩子构成**一组**
#   3. 汇回      某条支线的产物被另一条线上的步骤 `input:` 消费 —— 数据早就有了
#
# 三种全是**派生**的：磁盘上只有每个孩子自己那句 `branch:` 和消费者自己那行
# `input:`，「这一组有谁」「谁选中了」「哪条 input 边是汇回」一律扫出来现算。


def _copy_group(g: dict[str, Any]) -> dict[str, Any]:
    """候选组要同时挂在 forest["branch_groups"] 和分叉点那个 step 上。

    共享同一个 dict 意味着谁改了一处两处都变——派生结果本该是只读的，但拦不住，
    所以宁可拷一份。组很小（几个 id），代价可以忽略。
    """
    out = dict(g)
    out["options"] = list(g.get("options") or [])
    out["live"] = list(g.get("live") or [])
    return out


def compute_branch_groups(
    by_id: dict[str, Step], children: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """派生出所有「候选组」：一个节点底下所有 `branch: alternative` 的孩子算一组。

    「选了哪个」不需要新字段——**其余候选标 dead 就是选择本身**。于是白拿一个
    这套系统最需要的派生信号：一组里还有两个以上没标 dead ⇒ 这个岔路口**还没决定**。
    研究者最想知道的就是「我手上还有几个岔路口悬着」。

    根节点之间也算一组（`at` 为空串）：两条互斥的开局没有共同的父节点可挂，
    不给它成组等于让那两句 `branch: alternative` 悄悄失效——写了却什么都不发生，
    比报错更难查。这一组自然没有 `decision:`（没有节点能承载那句话）。
    """
    groups: list[dict[str, Any]] = []
    # 根之间那一组的父是「没有父」，用空串表示；其余按父节点 id。
    buckets: list[tuple[str, list[str]]] = [
        ("", sorted((sid for sid, s in by_id.items() if not s.parent), key=id_key))
    ]
    buckets += [(p, children[p]) for p in sorted(children, key=id_key)]

    for at, kids in buckets:
        options = [c for c in kids if by_id[c].branch == "alternative"]
        if not options:
            continue
        live = [c for c in options if by_id[c].status != "dead"]
        if len(live) == 1:
            state, chosen = "decided", live[0]
        elif not live:
            # 全废也是一种结论（P4），不是错误：这个问题的答案是「都不行」。
            state, chosen = "abandoned", ""
        else:
            state, chosen = "open", ""
        groups.append({
            "at": at,
            "decision": (by_id[at].decision if at else ""),
            "options": options,
            "live": live,
            "state": state,
            "chosen": chosen,
        })
    return groups


def validate_branches(
    by_id: dict[str, Step], groups: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """四条 warn 级提醒，一条都不降级——分叉的写法有问题时树照样画得出来。"""
    out: list[dict[str, str]] = []

    # `decision:` 写了，底下却一个 `branch: alternative` 都没有。
    #
    # 这是 fork_without_decision 的**镜像**，而且后果更重：那一条至少还有两条并排的
    # 支线摆在图上，人看得见「这里有个岔路口，只是没写在决定什么」；这一条写下去
    # 之后**什么都不发生**——不成组、不进 branch_groups、图上没有括弧、清单里也没有。
    # 写的人刚敲完那句话就在界面上找不到它，只会以为没保存成功，然后重写一遍或者
    # 干脆放弃。而它偏偏是整套东西里唯一推导不出来、只能人写的那一句。
    #
    # 判据只看这一步自己的孩子，不看孙子：`decision:` 说的就是「**我底下**那个岔路口」。
    forks_at = {s.parent for s in by_id.values() if s.branch == "alternative" and s.parent}
    for sid in sorted(by_id, key=id_key):
        s = by_id[sid]
        if not s.decision or sid in forks_at:
            continue
        out.append(warn(
            "warn", "decision_without_candidates",
            f"{sid} 上写着 `decision:`（在决定什么），底下却没有任何一步声明自己是候选。"
            "这一行现在还不构成岔路口——不成组、图上没有括弧、`forks` 清单里也不出现。"
            "给每条候选各写一行 `branch: alternative` 它就立起来了；"
            "要是这里其实没有分叉，把 `decision:` 那一行删掉",
            s.dirname, {"id": sid}))

    for g in groups:
        at, options = g["at"], g["options"]
        # where 指到能改的那个文件：有父节点就指父节点（decision 写在那里），
        # 根之间那一组只能指第一个候选。
        where = by_id[at].dirname if at else by_id[options[0]].dirname
        opts = " / ".join(options)

        if len(options) == 1:
            out.append(warn(
                "warn", "lone_alternative",
                f"这一组候选只有一个（{options[0]}）：一个候选不成其为选择。"
                "多半是另一条支漏标了 `branch: alternative`，"
                "或者它其实是普通延伸（把这一行改成 extends 或删掉）",
                where, {"id": at, "option": options[0]}))

        # 根之间那一组跳过：`decision:` 得写在分叉点上，而它没有分叉点，
        # 报一条改不动的警告只会训练人忽略警告。
        elif at and not g["decision"]:
            out.append(warn(
                "warn", "fork_without_decision",
                f"{at} 底下有 {len(options)} 条并列的候选（{opts}），却没写 `decision:`。"
                "「当时在决定什么」是推导不出来的——候选有谁、选中了谁都算得出来，"
                "唯独这句话只能人写。半年后看到两条并排的支线，没有它就只剩猜",
                where, {"id": at, "n": len(options), "options": opts}))

        if g["state"] == "open":
            live = " / ".join(g["live"])
            out.append(warn(
                "warn", "undecided_fork",
                f"这个岔路口还没定：{len(g['live'])} 条候选（{live}）都还活着。"
                "同时开几条线是研究的常态，不是错——只是等哪条走通了，"
                "记得把其余的标成 dead 并写清为什么放弃，这个岔路口才算结掉",
                where, {"id": at, "n": len(g["live"]), "options": live}))
    return out


def _ancestry(by_id: dict[str, Step], order: list[str]) -> tuple[dict[str, int], dict[str, str]]:
    """每个节点的深度和它所属的根。order 是前序，父必定排在子之前。"""
    depth: dict[str, int] = {}
    root: dict[str, str] = {}
    for sid in order:
        p = by_id[sid].parent
        if p is None:
            depth[sid], root[sid] = 0, sid
        else:
            depth[sid], root[sid] = depth[p] + 1, root[p]
    return depth, root


def _lca(by_id: dict[str, Step], depth: dict[str, int], a: str, b: str) -> str:
    """最近公共祖先。只在 a、b 同属一棵树时调用，否则不会终止。"""
    while depth[a] > depth[b]:
        a = by_id[a].parent                                  # type: ignore[assignment]
    while depth[b] > depth[a]:
        b = by_id[b].parent                                  # type: ignore[assignment]
    while a != b:
        a = by_id[a].parent                                  # type: ignore[assignment]
        b = by_id[b].parent                                  # type: ignore[assignment]
    return a


def compute_merges(
    by_id: dict[str, Step], order: list[str]
) -> list[dict[str, Any]]:
    """哪些 `input:` 边是**汇回**：一条支线的产物，参与了另一条线上的某一步。

    判据只有两条，都只看树的形状，机械可判：

      1. 两端在**同一棵树**里（有共同祖先）；
      2. 谁都不是谁的祖先（LCA 既不是生产者也不是消费者）。

    满足 ⇒ 汇回，并带上它们分开的那个岔路口（LCA）；否则一律是**普通数据依赖**。

    为什么是这两条：
    * 生产者在消费者的**祖先链上**时，这条数据依赖和树边走的是同一条路，树已经把
      它画出来了；再叠一条曲线只是把主干描粗一遍。
    * 谁都不是谁的祖先，就意味着两端在 LCA 处分了家、各走各的，而字节却从一边流到
      了另一边——这正是「支线的产物汇回主路径」那件事。**不需要知道哪条是主线**：
      heir 是列表视图挑轨道用的几何启发式，拿它当语义会让「哪条边是汇回」跟着排版变。
    * 不同的树之间没有共同祖先，两端从来就没在同一条线上过，谈不上「汇回」；
      跨项目/孤儿造成的那些边老实算成普通数据依赖，不硬凑。

    刻意**不**看行序：`order` 是按 id 的前序遍历，不是时间轴。011 分叉出 012/012b，
    012b 底下的 013 汇回 012 底下的 014 时，前序是 011 012 014 012b 013 ——
    生产者 013 的行号反而比消费者 014 大。拿行序当"先后"会把这个最典型的汇回判掉。
    """
    depth, root = _ancestry(by_id, order)
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for sid in order:                       # 按行序遍历消费者：输出顺序确定（P3）
        for i in by_id[sid].inputs:
            src = (i.get("step") or "").strip()
            if not src or src == sid or src not in by_id:
                continue                    # 悬空 / 指向自己：validate_inputs 已经报过
            if root[src] != root[sid]:
                continue                    # 两棵树，从来没在同一条线上
            at = _lca(by_id, depth, src, sid)
            if at == src or at == sid:
                continue                    # 祖先链上：树边已经画出这条路了
            rec = seen.get((src, sid))
            if rec is None:
                rec = {"from": src, "to": sid, "at": at, "notes": []}
                seen[(src, sid)] = rec
                out.append(rec)
            note = (i.get("note") or "").strip()
            if note and note not in rec["notes"]:
                rec["notes"].append(note)   # 同一对之间可以有好几行 input:
    return out


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


# -------------------------------------------------------------- 定稿流程
#
# 同一批文件上的**两条路径**：
#
#   开发路径  现在这棵树的全部——含走不通的、含岔路口。给自己和查问题用。
#   定稿流程  真正产出成果的那一条链。给别人照着做、给论文 Methods 用。
#
# 全项目只声明**一件推导不出来的事**：哪一步是成果（project.md 的 `result:`）。
# 其余全部派生：从成果沿 `input:` 反向做闭包，剔掉 dead，应用每一步自己的
# `pipeline: include / exclude`。**成员清单一个字都不存**——存了就是一份会漂移的
# 中心索引（P1 禁止）：移动一步、补一条 `input:`、把某支标 dead，流程要自己跟着变，
# 而一份落盘的清单只会理直气壮地列着已经不对的东西。

# 一个 result 都没声明时说的话。**教怎么办，不责备**：大多数项目一开始就是这个状态，
# 「你还没声明成果」写成缺陷只会让人为了让界面干净随手指一步当成果，那是拿假结论换绿色。
PIPELINE_EMPTY_HINT = (
    "这个项目还没声明成果，所以推不出定稿流程——这是常态，不是缺陷。"
    "想要一条能给别人照着做、能直接写进论文 Methods 的流程，"
    "在 project.md 的 front-matter 里写一行 "
    "`result: <步骤 id> | <这是什么成果>`（可以写好几行，一个成果一行）。"
    "剩下的全是算出来的：从成果沿 `input:` 反向做闭包（一步没写 `input:` 时"
    "退回它的 parent），剔掉 dead。哪些步骤算流程的一员，永远不用你维护。"
)


def pipeline_deps(by_id: dict[str, Step], sid: str) -> list[str]:
    """定稿流程的闭包沿哪条边上溯：有 `input:` 就**只**沿 input，一条都没有才退回 parent。

    **为什么这个退路是对的。**`input:` 回答的正是「这些字节从哪来」，所以它当然是
    首选。但绝大多数步骤不写 `input:`——不是因为没有输入，而是因为输入就是上一步，
    写出来是废话。真按「没写 input 就没有上游」算，现存每一个项目的定稿流程都只剩
    成果那一步，这个功能等于不存在。退回 parent 就是把「我接着上一步做」读成
    「我吃了上一步的产物」，在一条直链上这两句话说的是同一件事。

    **它什么时候会把不该来的拉进来。**parent 是「我当时接着哪一步**想**」，不保证
    有字节流过来。016 挂在 013b 底下只是因为想法承接自那个判定，数据却是从磁盘上
    另拿的——此时闭包会把 013b 连同它整条祖先链拖进 Methods，而其中一个字节都没参与
    这个结果。两条出路，都在记录里说清楚而不是靠程序猜：给 016 补一行真正的 `input:`
    （首选，顺带把数据流图也修对了），或者在 013b 上写一行 `pipeline: exclude`。

    还有一种更隐蔽的情况：`input:` 全都指向不存在的步骤（悬空）。此时这一步**算是
    声明过**输入，不退回 parent，闭包就在这里断掉。这是有意的——悬空的 `input:`
    已经由 `dangling_input` 报出来了，再拿 parent 顶上等于替人猜一个来源。
    """
    s = by_id.get(sid)
    if s is None:
        return []
    if s.inputs:
        out: list[str] = []
        for i in s.inputs:
            t = (i.get("step") or "").strip()
            if t and t != sid and t in by_id and t not in out:
                out.append(t)
        return out
    p = s.parent
    return [p] if p and p in by_id else []


def _pipeline_members(by_id: dict[str, Step], closure: Iterable[str],
                      result_ids: set[str]) -> tuple[list[str], list[dict[str, str]]]:
    """闭包 → 成员清单，外加「被剔掉的是谁、为什么」。

    优先级只有三条，从具体到一般：

      1. 声明成果的那一步**永远在流程里**——`result:` 说的就是「这是产出」，
         比任何一般规则都具体。（它同时写了 exclude 时另报一条诊断，见 compute_pipeline。）
      2. 这一步自己的 `pipeline: include / exclude` 赢过默认规则——那是人当场写下的判断。
      3. 默认：`dead` 剔掉。dead 是**结论**（P4），不是错误，但一条被放弃的路
         本来就不该出现在「照着做」的说明书里。
    """
    members: list[str] = []
    dropped: list[dict[str, str]] = []
    for sid in sorted(closure, key=id_key):
        rule = by_id[sid].pipeline
        if sid in result_ids or rule == "include":
            members.append(sid)
        elif rule == "exclude":
            dropped.append({"step": sid, "why": "declared"})
        elif by_id[sid].status == "dead":
            dropped.append({"step": sid, "why": "dead"})
        else:
            members.append(sid)
    return members, dropped


def _pipeline_edges(by_id: dict[str, Step], members: list[str]) -> list[dict[str, Any]]:
    """成员之间的边。被剔掉的那些步骤**接过去**，不留断口。

    020 是 dead、023 吃了 020 的产物、020 又吃了 015 的：剔掉 020 之后如果连边一起
    丢掉，015 就成了一个和成果毫无关系的孤点，读的人只会以为它是多出来的。
    015 的字节确实流进了 023（只是路上经过一段已经放弃的路），所以边照接，
    并在 `via` 里记下路过了谁——诊断②会点名 020，`via` 让人一眼看出它卡在哪两步之间，
    也是「从流程跳回开发路径」的锚点。

    `kind` 是**第一跳**的来路（input / parent），因为那是这条边在文件里的依据；
    `notes` 只在直连（没有 via）且来自 `input:` 时才有——那一行说明写的是
    「我消费了谁的哪份产物」，接过一段之后它描述的就不再是这条边了。
    """
    member_set = set(members)
    index: dict[tuple[str, str], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for m in members:                       # members 按 id 序 ⇒ 输出顺序确定（P3）
        kind = "input" if by_id[m].inputs else "parent"
        for first in pipeline_deps(by_id, m):
            # 宽度优先：同一个成员被好几条路径够到时，`via` 取最短的那条。
            # seen 同时是环的刹车——数据依赖成环时这里必须能停下来。
            queue: list[tuple[str, list[str]]] = [(first, [])]
            seen: set[str] = set()
            while queue:
                node, via = queue.pop(0)
                if node in seen:
                    continue
                seen.add(node)
                if node not in member_set:
                    for nxt in pipeline_deps(by_id, node):
                        queue.append((nxt, via + [node]))
                    continue
                if node == m:
                    continue                # 绕一圈回到自己：不画自环
                key = (node, m)
                edge = index.get(key)
                if edge is None:
                    edge = {"from": node, "to": m, "kind": kind,
                            "via": list(via), "notes": []}
                    index[key] = edge
                    out.append(edge)
                if not via and kind == "input":
                    for i in by_id[m].inputs:
                        n = (i.get("note") or "").strip()
                        if (i.get("step") or "").strip() == node and n and n not in edge["notes"]:
                            edge["notes"].append(n)
    return out


def _pipeline_order(members: list[str], edges: list[dict[str, Any]]
                    ) -> tuple[list[str], list[str]]:
    """按数据依赖拓扑排序，平局按 id 序。返回 (顺序, 排不进去的那些)。

    平局必须有个说法，否则同一份文件在两台机器上能排出两种顺序，而 Methods 的
    段落顺序会跟着变——P3 要求逐字节确定，所以就近取 id 序（也正是人写记录的顺序）。
    """
    indeg = {m: 0 for m in members}
    adj: dict[str, list[str]] = {m: [] for m in members}
    for e in edges:
        adj[e["from"]].append(e["to"])
        indeg[e["to"]] += 1
    # 小根堆而不是「每轮重排一遍队列」：入度归零的先后不该影响结果，而重排在
    # 长流程上是 n² log n。堆里放 (id_key, id)，取出来的永远是 id 最小的那个。
    ready = [(id_key(m), m) for m in members if indeg[m] == 0]
    heapq.heapify(ready)
    out: list[str] = []
    while ready:
        cur = heapq.heappop(ready)[1]
        out.append(cur)
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                heapq.heappush(ready, (id_key(nxt), nxt))
    done = set(out)
    return out, [m for m in members if m not in done]


def _pipeline_chapters(seeds: list[dict[str, Any]], chapter_of: dict[str, str],
                       order: list[str], levels: dict[str, str],
                       by_id: dict[str, Step]) -> list[dict[str, Any]]:
    """把算好的那**一张** DAG 按章节切开，而不是给每个章节各算一遍。

    `result:` 指的是某一步，那一步的章节就决定了这条流程属于哪个章节——主实验一条
    Methods、消融一条，论文里本来就是两段。

    **为什么是切分而不是各算一份。**各算一份就要跑 N 遍闭包，而 N 个闭包必然相交
    （同一份清洗好的数据集既喂了主结果也喂了消融），于是同一步在 N 份派生里各算一遍
    ——「同一个事实只有一份」是这套系统的地基，多算一遍就是给漂移开了口子。
    切分则是同一张图的 N 个视图：一步一定在 order 里的同一个位置。

    `external` 是这条流程里**不属于本章节**的成员：消融吃着主实验的 023，那 023
    和它的上游当然要出现在消融的 Methods 里（一个输入不在流程里的成员，写进 Methods
    就是一句断了的话），但它们是**借来的**，导出时该标出来——那正是「消融是对着
    主结果测的」这句话在图上的样子。
    """
    groups: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    # 章节的先后跟着 `result:` 在 project.md 里的**声明顺序**：那是作者自己排的
    # 论文段落顺序，比任何一种 id 序都更接近他想要的 Methods 结构。
    for s in seeds:
        name = chapter_of.get(s["step"], "")
        g = index.get(name)
        if g is None:
            g = {"name": name, "results": [], "_members": set()}
            index[name] = g
            groups.append(g)
        g["results"].append(s["step"])
        g["_members"].update(s["members"])
    out: list[dict[str, Any]] = []
    for g in groups:
        members = [sid for sid in order if sid in g["_members"]]   # 仍按全局拓扑序
        weakest = ""
        for sid in members:
            if not weakest or LEVELS.index(levels[sid]) < LEVELS.index(levels[weakest]):
                weakest = sid
        out.append({
            "name": g["name"],
            "results": g["results"],
            "order": members,
            "external": [sid for sid in members if chapter_of.get(sid, "") != g["name"]],
            "level": levels[weakest] if weakest else "",
            "weakest": weakest,
            "weak": [sid for sid in members if levels[sid] in ("L0", "L1")],
            "dead": [sid for sid in members if by_id[sid].status == "dead"],
        })
    return out


def compute_pipeline(by_id: dict[str, Step],
                     results: list[dict[str, str]],
                     chapter_of: dict[str, str] | None = None) -> dict[str, Any]:
    """派生定稿流程。**派生字段，绝不存储**（P1）。

    `results` 是 project.md 里那几行 `result:`（parse_results 的产物）。

    `chapter_of` 是每一步属于哪个章节（resolve_chapters 的产物）。**不传就整个
    `chapters` 键都不出现**——一个 `chapter:` 都没写的项目，它的流程必须和这一轮
    改动之前逐字节一样，理由和「没声明成果就没有 pipeline 键」一字不差。

    **多个成果合成一张 DAG，不是几条独立的链。** 两个成果的闭包几乎一定相交
    （同一份清洗好的数据集喂了主结果和消融），拆成几条链就会把同一步在图上和
    Methods 里各画一遍，读的人得自己对着 id 去重——而「同一个事实只有一份」正是
    这套系统的地基。所以 `order` / `edges` 是合并后的一张图；同时每个成果各自带一份
    `members`（它在这张图上的祖先集合，仍按全局拓扑序），于是「Methods 里一个成果
    写一节」和「一张总图」都不用再算第二遍。
    """
    diags: list[dict[str, Any]] = []
    empty: dict[str, Any] = {
        "declared": False, "results": [], "order": [], "edges": [], "why": {},
        "levels": {}, "level": "", "weakest": "", "weak": [], "dead": [],
        "excluded": [], "included": [], "diagnostics": [],
    }
    declared = [r for r in results if (r.get("step") or "").strip()]
    if not declared:
        # 只在**有人主动问起**定稿流程时才说这句话。它绝不进 forest["warnings"]：
        # 现存项目一个 result 都没声明，把它挂进全局警告栏等于每个项目每次打开
        # 都被念一遍，而那正是让人从此不看警告的做法。
        out = dict(empty)
        out["diagnostics"] = [warn("info", "pipeline_no_result", PIPELINE_EMPTY_HINT)]
        if chapter_of is not None:
            out["chapters"] = []
        return out

    # 成果：按声明顺序，重复声明同一步只算一次（第二行没有新信息）。
    seeds: list[dict[str, str]] = []
    seen_seed: set[str] = set()
    for r in declared:
        sid = r["step"].strip()
        if sid not in by_id:
            diags.append(warn(
                "warn", "dangling_result",
                f"`result: {sid}` 指向不存在的步骤，这个成果推不出流程。"
                "多半是 id 写错了，或者那一步被删了（删除记录在 project.md 的「已删除」里）",
                PROJECT_NOTE, {"id": sid}))
            continue
        if sid in seen_seed:
            continue
        seen_seed.add(sid)
        seeds.append({"step": sid, "note": (r.get("note") or "").strip()})

    included = [sid for sid in sorted(by_id, key=id_key) if by_id[sid].pipeline == "include"]

    # 闭包：从成果和每个 `pipeline: include` 的步骤一起反向走。include 的步骤也当种子，
    # 是因为「它确实是流程的一环」这句话必然连带它的上游——一个输入不在流程里的
    # 成员，写进 Methods 就是一句断了的话。
    closure: set[str] = set()
    stack = [s["step"] for s in seeds] + list(included)
    while stack:
        cur = stack.pop()
        if cur in closure:
            continue
        closure.add(cur)
        stack.extend(pipeline_deps(by_id, cur))

    result_ids = {s["step"] for s in seeds}
    members, dropped = _pipeline_members(by_id, closure, result_ids)
    edges = _pipeline_edges(by_id, members)
    order, stuck = _pipeline_order(members, edges)
    if stuck:
        chain = " / ".join(sorted(stuck, key=id_key))
        diags.append(warn(
            "warn", "pipeline_cycle",
            f"这几步的数据依赖成环，排不出先后：{chain}。"
            "流程照样给出来（环上的按 id 序排在最后），但「先做哪一步」这个问题"
            "在记录里本身就没有答案，写进 Methods 之前得先把环拆掉",
            PROJECT_NOTE, {"ids": chain, "n": len(stuck)}))
        order = order + sorted(stuck, key=id_key)

    # 等级复用 traceability()，不另起一套判据。取**成员自己**的等级而不是整链等级：
    # chain 会把非成员的祖先（正是被剔掉的 dead / exclude）算进来，而流程说的就是
    # 「那些不算方法的一部分」。
    levels = {sid: traceability(by_id[sid])["level"] for sid in order}
    weakest = ""
    for sid in order:
        if not weakest or LEVELS.index(levels[sid]) < LEVELS.index(levels[weakest]):
            weakest = sid
    level = levels[weakest] if weakest else ""

    dead = [sid for sid in sorted(closure, key=id_key) if by_id[sid].status == "dead"]
    if dead:
        names = " / ".join(dead)
        diags.append(warn(
            "warn", "pipeline_dead_step",
            f"成果依赖着已经放弃的路：{names} 标着 dead，却在成果的上游。"
            "dead 的默认不进定稿流程（被剔掉的那几步，上游照样接进来了），"
            "但「我的结果建立在一条我自己判定走不通的路上」是必须说出来的事——"
            "要么那个 dead 下错了，要么这个依赖该换一步",
            PROJECT_NOTE, {"ids": names, "n": len(dead)}))

    weak = [sid for sid in order if levels[sid] in ("L0", "L1")]
    if weak:
        names = " / ".join(weak)
        diags.append(warn(
            "warn", "pipeline_weak_step",
            f"流程里有 {len(weak)} 步别人跑不起来：{names} 还停在 L0/L1"
            f"（整条流程的等级 = 最弱的一步 = {level}，卡在 {weakest}）。"
            "补记录要从这几步补起，不是从最新那一步补起——投稿前该补的就是它们",
            PROJECT_NOTE, {"ids": names, "n": len(weak), "level": level, "id": weakest}))

    # `pipeline: exclude` 却被流程里的步骤吃着产物：两句话互相矛盾，程序不替人裁决。
    member_set = set(members)
    consumed: dict[str, list[str]] = {}
    for m in members:
        for d in pipeline_deps(by_id, m):
            if d not in member_set and by_id[d].pipeline == "exclude":
                consumed.setdefault(d, []).append(m)
    for sid in sorted(consumed, key=id_key):
        eaters = " / ".join(consumed[sid])
        diags.append(warn(
            "warn", "pipeline_excluded_consumed",
            f"{sid} 写着 `pipeline: exclude`（不进流程），"
            f"可流程里的 {eaters} 正吃着它的产物。这两句话不能同时成立："
            "要么那行 exclude 该删掉（它其实是流程的一环），"
            f"要么 {eaters} 的 `input:` 指错了步骤",
            by_id[sid].dirname, {"id": sid, "ids": eaters, "n": len(consumed[sid])}))

    for sid in sorted(result_ids, key=id_key):
        if by_id[sid].pipeline == "exclude":
            diags.append(warn(
                "warn", "pipeline_excluded_result",
                f"{sid} 既被 project.md 声明成成果，自己又写着 `pipeline: exclude`。"
                "按「成果永远在流程里」处理（否则这条流程连终点都没有），"
                "但两行字里一定有一行是旧的，删掉那一行",
                by_id[sid].dirname, {"id": sid}))

    # 每一步**凭什么在流程里**。派生自上面那几样，但单独给出来，是因为它是读者
    # 第一个会问的问题（「这一步凭什么算进 Methods」），而从 edges 里反查一遍
    # 要挑「哪个下游算数」——那个挑法必须确定，放在这里挑一次胜过每个出口各挑一次。
    # 优先级和 _pipeline_members 一致：声明的成果 > 人手 include > 被谁吃了。
    first_eater: dict[str, tuple[str, str]] = {}
    for e in edges:                                   # edges 按消费者 id 序 ⇒ 取到的是最小的那个
        first_eater.setdefault(e["from"], (e["kind"], e["to"]))
    why: dict[str, dict[str, str]] = {}
    for sid in order:
        if sid in result_ids:
            why[sid] = {"kind": "result", "id": ""}
        elif by_id[sid].pipeline == "include":
            why[sid] = {"kind": "include", "id": ""}
        else:
            kind, eater = first_eater.get(sid, ("", ""))
            why[sid] = {"kind": kind, "id": eater}

    # 每个成果各自的成员：在合并图上从它反向可达的那些，仍按全局拓扑序输出。
    parents_of: dict[str, list[str]] = {}
    for e in edges:
        parents_of.setdefault(e["to"], []).append(e["from"])

    def _upstream(start: str) -> list[str]:
        seen: set[str] = set()
        st = [start]
        while st:
            cur = st.pop()
            if cur in seen:
                continue
            seen.add(cur)
            st.extend(parents_of.get(cur, []))
        return [sid for sid in order if sid in seen]

    seeds_out = [{"step": s["step"], "note": s["note"], "members": _upstream(s["step"])}
                 for s in seeds]

    out: dict[str, Any] = {
        "declared": True,
        "results": seeds_out,
        "order": order,
        "edges": edges,
        "why": why,
        "levels": levels,
        "level": level,
        "weakest": weakest,
        "weak": weak,
        "dead": dead,
        "excluded": dropped,
        "included": included,
        "diagnostics": diags,
    }
    # 位置固定在最后（dict 的插入顺序就是 JSON 里的顺序，静态导出要逐字节一致）。
    # 没有章节时整个键不出现——不是「算一份只有一组的清单挂上去」。
    if chapter_of is not None:
        out["chapters"] = _pipeline_chapters(seeds_out, chapter_of, order, levels, by_id)
    return out


def scan_results(steps_dir: Path) -> list[dict[str, str]]:
    """读 steps/ 同级的 project.md，取出全部 `result:`。读不到就当没声明。

    为什么在这里读盘、而不是让调用方传进来：`compile_forest` 的入参只有 steps 目录，
    而「哪一步是成果」是**项目级**的事实，写在 project.md 里（steps/ 的上一级）。
    `signature()` 早就把 project.md 算进目录指纹了，所以改一行 `result:` 会照常
    涨版本、推 SSE、触发重编译，不需要再加一条通知路径。

    编码坏掉时这里不报警：同一个文件的 `not_utf8` 由 scan_projects 那条路报过一次了，
    同一件事说两遍只会让人开始略过警告。
    """
    note = steps_dir.parent / PROJECT_NOTE
    try:
        raw = note.read_bytes()
    except OSError:
        return []
    text, _w = decode_note(raw, PROJECT_NOTE)
    return parse_results(text)


# ---------------------------------------------------------------- 章节
#
# 一个项目内部还要分子章节：主实验、消融实验、数据准备——它们各有独立的探索路径。
# 森林（多个根）早就给了「独立的路径」，缺的只是给它们**起名字**、当作一个单元来看。
#
# 落盘的只有一行 `chapter: 消融实验 | …`，写在**开启那条线的那一步**上，
# **沿树继承**：整条子树跟着它，不用给 20 步各写一遍。其余全部派生：
# 有哪些章节、各自有谁、各自的根、各自的等级、跨章节的边，一律扫出来现算。
#
# 为什么继承而不是每步各写一份：每步各写就是 20 份拷贝，移动一步之后它们集体过期，
# 而**没有任何机制会告诉你它们过期了**——这正是上一代系统的死因。继承的另一半好处
# 是 `moved:` 之后章节自动跟着变：把一条线从主实验挪进消融，改的是 parent 一个字。
#
# 一个章节可以**横跨多棵树**：消融可能是好几条独立的根。所以章节是**一组步骤**，
# 不必是一棵子树——这也是它不能用「子树 = 章节」来实现的原因。


def resolve_chapters(by_id: dict[str, Step]) -> dict[str, str]:
    """每一步属于哪个章节：自己声明的，没有就沿 parent 一路往上找。**派生，绝不存储**。

    返回只装**进了章节的**那些步骤（`{id: 章节名}`）。一路到根都没有声明的不出现在
    这里，调用方 `.get(sid, "")` 拿到空串——那是「未分章」，是绝大多数项目的状态，
    不是错。

    两件必须做对的事：

    * **不死循环。**validate 之后 parent 已经无环、也不悬空，但这个函数也会被直接
      拿去算手工造出来的 by_id（测试、演算），而十年后的日志一定是残缺的。所以
      环和悬空都当「这条链到此为止」处理：算出「未分章」，绝不中断。
    * **记忆化。**1000 步的项目不能每步都重爬一遍祖先链（那是 n²/2 次跳跃，深链上
      正是 compute_traces 当初栽的那个跟头）。爬一次把**整条路径**上的步骤一起写进
      memo，于是每个节点只被走一次。
    """
    # 一个 `chapter:` 都没写时连爬都不用爬——现存项目全是这个状态，这条让它们
    # 一次跳跃都不多做（「完全无感」不只是输出上的，也是开销上的）。
    if not any(s.chapter for s in by_id.values()):
        return {}

    memo: dict[str, str] = {}
    for start in sorted(by_id, key=id_key):        # 定序：memo 的填法不该影响结果，但确定的遍历更好复现
        if start in memo:
            continue
        path: list[str] = []                       # 一路上没自己声明的那些，等着接同一个答案
        seen: set[str] = set()                     # 环的刹车
        cur: str | None = start
        while cur is not None and cur in by_id and cur not in memo and cur not in seen:
            seen.add(cur)
            s = by_id[cur]
            if s.chapter:
                memo[cur] = s.chapter
                break
            path.append(cur)
            cur = s.parent
        val = memo.get(cur, "") if cur else ""     # 到根 / 悬空 / 踩到环：都是未分章
        for sid in path:
            memo[sid] = val
    return {sid: name for sid, name in memo.items() if name}


def chapter_crossings(by_id: dict[str, Step], of: dict[str, str],
                      seq: Iterable[str]) -> list[dict[str, str]]:
    """跨章节的边：`parent` 跨过去的、`input:` 跨过去的。

    这不是要藏起来的脏东西，正相反——消融当然要吃主实验的产物（`input: 023`），
    那条边说的正是**「消融是对着主结果测的」**，它是两个章节之间唯一的连接，
    值得画出来。`parent` 跨章节同理：消融那条线是从主实验某一步分出去的，
    那一步就是「消融从哪儿开始的」。

    **一头未分章也算跨。**主实验没起名字、消融起了名字是很常见的写法，此时那条边
    仍然是「消融接在某个东西上」——把它当成同一章内部的边就等于把唯一的连接抹掉。
    两头都未分章才不算（那是普通项目里的普通边，跟章节没关系）。

    `kind` 是这条边在文件里的依据（parent / input），`note` 只有 `input:` 那边有
    （竖线右边那句「消费的是哪份产物」）；键恒在，值可以是空串——静态导出要逐字节
    确定，键的有无不能随内容变。
    """
    out: list[dict[str, str]] = []
    for sid in seq:
        s = by_id.get(sid)
        if s is None:
            continue
        mine = of.get(sid, "")
        p = s.parent
        if p and p in by_id and of.get(p, "") != mine:
            out.append({"from": p, "to": sid, "kind": "parent",
                        "from_chapter": of.get(p, ""), "to_chapter": mine, "note": ""})
        done: set[str] = set()
        for i in s.inputs:                          # 文件里的行序，同一个来源只画一次
            t = (i.get("step") or "").strip()
            if not t or t == sid or t not in by_id or t in done:
                continue
            if of.get(t, "") == mine:
                continue
            done.add(t)
            out.append({"from": t, "to": sid, "kind": "input",
                        "from_chapter": of.get(t, ""), "to_chapter": mine,
                        "note": (i.get("note") or "").strip()})
    return out


# 屏幕上不占位置的那些字符：Cf（格式类）加上 BOM 和软连字符。
# 它们能让两个「看起来完全相同」的名字在字节上不同。
#
# **必须写成转义，不许把字面字符敲进来**——那样这一行自己就是不可见的：
# 读代码的人看到的是一对空方括号，grep 也搜不到，改的人不知道自己在改什么。
_ZERO_WIDTH_RE = re.compile(
    "[" + chr(0xAD) + chr(0x200B) + "-" + chr(0x200F) + chr(0x202A) + "-" + chr(0x202E)
    + chr(0x2060) + "-" + chr(0x2064) + chr(0xFEFF) + "]"
)


def _chapter_fold(name: str) -> str:
    """章节名归一化**只用来找笔误**，绝不用来判定归属。

    判定归属用的永远是原样的字节（章节名一个字符不同就是两个章节，见 parse_chapter）。
    这里把大小写和空白抹平，是为了逮住 `Ablation` / `ablation ` / `消融 实验` 这种
    「同一个章节被拆成两半」——两半各导出一份 Methods，而两边看着都像对的。
    """
    # 零宽字符要在折叠时**抹掉**，不是当空白切开。
    # `消融实验` 和 `消融<U+200B>实验` 在屏幕上逐像素一模一样，而它们是两个章节：
    # 各导出一份 Methods、各有一个等级，两边看着都对。str.split() 只认 Unicode
    # 空白，Cf 类（零宽空格 / 零宽连接符 / 软连字符 / LTR-RTL 标记 / BOM）
    # 一个都不在其中，于是这类笔误从写入侧的控制字符检查和读侧的近似检查
    # 之间整个漏过去——而它恰恰是"肉眼绝对发现不了"的那一种。
    return " ".join(_ZERO_WIDTH_RE.sub("", name).casefold().split())


def compute_chapters(by_id: dict[str, Step], order: list[str],
                     levels: dict[str, str] | None = None,
                     result_ids: Iterable[str] = (),
                     of: dict[str, str] | None = None) -> dict[str, Any]:
    """派生章节清单。**派生字段，绝不存储**（P1）。

    `levels` 是每一步**自己**的可溯源等级（compile_forest 把 compute_traces 已经
    算好的那份传进来，省一遍正文解析）；不传就现算。`result_ids` 是 project.md 里
    声明过、且确实存在的那些成果步骤——用来判断「这个章节推不推得出自己的定稿流程」。

    每个章节回答的问题，全部是「把消融单独拿出来看」时会问的那几个：
    有哪些步骤（按 order，也就是树上的先后）、从哪里开始（roots）、多少步、
    状态怎么分布、说明是什么、**别人能不能重做**（level + 最弱的那一步）。

    **章节之间的顺序**按「这个章节最早那一步的 id」排。理由：id 是分配顺序，
    所以这就是章节被开启的先后，和步骤列表的顺序对得上——界面上从上往下读，
    章节的先后和步骤的先后是同一个方向。按名字排也能做到逐字节确定（码点序），
    但那个顺序对读的人不对应任何东西，而且下一个人一旦「修好」成按语言的排序规则，
    同一份文件在两台机器上就能排出两种顺序（P3 禁止）——不给那条路留门。
    """
    of = resolve_chapters(by_id) if of is None else of
    # order 之外的步骤（parent 成环时 compute_order 够不到它们）也要算进来：
    # 残缺输入上章节照样得给得出来，不然「构建不中断」在这里就破了个口子。
    tail = [sid for sid in sorted(by_id, key=id_key) if sid not in set(order)]
    seq = list(order) + tail

    declared_at: dict[str, list[str]] = {}          # 章节名 → 在哪几步上声明过（id 序）
    for sid in sorted(by_id, key=id_key):
        name = by_id[sid].chapter
        if name:
            declared_at.setdefault(name, []).append(sid)

    members: dict[str, list[str]] = {}
    unassigned: list[str] = []
    for sid in seq:
        name = of.get(sid, "")
        (members.setdefault(name, []) if name else unassigned).append(sid)

    # 传进来的那份可能不全（order 够不到的那几步不在里面），缺的现算：
    # 残缺输入上也得给出等级，不能让一条脏 parent 边把整份章节清单炸掉。
    levels = dict(levels or {})
    for sid in seq:
        if sid not in levels:
            levels[sid] = traceability(by_id[sid])["level"]

    diags: list[dict[str, Any]] = []
    out_chapters: list[dict[str, Any]] = []
    for name in sorted(members, key=lambda n: id_key(members[n][0])):
        steps = members[name]
        # 这个章节的**入口**：parent 不在同一个章节里的成员（含真正的树根）。
        # 一个章节可以有好几个入口——它横跨几棵树时本来就该是这样。
        roots = [sid for sid in steps
                 if not by_id[sid].parent or of.get(by_id[sid].parent or "", "") != name]
        weakest = ""
        for sid in steps:                           # 平局取 order 里靠前的那个（补记录从头补）
            if not weakest or LEVELS.index(levels[sid]) < LEVELS.index(levels[weakest]):
                weakest = sid
        # 说明归谁：**id 序最早的那个带说明的声明**。为什么不是「最早的声明」——
        # 在第二处声明同一个章节时不重复说明是正常写法（说明只该写一遍），
        # 按「最早的声明」算会把唯一的那句说明扔掉，而那不是任何人的本意。
        notes = [(sid, by_id[sid].chapter_note)
                 for sid in declared_at.get(name, []) if by_id[sid].chapter_note]
        distinct = list(dict.fromkeys(n for _sid, n in notes))
        if len(distinct) > 1:
            who = " / ".join(sid for sid, _n in notes)
            diags.append(warn(
                "warn", "chapter_note_conflict",
                f"章节「{name}」在 {who} 上写了 {len(distinct)} 句不同的说明。"
                f"按 id 序最早的那句生效（{notes[0][0]} 写的），其余几句在界面上一个字都不会出现"
                "——多半是笔误，或者两个人各写各的。留一句；要是本来就想说两个章节，"
                "把名字改开",
                by_id[notes[0][0]].dirname,
                {"name": name, "ids": who, "id": notes[0][0], "n": len(distinct)}))
        out_chapters.append({
            "name": name,
            # 名字里的 `主实验/数据准备` 拆开给界面分组用。**语义上仍然是一层**：
            # 这里给的是显示用的段，不是一棵章节树（见文件头 CHAPTER_SEP 那一段）。
            "parts": name.split(CHAPTER_SEP),
            "note": notes[0][1] if notes else "",
            "declared_at": list(declared_at.get(name, [])),
            "steps": list(steps),
            "roots": roots,
            "n": len(steps),
            # 三个键恒在（值可以是 0）：静态导出要逐字节确定，键的有无不能随内容变。
            "status": {st: sum(1 for sid in steps if by_id[sid].status == st) for st in STATUSES},
            "level": levels[weakest] if weakest else "",
            "weakest": weakest,
        })

    # 「这个章节推不出自己的定稿流程」。**只在项目已经声明过成果时才说**：
    # 一个 result 都没有的项目，该说的话 pipeline_no_result 已经说过一遍了，
    # 在这里按章节再说 N 遍就是同一件事念 N+1 次，而那正是让人从此不看诊断的做法。
    # 措辞和那一条同一个路子：教怎么办，不是责备——现存项目本来就没有成果声明。
    rset = set(result_ids)
    if rset:
        for c in out_chapters:
            if any(sid in rset for sid in c["steps"]):
                continue
            diags.append(warn(
                "info", "chapter_no_result",
                f"章节「{c['name']}」里一条 `result:` 都没有，所以推不出它自己的定稿流程"
                "（这个项目别的章节有）。论文里主实验和消融本来就是两段 Methods，"
                "想要这一段，在 project.md 里给这个章节的成果写一行 "
                "`result: <步骤 id> | <这是什么成果>`",
                PROJECT_NOTE, {"name": c["name"], "n": str(c["n"])}))

    # 「几乎同名的两个章节」。**刻意只认大小写和空白**，不做任何相似度猜测：
    # `消融实验` vs `消融試驗` 靠猜才认得出来，而猜错一次就是对着人指认一个不存在的
    # 笔误——评级一旦会撒谎人就不再看它，诊断也一样。
    #
    # 为什么**不做**「章节只有一个步骤 → 也许是笔误」：一个章节被开启的那一刻
    # 必然只有一步（就是声明它的那一步），这条会在每一次正确使用时当场炸一声。
    # 合法的单步章节也确实存在（一步就说完的「数据准备」）。会在正确用法上响的
    # 诊断，人学会的是忽略整个诊断栏。
    folds: dict[str, list[str]] = {}
    for c in out_chapters:
        folds.setdefault(_chapter_fold(c["name"]), []).append(c["name"])
    for fold in sorted(folds):
        names = folds[fold]
        if len(names) < 2:
            continue
        shown = " / ".join(f"「{n}」" for n in names)
        diags.append(warn(
            "warn", "chapter_near_duplicate",
            f"{shown} 只差大小写或空白，几乎肯定是同一个章节被拆成了两半——"
            "两半各自算一份成员、各自导出一段 Methods，而两边看着都像对的。"
            "章节名一个字符不同就是两个章节，统一成一种写法",
            PROJECT_NOTE, {"names": " / ".join(names), "n": str(len(names))}))

    return {
        "declared": bool(out_chapters),
        "chapters": out_chapters,
        # 每一步属于哪个章节。只装进了章节的那些，未分章的 `.get(sid, "")` 拿空串。
        "of": {sid: of[sid] for sid in seq if sid in of},
        "unassigned": unassigned,
        "crossings": chapter_crossings(by_id, of, seq),
        "diagnostics": diags,
    }


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

    # 候选组 / 汇回边：同样是派生，磁盘上只有各个孩子自己那句 branch: 和 input:。
    groups = compute_branch_groups(by_id, children)
    fork_at = {g["at"]: g for g in groups}
    merges = compute_merges(by_id, order)
    merge_in: dict[str, list[str]] = {}
    merge_out: dict[str, list[str]] = {}
    for m in merges:                                  # merges 已按行序，这里跟着确定
        merge_in.setdefault(m["to"], []).append(m["from"])
        merge_out.setdefault(m["from"], []).append(m["to"])
    w_branch = validate_branches(by_id, groups)

    traces = compute_traces(by_id, order)             # 派生，不存储
    # 每一步**自己**的等级（不是整链的）。章节的等级取的就是它，理由和定稿流程
    # 那边一字不差：整链会把不属于本章节的祖先算进来，而「消融这部分别人能不能重做」
    # 问的正是这几步自己。从 traces 里取，省一遍正文解析（1000 步上这是实打实的钱）。
    self_levels = {sid: traces[sid]["self"] for sid in order}

    # 章节。和定稿流程同一条规矩：**只在真有人写过 `chapter:` 时才存在**。
    # 现存项目一个 `chapter:` 都没有，它们的 forest 必须和这一轮改动之前逐字节一样
    # ——不多一个键、不多一条诊断。所以这里不是「算一份『未分章』挂上去」，
    # 而是整个键都不出现（forest 和每个 step 上都是）。
    chapter_of = resolve_chapters(by_id)           # 派生，绝不存储；没人写就是空 dict

    # 定稿流程。**只在项目声明了成果时才存在**，理由同上。
    results = scan_results(steps_dir)
    pipeline = compute_pipeline(
        by_id, results, chapter_of if chapter_of else None) if results else None
    pipe_pos = {sid: i for i, sid in enumerate(pipeline["order"])} if pipeline else {}
    pipe_results = {r["step"] for r in pipeline["results"]} if pipeline else set()

    # 章节清单。`result_ids` 用的是流程那边已经筛过的那份（声明了、而且真存在），
    # 不是 project.md 的原样行——悬空的 `result:` 已经由 dangling_result 报过一次，
    # 拿它去判断「这个章节有没有成果」只会让同一个笔误引出第二条诊断。
    chapters = compute_chapters(by_id, order, self_levels, pipe_results, chapter_of) \
        if chapter_of else None

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
        # 这一步是不是一个决策分叉点：是就给出整组候选（含决定定了没有），不是就 None。
        # 键恒在，值可以是 None——静态导出要逐字节确定，键的有无不能随内容变。
        g = fork_at.get(sid)
        d["fork"] = _copy_group(g) if g else None
        # 哪些 input 边是汇回（生产者 id），以及本步的产物汇回到了谁那里（消费者 id）。
        #
        # 刻意**不**把这个标进 d["inputs"] 里的那几条记录：inputs 是文件里那几行
        # `input:` 的原样镜像，而「是不是汇回」是从树的形状算出来的——同一行 input
        # 会因为别人被移动而改变归类。混进同一个 dict，读的人就分不清哪些字段是
        # 文件里写着的、哪些是这一轮算出来的（移动一步 → inputs 看着变了 = 假象）。
        d["merge_in"] = merge_in.get(sid, [])
        d["merge_out"] = merge_out.get(sid, [])
        # 用目录名取附件，不用 id：两个目录写了同一个 id 时，validate 只改得动
        # 后一个的 id（→ 001~dup2），而附件是按目录扫出来的，用 id 做键会把
        # 001 的清单换成 001~dup2 那个目录的文件（点开 404，自己的附件消失）。
        d["files"] = files.get(step.dirname, [])
        d["lane"] = lane[sid]
        d["row"] = len(steps_out)
        d["trace"] = traces[sid]
        # 「我在不在定稿流程里」。开发路径上的这个标记和流程那边跳回开发路径的链接
        # 是同一件事的两头——两条路径都留着，才答得出「这一步当时有 3 个候选，
        # 为什么选了它」。`rule` / `note` 是这一步自己写的那行 `pipeline:`（多数是空的）。
        if pipeline is not None:
            d["pipeline"] = {
                "member": sid in pipe_pos,
                "result": sid in pipe_results,
                "index": pipe_pos.get(sid),
                "rule": step.pipeline,
                "note": step.pipeline_note,
            }
        # 「我属于哪个章节」。`name` 是**继承**出来的（自己没写就是 parent 的），
        # `declared` 说的是这一行是不是写在自己身上——两者必须分开：界面要把
        # 「这一步开启了消融」和「这一步跟着消融走」画成两回事，否则一整条子树
        # 看着都像各自声明过，而实际上改一个字就能整条搬走。
        # `note` 是这一步写下的**章节说明**（多数是空的；生效的那句在 chapters 里）。
        if chapters is not None:
            d["chapter"] = {
                "name": chapters["of"].get(sid, ""),
                "declared": bool(step.chapter),
                "note": step.chapter_note,
            }
        steps_out.append(d)

    out: dict[str, Any] = {
        "steps": steps_out,
        "order": order,
        "lanes": {sid: lane[sid] for sid in order},
        "lane_count": (max(lane.values()) + 1) if lane else 0,
        "tree": compute_tree(by_id, children, order),
        # 互斥候选组（按分叉点排序，根之间那一组在最前）与汇回边。两者都是**边和组的
        # 语义**，不是几何：布局（order / lanes / tree）一个数都不因为它们而变。
        "branch_groups": [_copy_group(g) for g in groups],
        "merges": merges,
    }
    # 键的位置固定在 merges 之后、warnings 之前（静态导出要逐字节一致，
    # 而 dict 的插入顺序就是 JSON 里的顺序）。没有成果时整个键不出现。
    if pipeline is not None:
        out["pipeline"] = pipeline
    # 章节同理：位置固定在 pipeline 之后、warnings 之前，一个 `chapter:` 都没写
    # 的项目整个键不出现。诊断走 chapters["diagnostics"]，**不进 warnings**——
    # 和定稿流程那几条同一档：它们问的是「这个项目分得清不清楚」，
    # 不是「这个结果追不追得到」，一条都不影响可溯源等级。
    if chapters is not None:
        out["chapters"] = chapters
    out["warnings"] = w_scan + w_val + w_inputs + w_branch + w_lint
    out["row_h"] = ROW_H
    out["lane_w"] = LANE_W
    return out
