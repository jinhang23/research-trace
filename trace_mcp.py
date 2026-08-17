"""trace_mcp — 把 trace 暴露成 MCP 工具。

用 MCP 而不是让 agent 自己拼 HTTP 请求，换来的是：参数有 schema（客户端先校验，
不合法的调用根本发不出来）、不用生成 requests/curl 代码、中文不会再撞上终端编码。

两种后端，按环境变量选：

    TRACE_URL + TRACE_TOKEN    → 走 HTTPS 打远端服务（agent 在 HPC 上就用这个）
    TRACE_DATA=<仓库路径>       → 直接读写本地文件（agent 和数据在同一台机器上）

零依赖：MCP 是开放协议规范，`mcp` 那个 pip 包只是它的官方 Python SDK 之一。
stdio 侧就是换行分隔的 JSON-RPC 2.0，这里直接说协议，任何裸 Python 3.10+ 都能跑。

注意：stdio 传输下 stdout 是协议通道，任何诊断输出都必须走 stderr。
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif"}
# 和 trace_write.MAX_FILE_BYTES 保持一致。这里单独放一份，是因为 HTTP 后端
# 不 import trace_write，而大小检查必须在读文件**之前**做。
MAX_ATTACH_BYTES = 32 * 1024 * 1024
MARK = {"done": "●", "wip": "○", "dead": "▣"}
LEVELS = {"L0": "不可溯源", "L1": "可读", "L2": "可定位", "L3": "可重跑", "L4": "已复现"}
KIND_LABEL = {
    "hpc": "超算", "github": "GitHub", "git": "Git", "dropbox": "Dropbox", "drive": "Drive",
    "object": "对象存储", "archive": "数据仓库", "mlhub": "实验平台", "url": "链接",
    "local": "本机", "path": "路径",
}
PATHS_DESC = (
    "外部产物的位置，一条一行，按 `|` 分段：**第一段永远是位置**，之后每一段按内容认领——\n"
    "  · 整段恰好是 input / script / output / evidence 之一 → 它是**角色**\n"
    "  · 整段的空白分隔 token **全部**形如 k=v → 它们是**机器属性**\n"
    "  · 其余 → 都拼进**说明**（中文、逗号、\"lr=3e-4 的那次运行\" 都落这里，不会被误当机器字段）\n"
    "已知属性：size=字节数（写整数，别写 12GB）、n=条目数、md5= / sha256=、"
    "checked=YYYY-MM-DD（最后一次确认还在）、missing=YYYY-MM-DD（最后一次确认已经没了）。"
    "不认识的属性照样保留，不会被吃掉。\n"
    "GB 级的东西（数据集、checkpoint）不要传进来，只记它在哪 —— 这是溯源的一半。例：\n"
    "  \"/blue/<组>/<用户>/exp/agnews-clean | input | 去重后的训练集 | n=120000 size=12884901888\"\n"
    "  \"/orange/<组>/<用户>/ckpt/run042.pt | output | 最好的那一版 | sha256=ab12cd34\"\n"
    "  \"https://github.com/你/仓库/tree/9b7d112 | script | 跑这一步的代码\""
)
INPUTS_DESC = (
    "**数据依赖**：这一步消费了哪几步的产物。一条一行，写成 \"步骤id | 消费的是哪份产物\"。\n"
    "**它和 parent 是两件不同的事，最容易混，混了画出来的图会骗人**：\n"
    "  · `parent` ＝ **记录的派生关系**：我当时是接着哪一步的想法往下做的。树只有一个父。\n"
    "  · `input`  ＝ **数据依赖**：这些字节是从哪来的。可以有好几个，是一张 DAG。\n"
    "一步的输入常常同时来自两支（口袋组成来自 013、配对分数来自 014），"
    "而树上只能表达其中一个 —— 另一个就得写在这里，否则「这个数字怎么来的」永远缺一半。\n"
    "例：\"013 | pocket_composition.csv\"、\"014 | rmscore_pairs.csv\"。\n"
    "目标步骤不存在时**不会拒绝**（建立顺序不定），只会在读的一侧给一条警告。"
)
BRANCH_DESC = (
    "这一步和**它 parent 之间那条边**是什么性质。两选一：\n"
    "  extends —— **默认，不用写**。普通延伸：接着上一步继续做。\n"
    "  alternative —— **互斥候选**：我和我的兄弟们是同一个问题的几个答案，"
    "**只能选一条走下去**。\n"
    "**最容易搞错的一点**：「从 A 又分出一条支线 C 去试别的」**不是** alternative，"
    "那就是普通的 extends。只有当几条并排的支线是**同一个问题的互斥答案**"
    "（A 采样重加权 / B 损失重加权，最后只留一条）时才是 alternative。\n"
    "可以带一句这个候选自己的角度：\"alternative | 先试最便宜的：只调采样权重\"。\n"
    "三件必须知道的事：\n"
    "  · 「**这一组有谁**」是扫出来现算的——同一个父节点底下所有 alternative 的孩子算一组。"
    "父节点上**永远不写**孩子清单，兄弟之间也**不互相登记**：那是双真相源，"
    "而且一个候选被 trace_move_step 挪走之后，存下来的清单立刻变成谎话。\n"
    "  · 「**选了哪个**」不用另写字段：哪条走通了，就把**其余候选标 status=dead 并写清"
    "为什么放弃**，「已定」就是从这里推出来的。一组里还有两个以上没标 dead ＝ "
    "这个岔路口**还没决定**（那不是错，只是还没结掉）。\n"
    "  · **汇回不要用它表达**。C 这条支线的产物后来又参与了 A 那条线上的某一步，"
    "那是 `inputs`（\"013 | scores.csv\"），不是一种 branch —— branch 说的只有"
    "「我和我 parent 那条边」，它表达不了跨支线的边。"
)
DECISION_DESC = (
    "**写在分叉点（也就是父节点）身上**的一句话：它底下那几个互斥候选**在决定什么**。"
    "例：\"类别不平衡怎么处理？只能选一条走下去\"。\n"
    "候选有谁、哪个被选中，程序全都算得出来；**唯独这句话推导不出来，只能人写**——"
    "半年后看到两条并排的支线，没有它就只剩猜。和「为什么」是同一类字段。\n"
    "别写在候选自己身上：候选身上写的是 `branch`，分叉点身上写的是 `decision`。"
)
PIPELINE_DESC = (
    "**这一步和定稿流程的关系**。三选一，而且**默认那一档就是别动它**：\n"
    "  （不写）  —— 默认。算出来：从声明的成果沿 `input:` 反向做闭包"
    "（一步没写 input: 时退回它的 parent），剔掉 dead。**先试着让推导对**。\n"
    "  exclude —— 「成功了，但它不是最终流程的一环」。典型是一次探索性的旁支："
    "它的产物确实被下游读了，可写进 Methods 只会让别人以为非做不可。\n"
    "  include —— 「闭包够不到它，但它确实是流程的一环」。够不到多半说明"
    "**记录本身缺了一条边**，所以先想想是不是该给下游补一行 `input:`——"
    "补了 input 连数据流图也一起修对了，写 include 只修了这一份导出。\n"
    "**必须带一句理由**，写成 \"exclude | 探索性的，成功了但没进最终流程\"。"
    "这是它和 branch 唯一的分歧：候选组在树上看得见，而 pipeline 除了改变一份导出"
    "之外不留任何痕迹——没有那半句话，半年后分不清这是想清楚的决定还是一次误点。\n"
    "**它推翻的是算出来的结果，所以是例外不是常规**：一个项目里写满 include/exclude，"
    "等于把「成员清单」一行行手抄回了记录里，而那正是这套推导要避免的东西。"
)
# 章节。这一段几乎全部篇幅都花在**三样东西的分界**上，因为 agent 在这里犯的错
# 是「三选一选错了」而不是「格式写错了」，而选错了不会报任何错：
#   项目 = 不同的研究        章节 = 同一个研究里并列的几块      分叉 = 同一个问题的互斥候选
# 拿章节去表达分叉（「章节 A / 章节 B，最后选一个」）在磁盘上完全合法，读侧只会
# 老老实实给两块都导出一段 Methods —— 而那正是「只能选一条」这句话被弄丢的样子。
#
# 第二个必须说死的是**只标第一步**。沿树继承是这个设计的全部收益：不写这一句，
# agent 会给 20 步各标一遍（那是它对「让这些步骤属于消融」最直觉的实现），
# 于是改一次章节名要改 20 个文件、移走一支还带着一行过期的声明。
CHAPTER_DESC = (
    "这一步开启的**章节**（同一个项目内部并列的几块：主实验 / 消融实验 / 数据准备）。"
    "写成 \"消融实验\" 或 \"消融实验 | 逐个拿掉模块，对着主实验的 023 比\"，"
    "竖线右边是**这个章节**的说明（不是这一步的），可选。\n"
    "**只标在开启那条线的第一步上。** 下面整棵子树自动继承，不用也不要给每一步各标一遍——"
    "标满了的代价是改一次章节名要改 20 个文件、移走一支还带着一行过期的声明，"
    "而继承下来的归属本来一个字都不用维护。想让某一步脱离，让它自己声明一个新的章节名。\n"
    "**和另外两样别混，选错了不会报错，只会让记录说出你没打算说的话**：\n"
    "  · **项目**（trace_new_project）＝ 不同的研究。步骤 id 各自从 001 开始，互不相干。\n"
    "  · **章节**（这个字段）＝ 同一个研究里**并列**的几块。**互不排斥，都要留着**——"
    "论文里主实验一段 Methods、消融一段，两段都要写。章节之间的引用是正常的"
    "（消融当然吃主实验的产物，写成 `input:` 就行，那条跨章节的边会被标出来）。\n"
    "  · **分叉**（branch: alternative）＝ 同一个问题的**互斥候选**，**只能选一个**，"
    "其余的最后要标 dead。「A 方案 / B 方案，最后二选一」是分叉，**不是**两个章节；"
    "拿章节去写它，读侧只会给两边各导出一段 Methods，而「只能选一条」这句话就此丢了。\n"
    "id **不按章节重编号**（消融不从 001 重新开始）：`[[007]]` 和论文脚注要在整个项目里唯一。"
    "章节也**不嵌套**——名字里可以写 \"主实验/数据准备\"，那只是显示时按 `/` 分组，语义上仍是一层。"
)
CODE_DESC = (
    "跑这一步的代码在哪，一条一行，写成 \"kind | 位置 | k=v …\"。kind 三选一：\n"
    "  git       —— 位置是仓库/树的 URL 或本地仓库路径（`commit:` 字段等价于这一种，"
    "已经写了 commit 就不用再写一条）\n"
    "  snapshot  —— **代码不在 git 里**时用它：一个快照目录 + 逐文件校验和清单。"
    "例 \"snapshot | /orange/lab/run_snapshots/20260809 | manifest=MANIFEST.md5 n=43\"\n"
    "  container —— 容器镜像，位置是镜像引用，例 \"container | ghcr.io/lab/rna:2026-08 | "
    "digest=sha256:7d4e…\"\n"
    "**snapshot 和 container 一样能上 L2**（可定位）。以前没有这个字段时，"
    "人会把快照目录塞进 commit:，于是那一格里躺着一个不是 commit 的东西，"
    "「解不解析得出来」这条查证就永远给不出答案 —— 现在不要再那么干。"
)
# 路径核对的三种结论。**unreachable 不是 missing**：服务器（或 agent 这台机器）
# 看不到 /blue/… 只说明这条链路够不着，不说明那份数据没了。把两者混为一谈，
# 就会在别人的超算目录还好好的时候，在记录上写下「2026-08-09 起不存在」。
PROBE_PRESENT = "present"
PROBE_MISSING = "missing"
PROBE_UNREACHABLE = "unreachable"
DEFAULT_AUTHOR = os.environ.get("TRACE_AUTHOR", "agent")

BODY_TEMPLATE = (
    "## 为什么\n（承接上一步的什么发现，想验证什么假设）\n\n"
    "## 做了什么\n\n## 结果\n\n## 结论\n\n## 下一步\n"
)

# 译文的 front-matter 里**只准**出现 title（项目笔记是 name）。下面这些是结构键，
# 它们在 note.md / project.md 里已经有了；写进译文就是双真相源，读侧一律忽略并报警告。
# 这里留一份字面量而不是 import trace_core，是因为远端后端那条路上这个文件可能是
# 单独拷过去的（只有 TRACE_URL，没有 trace_core）。tests/test_mcp.py 拿 core 的
# TR_STRUCT_KEYS 逐字核对这一份，漂移会当场被测出来。
TR_STRUCT_KEYS = ("id", "parent", "status", "date", "commit", "author",
                  "tags", "path", "repro", "key", "input", "code", "moved",
                  # branch / decision 是**结构**（这条边什么意思、这个岔路口在问什么），
                  # 不是正文。译文里写一句 `branch: alternative` 就等于让「谁和谁互斥」
                  # 这件事在两个文件里各有一份答案，而它们只有在改了其中一份时才会打架。
                  "branch", "decision",
                  # result / pipeline 同理，而且后果更重：这两个键决定**定稿流程长什么样**
                  # ——论文附录里出现哪几步。译文里写一句 `pipeline: exclude` 会被读侧
                  # 一个字不看地丢掉，于是「我明明把它排除了」和「它还在 Methods 里」
                  # 同时成立，而人只会去怀疑推导错了。
                  "result", "pipeline",
                  # chapter 是这一批里最能悄悄分家的一个：它**沿树继承**。译文里多写
                  # 一行 `chapter: Ablation`，改的不是这一步，是它底下整棵子树在英文
                  # 页面上的归属——同一个项目按两种分法各导出一份 Methods，而两边
                  # 看着都像对的。读侧一个字节都不看它。
                  "chapter")

# 小节名的中英对照。agent 手上**没有别的地方**能知道这张表：FORMAT.md 在 pip 装的
# 机器上根本不存在，而小节名是精确匹配的——写成 `## Why not`，评级和 check 就都
# 找不到内容。所以它必须出现在工具描述里。同样由测试对着 trace_core.SECTION_NAMES /
# INSIGHT_NAMES 逐字核对。
SECTION_TABLE = (
    "  中文 为什么 / 做了什么 / 结果 / 结论 / 下一步\n"
    "  英文 Why / What / Result / Conclusion / Next\n"
    "  项目笔记的洞察 中文 核心想法 / 有效 / 无效 / 坑；英文 Ideas / Works / Doesn't work / Pitfalls"
)


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- 数据仓体检

DATA_ROOT_READY = "ready"      # 已经是数据仓（有 projects/）
DATA_ROOT_EMPTY = "empty"      # 目录在、但里面什么都没有
DATA_ROOT_ABSENT = "absent"    # 目录还不存在（但父目录在，可以建）
DATA_ROOT_OCCUPIED = "occupied"  # 目录里有别的东西，却没有 projects/


def _visible_entries(d: Path) -> list[str]:
    """目录里点开头之外的条目名。`.git`、`.trace-lock` 这些不算「内容」。"""
    try:
        return sorted(x.name for x in d.iterdir() if not x.name.startswith("."))
    except OSError:
        return []


def _sibling_data_roots(d: Path) -> list[str]:
    """同一层里长得像数据仓的目录名 —— 用来提示「你是不是想指那个」。"""
    try:
        return sorted(x.name for x in d.parent.iterdir()
                      if x.is_dir() and x.name != d.name and (x / "projects").is_dir())[:5]
    except OSError:
        return []


def check_data_root(raw: str | Path) -> tuple[Path, str, str]:
    """看一眼数据仓路径是什么状况。**只看不动**，一个目录都不建。

    要解决的问题是：`ensure_layout` 见到什么路径都乖乖 mkdir，所以「首次全新安装」
    和「老机器上把路径打岔了一个字符」在磁盘上是**一模一样**的观测（空目录 + 0 个项目），
    自检还照样报「全部通过」。用户于是在一棵凭空造出来的空树上开始记录，几十步之后
    才发现老项目一个都看不见。

    这里不禁止创建 —— 换机首装时数据仓本来就不存在，插件的 data_dir 说明里也写明
    「目录不存在会自动建出来」，禁掉等于挡住正常的第一次初始化。做法是**把状态说出来**，
    让调用方（selfcheck / init）能区分这四种情况并如实报出去。

    唯一当场拒绝的是「连父目录都不存在」：那不是「还没初始化」，那是路径写错了
    ——没人会指望一个记录工具替你 mkdir -p 一整条凭空的路径。

    返回 (解析后的绝对路径, 上面四个状态之一, 给人看的一句话)。
    """
    text = str(raw)
    # 宿主没把 ${user_config.…} 展开就原样传下来了。实测过：这种字面量会被
    # 当成相对路径，在当前工作目录下真的建出一个名叫 ${user_config.data_dir}
    # 的目录，然后一切「正常」。这是配置管道坏了，不是「还没初始化」。
    if "${" in text:
        raise ToolError(
            f"数据仓路径里有没被展开的配置模板：{text}\n"
            "说明宿主没有把 /plugin 里填的值代进来。去 /plugin → research-trace → 配置，"
            "确认数据仓目录那一栏填的是真实路径；照原样用下去会在当前目录建出一个"
            "以模板名命名的幽灵目录。")

    root = Path(text).expanduser()
    try:
        root = root.resolve()
    except OSError:
        pass

    if root.exists() and not root.is_dir():
        raise ToolError(f"数据仓路径 {root} 是个文件，不是目录。填步骤树所在的目录（里面是 projects/）。")

    if (root / "projects").is_dir() or (root / "steps").is_dir():
        return root, DATA_ROOT_READY, ""

    def _hint(head: str) -> str:
        sibs = _sibling_data_roots(root)
        if sibs:
            return head + f" 同一层里这些看着才像数据仓：{'、'.join(sibs)} —— 你确定不是想指其中一个吗？"
        return head

    if root.is_dir():
        rest = _visible_entries(root)
        if not rest:
            return root, DATA_ROOT_EMPTY, _hint(f"{root} 是个空目录，我会在里面建一棵全新的空树。")
        return root, DATA_ROOT_OCCUPIED, _hint(
            f"{root} 里已经有 {'、'.join(rest[:5])}{' 等' if len(rest) > 5 else ''}，"
            f"却没有 projects/ —— 如果这台机器上本来就有记录，那多半是路径填错了。")

    if not root.parent.is_dir():
        raise ToolError(
            f"数据仓路径 {root} 不存在，连它的上级 {root.parent} 也不存在。\n"
            "这不像「还没初始化」，像是路径写错了（盘符大小写、分隔符、多打一层）。\n"
            "确认路径；如果确实要新建，先手工把上级目录建出来再重试。")
    return root, DATA_ROOT_ABSENT, _hint(f"{root} 还不存在，我会现建一个全新的空仓。")


# ---------------------------------------------------------------- 路径核对
#
# 这一份实现被三个门面共用（MCP 工具、trace_cli paths --check、服务端的定期扫描）。
# 放在 trace_mcp 里而不是 core，是因为 core 除 scan/signature 外不碰 IO；
# 放在这里而不是各写一份，是因为「什么叫看不见」的判据一旦分家，就会有一个门面
# 在别人的超算目录还好好的时候，往记录上写下「已确认不存在」。

_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def probe_path(loc: str) -> tuple[str, int | None]:
    """在**本机**上看一眼这条路径还在不在。返回 (present/missing/unreachable, 字节数或 None)。

    三条规则，每一条挡的都是一种会写进记录的谎话：

    **① 远端位置一律不探测**（`s3://` / `https://` / `//host/share`）。
    这不是省事，是安全边界：任何能写记录的人都能往 path: 里塞一个内网地址，
    如果这里去发请求，服务器就变成了替他扫内网的代理（SSRF），而它跑在能看到
    数据仓的那台机器上。远端产物还在不在，只能由**人**去核对后写回结论。

    **② 够不着 ≠ 不存在。** 服务端跑在一台机器上，用户的 `/blue/…` 多半挂在另一台
    超算上。那台机器上根本没有 /blue 时，`exists()` 返回 False —— 把它当成
    「数据没了」，就会给一份好好的记录盖上「2026-08-09 起不存在」的结论，
    而 P4 说了 missing 是**结论**不是错误，结论写错比不写贵得多。
    判据取「**上级目录看得见**」：`/blue/lab/cif_files` 没了但 `/blue/lab` 在，
    那是真的被删了；连 `/blue/lab` 都看不见，就是这台机器够不着这条链路，
    什么都不写。宁可漏报，不可误报。

    **③ 只报文件的大小。** 目录的 st_size 是元数据块大小（Linux 上常是 4096），
    写进 size= 会把「57 GB 的那个目录」记成 4 KB。目录有多大要遍历，见
    sweep 那边关于「数条目数」的说明——不在这里做。
    """
    loc = (loc or "").strip()
    if not loc or _URL_SCHEME_RE.match(loc) or loc.startswith("\\\\") or loc.startswith("//"):
        return PROBE_UNREACHABLE, None
    p = Path(loc)
    if not p.is_absolute():
        # 相对路径相对于**谁**的工作目录？没有答案，就不该给结论。
        # 这一条在 Windows 上顺带挡住了 `/blue/lab/…`：那种写法在 Windows 上是
        # **盘符相对**的（跟着当前驱动器走），拿它去 stat 等于换了个盘查一遍。
        return PROBE_UNREACHABLE, None
    try:
        parent = p.parent
        if parent == p or not parent.is_dir():
            return PROBE_UNREACHABLE, None
        st = p.stat() if p.exists() else None
    except OSError:
        # 权限不足、NFS 掉线、路径过长——都是「这次没看清」，不是「它没了」。
        return PROBE_UNREACHABLE, None
    if st is None:
        return PROBE_MISSING, None
    return PROBE_PRESENT, (int(st.st_size) if p.is_file() else None)


# ---------------------------------------------------------------- 后端


class HttpBackend:
    """打远端 trace 服务。只用标准库。"""

    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token

    def _call(self, method: str, path: str, payload=None, raw: bytes | None = None,
              headers: dict[str, str] | None = None):
        data = raw if raw is not None else (
            json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        if payload is not None and raw is None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except Exception:
                pass
            raise ToolError(f"{e.code} {detail}") from None
        except urllib.error.URLError as e:
            raise ToolError(f"连不上 {self.base}：{e.reason}") from None
        except json.JSONDecodeError:
            # 代理/网关返回 HTML 错误页时会走到这里
            raise ToolError(f"{self.base} 返回的不是 JSON，可能中间有代理或网关错误页") from None
        except (TimeoutError, OSError) as e:
            # 读阶段的超时是裸 TimeoutError，绕开了上面的 URLError 分支
            raise ToolError(f"请求 {self.base} 失败：{e}") from None

    def projects(self):
        return self._call("GET", "/api/projects")["projects"]

    def forest(self, project):
        return self._call("GET", f"/api/p/{urllib.parse.quote(project)}/forest")

    def step(self, project, sid):
        return self._call("GET", f"/api/p/{urllib.parse.quote(project)}/steps/{urllib.parse.quote(sid)}")

    def create_project(self, name):
        return self._call("POST", "/api/projects", {"name": name})

    def update_project(self, project, payload):
        return self._call("PATCH", f"/api/projects/{urllib.parse.quote(project)}", payload)

    def create(self, project, payload):
        return self._call("POST", f"/api/p/{urllib.parse.quote(project)}/steps", payload)

    def update(self, project, sid, patch):
        return self._call("PATCH", f"/api/p/{urllib.parse.quote(project)}/steps/{urllib.parse.quote(sid)}", patch)

    def move(self, project, sid, payload):
        # 和 update 打的是同一条路由：`parent` 出现在 PATCH 里就是一次移动。
        # 服务端在那里分流到 move_step（reason 必填），返回的是移动审计而不是步骤。
        return self._call("PATCH", f"/api/p/{urllib.parse.quote(project)}/steps/{urllib.parse.quote(sid)}", payload)

    def check_path(self, project, sid, payload):
        return self._call("POST", f"/api/p/{urllib.parse.quote(project)}/steps/"
                                  f"{urllib.parse.quote(sid)}/paths/check", payload)

    def delete(self, project, sid, payload):
        return self._call("DELETE", f"/api/p/{urllib.parse.quote(project)}/steps/{urllib.parse.quote(sid)}", payload)

    def attach(self, project, sid, data, name, mime):
        h = {"Content-Type": mime}
        if name:
            h["X-Filename"] = urllib.parse.quote(name)
        return self._call("POST", f"/api/p/{urllib.parse.quote(project)}/steps/{urllib.parse.quote(sid)}/files",
                          raw=data, headers=h)

    def translate(self, project, sid, lang, payload):
        return self._call("PUT", f"/api/p/{urllib.parse.quote(project)}/steps/"
                                 f"{urllib.parse.quote(sid)}/tr/{urllib.parse.quote(lang)}", payload)

    def translate_project(self, project, lang, payload):
        return self._call("PUT", f"/api/p/{urllib.parse.quote(project)}/tr/"
                                 f"{urllib.parse.quote(lang)}", payload)

    def untranslated(self, project, lang):
        return self._call("GET", f"/api/p/{urllib.parse.quote(project)}/untranslated"
                                 f"?lang={urllib.parse.quote(lang)}")

    def pipeline(self, project, chapter: str = ""):
        # 读，所以不要令牌（和 forest / forks 一致）。服务端已经把 pipeline_payload
        # 拼好了，这里一个字段都不重算——远端和本地必须给出同一条流程。
        #
        # 按章节切也走**服务端**那一次调用，不在这边拿整份流程自己筛：切分的判据
        # （哪几步属于这一章、哪几步是借来的）只有 core 那一份，客户端再筛一遍就是
        # 第二份实现，而其中一份会进论文。
        q = f"?chapter={urllib.parse.quote(chapter)}" if chapter else ""
        return self._call("GET", f"/api/p/{urllib.parse.quote(project)}/pipeline{q}")

    def chapters(self, project):
        return self._call("GET", f"/api/p/{urllib.parse.quote(project)}/chapters")

    def set_result(self, project, step, note):
        return self._call("PUT", f"/api/p/{urllib.parse.quote(project)}/results/"
                                 f"{urllib.parse.quote(step)}", {"note": note})

    def drop_result(self, project, step):
        return self._call("DELETE", f"/api/p/{urllib.parse.quote(project)}/results/"
                                    f"{urllib.parse.quote(step)}")


class LocalBackend:
    """直接读写文件。agent 和数据在同一台机器上时用这个，不需要起服务。"""

    def __init__(self, root: Path) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import trace_core as core
        import trace_write as W

        self.core, self.W = core, W
        # 先看一眼这个路径是什么状况再动手。ensure_layout 见到什么都建，
        # 于是「填错一个字符」和「首次安装」在磁盘上没有区别 —— 状态记下来，
        # 自检要靠它把「我刚给你造了一棵空树」这件事说出口。
        self.root, self.root_state, self.root_note = check_data_root(root)
        core.ensure_layout(self.root)

    def _sd(self, project):
        try:
            return self.W.resolve_project(self.root, project)
        except self.W.WriteError as e:
            raise ToolError(str(e)) from None

    def projects(self):
        out = []
        for p in self.core.scan_projects(self.root):
            f = self.core.compile_forest(self.core.steps_dir_of(self.root, p.slug), with_files=False)
            c = {"wip": 0, "done": 0, "dead": 0}
            for s in f["steps"]:
                c[s["status"]] = c.get(s["status"], 0) + 1
            d = p.to_dict()
            d.update(steps=len(f["steps"]), counts=c, warnings=len(f["warnings"]),
                     latest=max((s["date"] for s in f["steps"] if s["date"]), default=""))
            out.append(d)
        return out

    def forest(self, project):
        self._sd(project)
        return self.core.compile_forest(self.core.steps_dir_of(self.root, project))

    def step(self, project, sid):
        f = self.forest(project)
        idx = {s["id"]: s for s in f["steps"]}
        if sid not in idx:
            raise ToolError(f"步骤 {sid} 不存在")
        out = dict(idx[sid])
        chain, cur, seen = [], sid, set()
        while cur and cur in idx and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = idx[cur]["parent"]
        out["lineage"] = list(reversed(chain))
        out.update(step_context(f, sid))
        return out

    def _guard(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except self.W.WriteError as e:
            raise ToolError(str(e)) from None

    def create_project(self, name):
        return self._guard(self.W.create_project, self.root, name).to_dict()

    def update_project(self, project, payload):
        """改项目名 / 整段洞察 / 追加一条洞察 / 就地改一条既有洞察。

        洞察那一条走 W.add_insight / W.update_insight 而不是 update_project，
        是为了把**分配到的 id** 带回去（update_project 只把它挂在返回对象的属性上，
        进不了 to_dict）。不知道刚写的那条叫 p3，agent 就说不出下一句「· 取代 p3」。
        """
        info: dict[str, Any] = {}
        a = payload.get("add_insight")
        if a:
            lang = a.get("lang", "")
            if a.get("id"):
                info = self._guard(self.W.update_insight, self.root, project, a["id"],
                                   text=a.get("text"), supersedes=a.get("supersedes"), lang=lang)
            else:
                info = self._guard(self.W.add_insight, self.root, project, a.get("kind"),
                                   a.get("text", ""), supersedes=a.get("supersedes") or "", lang=lang)
        if payload.get("name") is not None or payload.get("insights") is not None:
            p = self._guard(self.W.update_project, self.root, project,
                            name=payload.get("name"), insights=payload.get("insights"))
            out = p.to_dict()
        else:
            hit = next((x for x in self.core.scan_projects(self.root) if x.slug == project), None)
            if hit is None:
                raise ToolError(f"项目 {project} 不存在")
            out = hit.to_dict()
        if info:
            out["insight"] = info
        return out

    def create(self, project, payload):
        step, created = self._guard(
            self.W.create_step, self._sd(project),
            parent=payload.get("parent"), title=payload.get("title", ""),
            status=payload.get("status", "wip"), body=payload.get("body"),
            date=payload.get("date", ""), commit=payload.get("commit", ""),
            author=payload.get("author", ""), key=payload.get("key", ""),
            tags=payload.get("tags"), paths=payload.get("paths"),
            inputs=payload.get("inputs"), code=payload.get("code"),
            lang=payload.get("lang", ""),
            # 互斥候选多半是同一次想清楚之后一起建出来的（「A 和 B 只能选一条」）。
            # 不在建的时候收下 branch/decision，就得建完再 PATCH 一次，而
            # decision 那句话是整件事里唯一推导不出来的信息，多一道摩擦就永远空着。
            branch=payload.get("branch", ""), decision=payload.get("decision", ""),
            # 「这一步进不进最终流程」偶尔动手之前就知道（「拿来试试看的」）。
            # 收不下的话那句必填的理由就得等一次 PATCH，而多一道摩擦它就永远空着。
            pipeline=payload.get("pipeline", ""),
            # 章节更急：这一行的作用是**开一条新线**（「下面开始做消融了」），
            # 而它只该写在那条线的第一步上。建完再 PATCH 一次的话，那一步已经
            # 在别的章节里躺过一轮，中间任何一次读都会把它算进上一章。
            chapter=payload.get("chapter", ""),
        )
        d = step.to_dict()
        d["created"] = created
        return d

    def update(self, project, sid, patch):
        return self._guard(self.W.update_step, self._sd(project), sid, patch).to_dict()

    def move(self, project, sid, payload):
        return self._guard(self.W.move_step, self._sd(project), sid,
                           payload.get("parent"), payload.get("reason", ""),
                           by=payload.get("author", ""), date=payload.get("date", ""),
                           expect=payload.get("expect", ""))

    def check_path(self, project, sid, payload):
        return self._guard(self.W.record_path_check, self._sd(project), sid,
                           payload.get("loc", ""), exists=bool(payload.get("exists")),
                           date=payload.get("date", ""), size=payload.get("size"),
                           n=payload.get("n"))

    def delete(self, project, sid, payload):
        return self._guard(self.W.delete_step, self._sd(project), sid,
                           payload.get("reason", ""), by=payload.get("by", ""),
                           date=payload.get("date", ""))

    def attach(self, project, sid, data, name, mime):
        return self._guard(self.W.attach_auto, self._sd(project), sid, data, filename=name or "", mime=mime)

    # 翻译这三条**不碰原文**：write_translation / write_project_translation 只写
    # note.<lang>.md / project.<lang>.md，note.md 一个字节都不动。远端后端那三条
    # 打的是同名 REST 端点，两个门面走的是同一个 trace_write 函数。

    def translate(self, project, sid, lang, payload):
        return self._guard(self.W.write_translation, self._sd(project), sid, lang,
                           title=payload.get("title", ""), body=payload.get("body", ""),
                           expect=payload.get("expect", ""))

    def translate_project(self, project, lang, payload):
        self._sd(project)          # 项目不存在时给和别的工具一样的报错
        return self._guard(self.W.write_project_translation, self.root, project, lang,
                           name=payload.get("name", ""), body=payload.get("body", ""),
                           expect=payload.get("expect", ""))

    def untranslated(self, project, lang):
        lang = self._guard(self.W.norm_lang, lang)
        p = next((x.to_dict() for x in self.core.scan_projects(self.root) if x.slug == project), None)
        return untranslated_report(self.forest(project), p, lang)

    def _display_name(self, project: str) -> str:
        """显示名。和目录名可以不一样（改显示名不动目录名，已发出去的链接才不会失效），
        而导出的抬头要的是显示名——那是一份要进论文的产物。"""
        return next((x.name for x in self.core.scan_projects(self.root)
                     if x.slug == project), project)

    def pipeline(self, project, chapter: str = ""):
        """定稿流程。**和 REST 的 /pipeline 走同一个 pipeline_payload**。

        `compute_pipeline({}, [])` 那个 fallback 不是形式：一个 `result:` 都没声明
        的项目，forest 里刻意**整个 pipeline 键都不出现**（现存项目必须完全无感），
        于是那条「教你怎么办」的 info 级诊断只有主动问起来的这条路上拿得到。
        """
        f = self.forest(project)
        return pipeline_payload(f, project, self.core.compute_pipeline({}, []),
                                self._display_name(project), chapter)

    def chapters(self, project):
        """章节清单。判据全在 core.compute_chapters / compute_pipeline 里，
        这里和 REST 的 /chapters 共用同一个 chapters_payload。"""
        return chapters_payload(self.forest(project), project, self._display_name(project))

    def set_result(self, project, step, note):
        self._sd(project)          # 项目不存在时给和别的工具一样的报错
        return self._guard(self.W.set_result, self.root, project, step, note)

    def drop_result(self, project, step):
        self._sd(project)
        return self._guard(self.W.drop_result, self.root, project, step)


# 配置文件的查找顺序。有它才能让插件的 .mcp.json 保持静态——
# 数据在哪是每台机器不同的，不该写死在插件清单里。
CONFIG_PATHS = ("~/.trace.json", "~/.config/trace/config.json")


def read_config() -> tuple[dict[str, Any], Path | None]:
    cands = [os.environ["TRACE_CONFIG"]] if os.environ.get("TRACE_CONFIG") else []
    for raw in cands + list(CONFIG_PATHS):
        p = Path(raw).expanduser()
        try:
            if p.is_file():
                return json.loads(p.read_text(encoding="utf-8")), p
        except (OSError, json.JSONDecodeError):
            continue
    return {}, None


def discover_token(url: str, hints) -> str:
    """服务在本机时，令牌本来就在 config.json 里，不该再让人手抄一遍。

    只有 config.json 里的 space 出现在目标 URL 里才用它的令牌——这确认了
    「这份配置就是那台服务器的配置」。否则本地留着的是另一台服务器的令牌，
    拿去用只会换来一个莫名其妙的 401。
    """
    for hint in hints:
        if not hint:
            continue
        p = Path(str(hint)).expanduser() / "config.json"
        try:
            if not p.is_file():
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        space, token = str(d.get("space") or "").strip(), str(d.get("token") or "").strip()
        if space and token and space in url:
            return token
    return ""


ROLES = ("auto", "server", "client")


def make_backend() -> HttpBackend | LocalBackend:
    cfg, src = read_config()

    role = (os.environ.get("TRACE_ROLE") or cfg.get("role") or "auto").strip().lower() or "auto"
    if role not in ROLES:
        raise ToolError(f"角色必须是 {'/'.join(ROLES)} 之一，收到 {role!r}")

    url = (os.environ.get("TRACE_URL") or cfg.get("url") or "").strip()
    data = (os.environ.get("TRACE_DATA") or cfg.get("data") or "").strip()

    # 角色说明白之后，配错了就当场报出来，而不是悄悄退回另一种模式。
    # 「我选了客户端，怎么读到的是本地空目录」这种问题最难查。
    if role == "client":
        if not url:
            raise ToolError("这台机器配成了**客户端**，但没填远端服务地址。"
                            "填 https://你的域名/t/<space> ，或者把角色改成 server / auto。")
        data = ""
    elif role == "server":
        if not data:
            raise ToolError("这台机器配成了**服务端**，但没填数据仓目录。"
                            "填步骤树所在的目录（里面是 projects/），或者把角色改成 client / auto。")
        url = ""

    if url:
        token = (os.environ.get("TRACE_TOKEN") or cfg.get("token") or "").strip()
        if not token:
            token = discover_token(url, [
                cfg.get("data"),
                os.environ.get("TRACE_DATA"),
                src.parent if src else None,
                Path(__file__).resolve().parent,
            ])
        return HttpBackend(url, token)

    data = (os.environ.get("TRACE_DATA") or cfg.get("data") or "").strip()
    if data:
        return LocalBackend(Path(data).expanduser())

    raise ToolError(
        "没有配置后端。三选一：\n"
        "  · 写 ~/.trace.json：{\"data\": \"/path/to/数据仓\"}  或  "
        "{\"url\": \"https://域名/t/<space>\", \"token\": \"…\"}\n"
        "  · 设环境变量 TRACE_DATA（本地）或 TRACE_URL + TRACE_TOKEN（远端）\n"
        "  · 设 TRACE_CONFIG 指向别处的配置文件\n"
        "环境变量优先于配置文件。"
    )


# ---------------------------------------------------------------- 缺哪些翻译


def untranslated_report(forest: dict[str, Any], project: dict[str, Any] | None,
                        lang: str) -> dict[str, Any]:
    """哪些步骤还没有 `lang` 版。**纯函数，也是这个判据的唯一一份实现。**

    三个门面共用它：REST 的 `/untranslated`、MCP 的 trace_untranslated、
    CLI 的 `trace tr`。各写一遍的话，三处对「什么叫还没翻译」的答案迟早会分家
    （最容易分的就是下面那条 native 规则），而 agent 是照着其中一个的答案去补的。

    「还没翻译」是**派生状态**：文件不存在就是没有，不存储、也不进 check 的警告
    （用户明确不要那一项）。这里只回答「还欠哪些」，不做价值判断。

    原文自己就声明了这个语言（front-matter 的 `lang: en`）的步骤**不算缺**：
    给它写一份同语言译文会和正文各说各话，trace_write 那边本来就会拒绝，
    列进来只会让 agent 去做一件必然失败的事。
    """
    lang = str(lang or "")
    steps = forest.get("steps") or []
    missing: list[dict[str, str]] = []
    translated = native = 0
    for s in steps:
        if (s.get("lang") or "") == lang:
            native += 1
        elif lang in (s.get("tr") or {}):
            translated += 1
        else:
            missing.append({"id": s.get("id", ""), "title": s.get("title", ""),
                            "status": s.get("status", "")})
    p = project or {}
    note = {
        "name": p.get("name", ""),
        "native": (p.get("lang") or "") == lang,
        "translated": lang in (p.get("tr") or {}),
    }
    note["missing"] = not (note["native"] or note["translated"])
    return {
        "project": p.get("slug") or forest.get("project") or "",
        "lang": lang,
        "total": len(steps),
        "translated": translated,
        "native": native,
        "missing": missing,
        "project_note": note,
    }


# ---------------------------------------------------------------- 三种关系

# 树上的父子边现在有两种意思（`branch:`），另外还有一种边根本不在树上（`input:` 里
# 那些「汇回」）。三样全是**派生**的，磁盘上只有每个孩子自己那句 branch: 和消费者
# 自己那行 input:。下面这些函数只做**渲染**，一条判据都不重写 —— 判据在 trace_core
# 的 compute_branch_groups / compute_merges 里，读单步和读整棵树看到的必须是同一次推导。


def open_forks(forest: dict[str, Any]) -> list[dict[str, Any]]:
    """还没做决定的岔路口：一组候选里还有两个以上没被标 dead。

    这是整件事对研究者最直接的那个信号——「我手上还有几个岔路口悬着」。
    它是**待办，不是缺陷**：同时开几条线是研究的常态，所以三个门面都不把它
    算进错误、也不计进退出码。
    """
    return [g for g in (forest.get("branch_groups") or []) if g.get("state") == "open"]


def step_context(forest: dict[str, Any], sid: str) -> dict[str, Any]:
    """单步视图额外要的两样：它属于哪一组候选、它身上有哪几条汇回边。

    `step` 自己带的 `fork` 只回答「**我**是不是分叉点」，`merge_in` / `merge_out`
    只给对端的 id。而读一步的人真正要问的是「我是不是站在某个岔路口的一条岔上、
    同组还有谁」以及「这条汇回边上的两条线是在哪里分开的」——两者都要看整片森林
    才答得出，所以在这里补齐，而不是让每个门面各自去 forest 里翻一遍。

    REST 的 `GET /steps/{id}` 和 MCP 的 LocalBackend.step 共用这一份，
    于是远端后端（照着 REST 拿数据）和本地后端渲染出来的东西逐字一样。
    """
    at_me = [g for g in (forest.get("branch_groups") or []) if sid in (g.get("options") or [])]
    out = {
        # 我是某一组候选里的一个 → 整组给出来（同组还有谁、定了没有、谁被选中）。
        "in_fork": dict(at_me[0]) if at_me else None,
        # 和本步有关的汇回边，两个方向都在里面（from/to 自带方向，不用分两个键）。
        "rejoins": [dict(m) for m in (forest.get("merges") or [])
                    if m.get("from") == sid or m.get("to") == sid],
    }
    # 章节是**从哪一步继承来的**。step 自己带的 `chapter` 只回答「我属于哪一章」和
    # 「这一行是不是写在我身上」，而读一步的人接着要问的是「那一行写在哪」——
    # 改章节名、把整支挪进别的章，动的都是那一步，不是眼前这一步。
    # 只有真有章节的项目才多这个键（现存项目一个字都不该多），所以它和 forest
    # 里那两个键同一条规矩：没人写过 `chapter:` 就整个键不出现。
    idx = {s["id"]: s for s in (forest.get("steps") or [])}
    mine = (idx.get(sid) or {}).get("chapter")
    if mine and mine.get("name"):
        cur, seen = sid, set()
        while cur and cur in idx and cur not in seen:
            seen.add(cur)
            if (idx[cur].get("chapter") or {}).get("declared"):
                break
            cur = idx[cur].get("parent")
        out["chapter_at"] = cur if cur in idx else ""
    return out


def fork_label(g: dict[str, Any]) -> str:
    """一组候选现在是什么状态，一句话。

    三态全部从 status 派生，磁盘上没有任何「选中了谁」的字段。
    **「都不行」是结论不是错误**（P4），所以措辞里不带责备。
    """
    state = g.get("state")
    # 只有一个候选时 core 算出来的是 decided（唯一一个还活着），但说成「已定」是骗人：
    # 一个候选不成其为选择，多半是另一条支漏标了。这不是另一套判据，就是 core 的
    # lone_alternative 那条诊断——只是把它说进标签里，免得清单上明晃晃写着「已定」。
    if len(g.get("options") or []) == 1:
        return "只有一个候选（还不成其为选择）"
    if state == "decided":
        return f"已定 → {g.get('chosen') or '?'}"
    if state == "abandoned":
        return "都不行（候选全部放弃——这是结论，不是窟窿）"
    return f"未决 · {len(g.get('live') or [])} 选 1"


def fmt_forks(forest: dict[str, Any], header: str, *, only_open: bool = True) -> str:
    """岔路口清单。给 MCP 的 trace_read(forks=true) 和 CLI 的 `forks` 用。"""
    groups = forest.get("branch_groups") or []
    show = open_forks(forest) if only_open else groups
    n_open = len(open_forks(forest))
    lines = [header, f"共 {len(groups)} 个岔路口，其中 {n_open} 个还没做决定。", ""]
    if not show:
        lines.append("（每个岔路口都已经有结论了——`--all` / all=true 看全部。）" if groups else
                     "（这个项目里还没有互斥候选。同一个问题的几个答案各写一句 "
                     "`branch: alternative`，再在它们的父节点上写一句 `decision:` "
                     "说清在决定什么；「又分出一条支线去试别的」不算，那是普通延伸。）")
        return "\n".join(lines)
    by_id = {s["id"]: s for s in forest.get("steps") or []}
    for g in show:
        at = g.get("at") or ""
        head = f"⑂ {at}" if at else "⑂ （森林的根之间——没有父节点能承载 decision:）"
        lines.append(f"{head}   {fork_label(g)}")
        if at:
            lines.append("   在决定什么: " + (g.get("decision")
                         or "（没写。这句话推导不出来，只能人写——"
                            "用 trace_update_step 给 " + at + " 补一个 decision）"))
        for oid in g.get("options") or []:
            s = by_id.get(oid) or {}
            note = (s.get("branch_note") or "").strip()
            lines.append(f"     · {oid:<6} [{s.get('status', '?')}] {s.get('title', '')}"
                         + (f"  —— {note}" if note else ""))
        if g.get("state") == "open":
            lines.append("     还没定不是错：同时开几条线是研究的常态。"
                         "等哪条走通了，把其余的标 status=dead 并写清为什么放弃，这个岔路口才算结掉。")
        lines.append("")
    return "\n".join(lines).rstrip("\n")


# ---------------------------------------------------------------- 渲染

ROLE_LABEL = {"input": "输入", "script": "脚本", "output": "产物", "evidence": "证据"}
CODE_KIND_LABEL = {"git": "git", "snapshot": "代码快照", "container": "容器镜像"}


def _fmt_bytes(n: Any) -> str:
    """字节数的显示形式。走 core.fmt_size，让人和网页看到的是同一串数字。"""
    try:
        import trace_core as _core  # noqa: PLC0415
        return _core.fmt_size(n)
    except Exception:
        return f"{n} 字节"


def _locations_haystack(s: dict) -> str:
    """一步里所有「东西在哪」的文本，拼成一串供 trace_search 用。

    权威实现在 core.locations_haystack；这里只是那条「trace_mcp.py 被单独拷到
    一台只有 TRACE_URL 的机器上」的老路上的退路，和 _why_is_blank / TR_STRUCT_KEYS
    是同一个处理方式。tests/test_seams_flex.py 拿同一批数据核对两份的输出逐字相同，
    漂移会当场被测出来。
    """
    try:
        import trace_core as _core  # noqa: PLC0415
        return _core.locations_haystack(s)
    except Exception:
        bits = []
        for p in s.get("paths") or []:
            bits += [str(p.get("location") or ""), str(p.get("note") or "")]
        for c in s.get("code") or []:
            bits += [str(c.get("location") or ""), str(c.get("note") or "")]
        for i in s.get("inputs") or []:
            bits.append(str(i.get("note") or ""))
        return " ".join(b for b in bits if b)


def _fork_haystack(s: dict) -> str:
    """一步里和分叉有关的人写的散文（`decision:` 和候选说明），供 trace_search 用。

    权威实现在 core.fork_haystack；这里同样只是「trace_mcp.py 被单独拷走」那条
    老路上的退路，形状和 _locations_haystack 一模一样。
    """
    try:
        import trace_core as _core  # noqa: PLC0415
        return _core.fork_haystack(s)
    except Exception:
        bits = [str(s.get("decision") or ""), str(s.get("branch_note") or "")]
        return " ".join(b for b in bits if b)


def _chapter_haystack(s: dict) -> str:
    """这一步自己写下的那一行 `chapter:`（名字 + 说明），供 trace_search 用。

    权威实现在 core.chapter_haystack；退路的理由和上面那两条一模一样。
    收的是**自己声明的**那几个字，不是继承来的归属 —— 判据是 grep：
    `grep -rn 消融实验 projects/` 命中的就是声明它的那一个文件。
    """
    try:
        import trace_core as _core  # noqa: PLC0415
        return _core.chapter_haystack(s)
    except Exception:
        ch = s.get("chapter")
        if not isinstance(ch, dict):
            return ""
        bits = [str(ch.get("name") or "") if ch.get("declared") else "",
                str(ch.get("note") or "")]
        return " ".join(b for b in bits if b)


def _matched_locations(s: dict, q: str) -> list[str]:
    """命中落在正文之外（位置、`decision:`、候选说明）时，把是哪一行说出来。

    一律用 note.md 里的原写法，于是 agent 看到的那一行和它 grep 出来的一模一样。
    """
    out = []
    for p in s.get("paths") or []:
        if q in (str(p.get("location") or "") + " " + str(p.get("note") or "")).lower():
            out.append("path: " + str(p.get("location") or "")
                       + (f"  — {p['note']}" if p.get("note") else ""))
    for c in s.get("code") or []:
        if q in (str(c.get("location") or "") + " " + str(c.get("note") or "")).lower():
            out.append(f"code: {c.get('kind', '')} | " + str(c.get("location") or ""))
    for i in s.get("inputs") or []:
        if q in str(i.get("note") or "").lower():
            out.append(f"input: {i.get('step', '')} | {i.get('note', '')}")
    # 分叉那两句人写的散文同理：命中落在 `decision:` 上而正文里一个字都没提这件事，
    # 是很常见的一种命中（「当年是在哪个岔路口纠结类别不平衡」）。不把那一行摆出来，
    # 结果就只剩一个 id 和标题，读的人（尤其 agent）判不出这是不是误命中。
    if q in str(s.get("decision") or "").lower():
        out.append("decision: " + str(s["decision"]))
    if q in str(s.get("branch_note") or "").lower():
        out.append("branch: " + str(s.get("branch") or "") + " | " + str(s["branch_note"]))
    # 命中落在这一步自己那一行 `chapter:` 上：摆出来的就是文件里那一行，
    # 和 `grep -rn 消融实验 projects/` 打出来的一模一样。
    if q in _chapter_haystack(s).lower():
        ch = s.get("chapter") or {}
        out.append("chapter: " + str(ch.get("name") or "")
                   + (f" | {ch['note']}" if ch.get("note") else ""))
    return out


def _fmt_attrs(p: dict) -> str:
    """一条 path 上机器记下来的那部分：条目数、大小、校验和、最后一次核对的结论。

    **「已确认不存在」要说成结论，不说成错误**（P4）：这一行不删，
    一个曾经装着 57 GB、现在空了的位置本身就是一条发现。
    未知属性照样列出来——半年后有人写了 `nodes=…`，工具不该把它吃掉。
    """
    bits = []
    if p.get("n") is not None:
        bits.append(f"{p['n']} 条")
    if p.get("size") is not None:
        bits.append(_fmt_bytes(p["size"]))
    # core.parse_paths 给的 checksum 是**一个字符串** "md5:7d4e1a9c"（算法在冒号左边），
    # 不是 {算法: 值} 的字典。以前这里当字典用，于是任何一条真写了 md5= 的记录
    # 都会让 trace_read 直接抛 AttributeError —— 而那恰恰是 ③ 存在的理由，
    # 也就是说这条渲染路径在最该管用的时候是坏的。attrs 里同时留着原始的 md5=/sha256=，
    # 所以下面的未知属性过滤仍然要把它们排除掉，否则会打印两遍。
    if p.get("checksum"):
        algo, _, val = str(p["checksum"]).partition(":")
        bits.append(f"{algo}={val}" if val else str(p["checksum"]))
    known = {"n", "size", "checked", "missing", "md5", "sha256"}
    for k, v in (p.get("attrs") or {}).items():
        if k not in known:
            bits.append(f"{k}={v}")
    if p.get("state") == "missing":
        bits.append(f"⚠ {p.get('missing')} 起已确认不存在（记录保留，这是结论不是笔误）")
    elif p.get("state") == "present":
        bits.append(f"{p.get('checked')} 确认还在")
    return "（" + "，".join(bits) + "）" if bits else ""


def _fmt_tree(forest: dict, header: str) -> str:
    """把森林渲染成缩进树。比返回 JSON 省 token，也更好读。"""
    steps = forest["steps"]
    if not steps:
        return header + "\n（还没有步骤）"
    depth: dict[str, int] = {}
    lines = [header, ""]
    for s in steps:
        d = 0 if not s["parent"] else depth[s["parent"]] + 1
        depth[s["id"]] = d
        extra = []
        # 三种关系放在最前面。缩进只表达「谁挂在谁下面」，表达不了「这两条只能选
        # 一条」和「那条支线的产物又回到了主路径上」—— 而 agent 看不到网页上那些
        # 括弧和曲线，缩进树是它唯一的结构视图，这里不说就等于这个功能不存在。
        if s.get("branch") == "alternative":
            note = (s.get("branch_note") or "").strip()
            extra.append("候选" + (f"：{note}" if note else ""))
        if s.get("fork"):
            extra.append("⑂ 岔路口 " + fork_label(s["fork"]))
        elif (s.get("decision") or "").strip():
            # 写了「在决定什么」却还没有候选。它不成组，所以上面那一行不会出现，
            # 而这句话是唯一只能人写的信息 —— 在树上也不留痕的话，它就彻底消失了。
            extra.append("⑂ 写了在决定什么，还没有候选")
        if s.get("merge_out"):
            extra.append("汇回→ " + " ".join(s["merge_out"]))
        if s.get("merge_in"):
            extra.append("汇回← " + " ".join(s["merge_in"]))
        t = s.get("trace") or {}
        if t.get("self"):
            extra.append(t["self"] if t.get("chain") == t["self"] else f"{t['self']}→链{t['chain']}")
        if s.get("paths"):
            # 「已确认不存在」的那几条要在树上就看得见。这一整条需求的来历就是
            # 三个目录被删了半年没人发现——只在单步详情里说，等于还是要人一步步点开。
            gone = sum(1 for p in s["paths"] if p.get("state") == "missing")
            extra.append(f"{len(s['paths'])} 路径" + (f"（{gone} 条已不存在）" if gone else ""))
        if s["files"]:
            extra.append(f"{len(s['files'])} 附件")
        if s["tags"]:
            extra.append(" ".join(s["tags"]))
        if s["author"]:
            extra.append(s["author"])
        lines.append(
            "  " * d + f"{MARK.get(s['status'], '·')} {s['id']:<5} {s['status']:<4} {s['title']}"
            + (f"   [{' · '.join(extra)}]" if extra else "")
            + (f"   {s['date']}" if s["date"] else "")
        )
    # 未决的分叉单独汇总一次。**它不是警告**（下面那一栏才是）：一个岔路口悬着
    # 是待办不是缺陷，混进警告栏只会稀释警告的分量。但它又是这棵树上最该被主动
    # 说出口的东西——「我还有几条路没做决定」，逐个节点看是看不出来的。
    todo = open_forks(forest)
    if todo:
        lines += ["", f"⑂ 还有 {len(todo)} 个岔路口没做决定（待办，不是缺陷）："]
        for g in todo:
            at = g.get("at") or "（根之间）"
            lines.append(f"  {at}  {len(g['live'])} 选 1（{' / '.join(g['live'])}）"
                         + (f" —— {g['decision']}" if g.get("decision") else ""))
        lines.append("  逐个看：trace_read(forks=true)")
    warn = [w for w in forest["warnings"]]
    if warn:
        lines += ["", f"⚠ {len(warn)} 条警告："] + [f"  [{w['where'] or w['code']}] {w['message']}" for w in warn]
    return "\n".join(lines)


def _fmt_relations(s: dict) -> list[str]:
    """单步视图里那三种关系。**读一步的人应该看得出自己站在不在一个岔路口上。**

    「子步骤: 002, 002b」这一行本身表达不了「这两个只能选一条」，而这恰恰是
    读到 002 的人最需要先知道的一件事——不知道它有个互斥的兄弟，就会把一条
    候选当成主线接着往下做。
    """
    out: list[str] = []
    g = s.get("fork")
    if g:
        out.append(f"  ⑂ 这一步是一个**决策分叉点** —— {fork_label(g)}")
        out.append("      在决定什么: " + (g.get("decision")
                   or "（没写。候选有谁、选中了谁都算得出来，唯独这句话只能人写——"
                      "请用 trace_update_step 的 decision 补上）"))
        out.append("      候选（只能选一条走下去）: " + " / ".join(g.get("options") or []))
        if g.get("state") == "open":
            out.append("      还活着: " + " / ".join(g.get("live") or [])
                       + " —— 这个岔路口还没定。同时开几条线是常态，不是错；"
                         "等哪条走通了，把其余的标 status=dead 并写清为什么放弃。")
        elif g.get("state") == "decided":
            out.append(f"      「已定」是派生的：其余候选都标了 dead，只剩 {g['chosen']}。"
                       "磁盘上没有任何「选中了谁」的字段。")
        else:
            out.append("      全部候选都已放弃 —— 这是一个结论（这条路走不通），不是缺口。")
    elif (s.get("decision") or "").strip():
        # `decision:` 写了，底下却一个候选都还没标 —— 这一步的 `fork` 是 None。
        # 不在这儿说出来的话，人（或 agent）刚写完那句话，回头一读这一步，
        # 它整个不见了：候选组是派生的，没有候选就没有组，没有组就没有这一块。
        # 于是最合理的反应是「刚才没保存上」，再写一遍，或者干脆放弃——
        # 而这一行偏偏是整套东西里唯一推导不出来、只能人写的那一句。
        out.append("  ⑂ 这一步上写着**在决定什么**: " + s["decision"].strip())
        out.append("      但底下还没有任何一步声明自己是候选，所以它现在**还不是**一个岔路口"
                   "（不成组、trace_read(forks=true) 里也不出现）。给每条候选各调一次 "
                   "trace_update_step(branch=\"alternative\") 它就立起来了；"
                   "要是这里其实没有分叉，把 decision 改成空串撤回这句话。")
    mine = s.get("in_fork")
    if mine:
        others = [o for o in (mine.get("options") or []) if o != s.get("id")]
        at = mine.get("at") or ""
        out.append(f"  ⑂ 这一步是{(at + ' 底下') if at else '森林的根之间'}"
                   f"那一组的一个**互斥候选** —— {fork_label(mine)}")
        out.append("      同组的其他候选: " + (" / ".join(others) or "（只有它一个——"
                   "多半是另一条支漏标了 branch: alternative）"))
        if (s.get("branch_note") or "").strip():
            out.append("      这个候选自己的角度: " + s["branch_note"].strip())
        if mine.get("decision"):
            out.append(f"      {at} 上写着在决定什么: {mine['decision']}")
        out.append("      同一组里**只能选一条走下去**，其余最后标 status=dead —— "
                   "所以在这条上继续做之前，先看一眼决定做了没有。")
    for m in (s.get("rejoins") or []):
        me = s.get("id")
        if m.get("from") == me:
            out.append(f"  ⇢ 汇回: 本步的产物又参与了另一条线上的 {m['to']}"
                       + (f"（两条线在 {m['at']} 分开）" if m.get("at") else "")
                       + (f" —— {'、'.join(m['notes'])}" if m.get("notes") else ""))
        else:
            out.append(f"  ⇠ 汇回: {m['from']} 的产物参与了本步"
                       + (f"（两条线在 {m['at']} 分开）" if m.get("at") else "")
                       + (f" —— {'、'.join(m['notes'])}" if m.get("notes") else ""))
    if s.get("rejoins"):
        out.append("      汇回就是一条普通的 `input:`，只是两端分属两条支线 —— "
                   "**别用 branch 去表达它**，branch 只说得了「我和我 parent 那条边」。")
    return out


def _fmt_step(project: str, s: dict) -> str:
    head = [f"{project} / {s['id']}  [{s['status']}]  {s['title']}"]
    meta = []
    for k in ("date", "commit", "author"):
        if s.get(k):
            meta.append(f"{k}={s[k]}")
    if s.get("tags"):
        meta.append("tags=" + ",".join(s["tags"]))
    if meta:
        head.append("  " + "  ".join(meta))
    head.append("  溯源: " + " → ".join(s.get("lineage", [s["id"]])))
    ch = s.get("chapter") or {}
    if ch.get("name"):
        # **归属**和**这一行写在哪**是两件事，必须分开说：拿后者当归属判断，
        # 继承来的整条子树会集体看着像未分章。
        at = s.get("chapter_at") or ""
        head.append(f"  章节: {ch['name']}"
                    + ("（`chapter:` 就写在这一步上——它是这条线的起点，"
                       "底下整棵子树都继承它；改这一行就能整条搬走）"
                       if ch.get("declared")
                       else f"（继承来的，声明在 {at or '某个祖先'}；"
                            "这一步自己没写，也不该写——重复声明只会让改名要改很多处）")
                    + (f"  —— {ch['note']}" if ch.get("note") else ""))
    t = s.get("trace") or {}
    if t:
        line = f"  可溯源性: {t['self']} {LEVELS.get(t['self'], '')}"
        if t.get("chain") and t["chain"] != t["self"]:
            line += f"，但整条链只到 {t['chain']} {LEVELS.get(t['chain'], '')}（最弱的一环是 {t['weakest']}）"
        head.append(line)
        for m in t.get("missing", []):
            head.append(f"    缺: {m}")
        r = t.get("repro")
        if r:
            head.append(f"    复现: {r['state']} {r.get('date', '')} {r.get('by', '')} — {r.get('note', '')}".rstrip(" —"))
    if s.get("lang"):
        head.append(f"  原文语言: {s['lang']}")
    if s.get("tr"):
        # 有哪些译文得说出来，否则 agent 补翻译前唯一的办法是猜或者再调一次
        # trace_untranslated；而不知道已经有 en 版就重写一遍，等于把别人的译文覆盖掉。
        head.append("  已有译文: " + ", ".join(sorted(s["tr"])) + "（正文见 note.<语言>.md）")
    if s.get("children"):
        head.append("  子步骤: " + ", ".join(s["children"]))
    if s.get("backlinks"):
        head.append("  被引用: " + ", ".join(s["backlinks"]))
    head += _fmt_relations(s)
    # 数据依赖：**两个方向都要说**。树上只看得到 parent（记录的派生关系），
    # 而「这个数字是拿谁的产物算出来的」在 input 上。agent 看不到网页，
    # 这几行是它唯一能读到数据流的地方；缺了下游那一半，就答不了
    # 「我要改 013 的产物，谁会跟着错」。
    if s.get("inputs"):
        head.append("  消费了这些步骤的产物（数据依赖，和 parent 是两回事）:")
        # 哪几行是**汇回**（对端在另一条支线上）逐行标出来。inputs 本身是文件里
        # 那几行的逐字镜像，故意没有派生字段，所以归类只能在渲染的时候贴上去。
        back = {m["from"] for m in (s.get("rejoins") or []) if m.get("to") == s.get("id")}
        for i in s["inputs"]:
            head.append(f"    ← {i['step']}" + (f"  {i['note']}" if i.get("note") else "")
                        + ("   [汇回：来自另一条支线]" if i["step"] in back else ""))
    if s.get("consumers"):
        head.append("  本步的产物被这些步骤消费: " + ", ".join(s["consumers"])
                    + "（派生，扫全项目算出来的）")
    if s.get("code"):
        head.append("  代码位置:")
        for c in s["code"]:
            attrs = " ".join(f"{k}={v}" for k, v in (c.get("attrs") or {}).items())
            head.append(f"    [{CODE_KIND_LABEL.get(c['kind'], c['kind'])}] {c['location']}"
                        + (f"  {attrs}" if attrs else "")
                        + (f"  — {c['note']}" if c.get("note") else "")
                        + ("  （由 commit: 折算而来，磁盘上没有第二份）" if c.get("from") == "commit" else ""))
    if s.get("moved"):
        head.append("  移动记录（只追加，顺序即历史）:")
        for m in s["moved"]:
            head.append(f"    {m['date']}  {m['from'] or '（根）'} → {m['to'] or '（根）'}"
                        + (f"  by {m['by']}" if m.get("by") else "")
                        + f"  —— {m['reason']}")
    if s.get("paths"):
        head.append("  外部产物（不在仓库里，只记了位置）:")
        for p in s["paths"]:
            attrs = _fmt_attrs(p)
            head.append(f"    [{KIND_LABEL.get(p['kind'], p['kind'])}]"
                        + (f"[{ROLE_LABEL[p['role']]}]" if p.get("role") in ROLE_LABEL else "")
                        + f" {p['location']}"
                        + (f"  — {p['note']}" if p.get("note") else "")
                        + (f"  {attrs}" if attrs else ""))
    if s.get("files"):
        head.append("  文件: " + ", ".join(f"{f['path']} ({f['size']}B)" for f in s["files"]))
        if any(Path(f["path"]).suffix.lower() in IMG_EXT for f in s["files"]):
            head.append("  ⓘ 图片内容你看不到，只能读正文里的图注。要看原图就取 "
                        "{base}/p/{项目}/files/{id}/{文件名}")
    return "\n".join(head) + "\n\n" + (s.get("body") or "（正文为空）")


# ---------------------------------------------------------------- 章节
#
# 一个项目内部并列的几块：主实验 / 消融实验 / 数据准备。磁盘上只有一行
# `chapter: 消融实验 | …`，写在**开启那条线的那一步**上，整棵子树沿 parent 继承。
# 判据一条都不在这里——归属是 core.resolve_chapters，清单是 core.compute_chapters，
# 按章切开的流程是 core.compute_pipeline 的 `chapters`。这一段只做**渲染**和
# **把两份派生join 起来**（章节清单 × 它自己那条定稿流程）。
#
# 「一步属于哪个章节」全项目只有 core.resolve_chapters 一份判据。任何地方都不要
# 拿「这一步自己写没写 chapter」当归属判断——那是 `declared`，和归属是两件事，
# 混用会让继承来的 20 步集体看着像未分章。

# 命令行和查询串上指代**未分章**那一组。
#
# 为什么要一个记号：核心把未分章那组的名字定成空串，而空串在 `?chapter=` 和
# `--chapter` 上和「没给」长得一模一样。而这一组恰恰常常是主实验——多数人只给
# 消融起了名字，主线一直没起——没有它，最该单独导一份 Methods 的那一块反而导不了。
#
# 取舍写在这里：一个**真叫 `-` 的章节**会赢过这个记号（真名优先，未分章那组退回
# 只能从整份流程里看）。`-` 通过得了写入侧的章节名校验，所以这个歧义理论上存在；
# 换一个不可能撞名的记号（比如含竖线的串）要用户在命令行上引号转义，那是拿一个
# 天天付的代价去换一个几乎不会发生的碰撞。
CHAPTER_NONE = "-"

# 空态那句话。和 PIPELINE_EMPTY_TIP 同一个路子：说给 agent 听（它手上有工具），
# 而且**不带缺失感**——没分章不是少写了什么，绝大多数项目从头到尾就是一条线。
CHAPTER_EMPTY_TIP = (
    "要分章，只在**开启那条线的那一步**上写一行 "
    "`chapter: 消融实验 | 逐个拿掉模块，对着主实验的 023 比`"
    "（trace_new_step / trace_update_step 的 chapter 参数），"
    "它底下整棵子树自动继承——**不要给每一步各标一遍**。\n"
    "章节之间**互不排斥**（主实验和消融两段 Methods 都要写）；"
    "「同一个问题的几个答案、只能选一条」不是章节，那是 branch: alternative。"
)


def chapter_label(name: str) -> str:
    """章节名的显示形态。未分章那组的名字是空串，直接打出来是一片空白。"""
    return name or "（未分章）"


def _pick_chapter(groups: list[dict[str, Any]], want: str) -> dict[str, Any] | None:
    """按名字从一份分组里挑一组。挑不中回 None（由调用方决定怎么说）。

    **不做任何模糊匹配**（大小写折叠、前缀、相似度）：章节靠同名成立，`Ablation`
    和 `ablation` 在这套系统里就是两个章节（core 会为此报一条 near_duplicate），
    这里替人猜一次，等于让「导出的是哪一章」取决于猜法——而其中一份会进论文。
    """
    hit = next((g for g in groups if g.get("name") == want), None)
    if hit is None and want == CHAPTER_NONE:
        # 真名优先（见 CHAPTER_NONE 那段的取舍）：一个**真叫 `-`** 的章节存在时，
        # 上面那一行已经命中了它，这里的记号不会把它抢走。
        hit = next((g for g in groups if g.get("name") == ""), None)
    return hit


def chapters_payload(forest: dict[str, Any], project: str, name: str = "") -> dict[str, Any]:
    """章节清单。三个门面（REST 的 /chapters、MCP 的 trace_read(chapters=true)、
    CLI 的 `chapter`）共用这一份。

    做的唯一一件事是 **join**：core 给了两份派生——章节清单（谁属于哪一章、多少步、
    等级、最弱一步）和按章切开的定稿流程（这一章有哪几个 `result:`）——而人问的
    「消融做到哪了」需要同时看这两份。join 一次胜过每个门面各 join 一次。

    一个 `chapter:` 都没写的项目：`forest["chapters"]` 整个键不存在，这里回
    `declared: False` 和一份空清单，**不报任何警告**——现存项目全是这个状态。
    """
    ch = forest.get("chapters") or {}
    groups = {g["name"]: g for g in ((forest.get("pipeline") or {}).get("chapters") or [])}
    rows = []
    for c in ch.get("chapters") or []:
        g = groups.get(c["name"])
        row = dict(c)
        # 「有没有成果声明」是这份清单最要紧的一列：没有 `result:` 的章节推不出
        # 自己那段 Methods，而那正是分章之后最想要的东西。
        row["results"] = list(g["results"]) if g else []
        row["pipeline"] = ({"n": len(g["order"]), "level": g["level"], "weakest": g["weakest"],
                            "external": list(g["external"]), "dead": list(g["dead"]),
                            "weak": list(g["weak"])} if g else None)
        rows.append(row)
    un = groups.get("")
    return {
        "project": project,
        "name": name or project,
        "declared": bool(ch.get("declared")),
        "chapters": rows,
        # 未分章那一组**不是一个章节**，所以不混进上面那份清单（core 也没给它算
        # 等级和状态分布）。但它常常就是主实验，所以它有几步、有没有自己的成果
        # 得说出来——否则按章节读这个项目的人会以为那些步骤不存在。
        "unassigned": list(ch.get("unassigned") or []),
        "unassigned_results": list(un["results"]) if un else [],
        "crossings": list(ch.get("crossings") or []),
        "diagnostics": list(ch.get("diagnostics") or []),
        "titles": {s.get("id", ""): s.get("title", "") for s in (forest.get("steps") or [])},
    }


def fmt_chapters(payload: dict[str, Any]) -> str:
    """章节清单的文本视图。MCP 的 trace_read(chapters=true) 和 CLI 的 `chapter` 共用。"""
    project = payload.get("name") or payload["project"]
    titles = payload.get("titles") or {}
    if not payload["declared"]:
        return (f"{project} · 还没有分章节。**这是常态，不是缺陷**——绝大多数项目"
                "从头到尾就是一条线。\n\n" + CHAPTER_EMPTY_TIP)
    rows = payload["chapters"]
    lines = [f"{project} · {len(rows)} 个章节",
             "（章节是同一个项目里**并列**的几块，主实验一块、消融一块，"
             "**互不排斥，都要留着**；「同一个问题的几个互斥候选、只能选一条」是"
             "**分叉**，那是 branch: alternative，不是章节。）",
             ""]
    for c in rows:
        head = (f"  ▸ {chapter_label(c['name'])}   {c['n']} 步  "
                f"done {c['status']['done']} / wip {c['status']['wip']} / dead {c['status']['dead']}"
                f"  ·  {c['level']} {LEVELS.get(c['level'], '')}")
        if c.get("weakest"):
            head += f"（最弱一环 {c['weakest']}）"
        lines.append(head)
        if c.get("note"):
            lines.append(f"      {c['note']}")
        lines.append("      声明在 " + "、".join(c["declared_at"])
                     + f"；入口 {'、'.join(c['roots'])}"
                     + ("（横跨几棵树，所以不止一个）" if len(c["roots"]) > 1 else ""))
        if c["results"]:
            g = c["pipeline"] or {}
            lines.append(f"      ★ 成果 {'、'.join(c['results'])} → 这一章自己的定稿流程 "
                         f"{g.get('n', 0)} 步 · {g.get('level', '')} "
                         f"{LEVELS.get(g.get('level') or '', '')}"
                         + (f"（其中 {len(g['external'])} 步借自别的章节）"
                            if g.get("external") else ""))
        else:
            lines.append("      ☆ 这一章还没有 `result:`，所以推不出它自己那段 Methods"
                         "（用 trace_result 标一个，或者它本来就不该有成果）")
        lines.append(f"      步骤 {' → '.join(c['steps'])}")
        lines.append("")
    if payload["unassigned"]:
        lines.append(f"  ▸ {chapter_label('')}   {len(payload['unassigned'])} 步 —— "
                     "没有哪个祖先声明过章节。**不是缺陷**，多数项目的主线本来就没起名字"
                     + (f"；它自己也有成果：{'、'.join(payload['unassigned_results'])}"
                        if payload["unassigned_results"] else "")
                     + f"。按章节导它用 chapter=\"{CHAPTER_NONE}\"")
        lines.append(f"      步骤 {' → '.join(payload['unassigned'])}")
        lines.append("")
    cross = payload["crossings"]
    if cross:
        lines.append(f"跨章节的边 {len(cross)} 条（**这不该藏起来**：它说的正是"
                     "「消融是对着主结果测的」）：")
        for x in cross:
            what = ("读的是" if x["kind"] == "input" else "从这里分出去的：")
            lines.append(f"  {x['to']} ← {x['from']}  "
                         f"[{chapter_label(x['to_chapter'])} {what} "
                         f"{chapter_label(x['from_chapter'])}]"
                         + (f"  {x['note']}" if x.get("note") else "")
                         + (f"  —— {titles.get(x['from'], '')}" if titles.get(x["from"]) else ""))
        lines.append("")
    diags = payload["diagnostics"]
    if diags:
        lines.append("诊断（**一条都不影响 L0–L4**）：")
        for d in diags:
            lines.append(("  ⓘ " if d.get("level") == "info" else "  ⚠ ") + str(d.get("message", "")))
        lines.append("")
    lines.append("按章节导出：trace_pipeline(chapter=\"<章节名>\")"
                 "（Methods / 那张图 / 独立页面都跟着只出这一章）。"
                 "章节名是人起的，`grep -r \"chapter: 消融实验\"` 原样捞得到。")
    return "\n".join(lines).rstrip("\n") + "\n"


def chapter_export_name(names: list[str], stem: str = "pipeline") -> dict[str, str]:
    """按章节导出时每一章的**文件名**（不含扩展名）。`build` 用它。

    章节名**不是路径安全的**，绝不能拿它直接拼文件名：它合法地可以是
    `主实验/数据准备`（设计要求按 `/` 分组显示）、`CON`、`..`、`note.md`。
    所以派生文件名要过三道：slugify（`/`、`..`、尾随点都被中和）、Windows 保留名、
    **去重**——写入侧刻意不折叠大小写（`Ablation` / `ablation` 是两个章节），
    于是两个不同章节能 slug 成同一个名字，用它在清单里的序号消歧，
    绝不让后一份静默盖掉前一份。

    slugify / WIN_RESERVED 都在 trace_write 里，这里**延迟 import**：这个文件
    在只有 TRACE_URL 的机器上是单独拷过去的，那台机器上没有 trace_write，
    而它也不会去写本地文件（导出到磁盘只发生在 CLI 的 `build` 那一侧）。
    """
    import trace_write as _W  # noqa: PLC0415

    out: dict[str, str] = {}
    used: set[str] = set()
    for i, name in enumerate(names):
        slug = _W.slugify(name) if name else "unassigned"
        if slug in _W.WIN_RESERVED:
            # `con.svg` 在 Windows 上打开的是设备不是文件——写出去不报错，读回来是空的。
            slug += "-ch"
        # 消歧要**一直加到真的没人占**为止。只试一次的话，序号本身能撞上另一个
        # 章节的真名：`a` / `a-3` / `A` 三章 —— `A` 撞了 `a`，退到 `a-3`，
        # 而 `a-3` 正是第二章的文件名，于是第三章静默盖掉第二章那份 Methods。
        cand, k = f"{stem}-{slug}", i + 1
        while cand in used:
            cand = f"{stem}-{slug}-{k}"
            k += 1
        used.add(cand)
        out[name] = cand
    return out


# ---------------------------------------------------------------- 定稿流程
#
# 一个项目有**两条路径**，这一段全部服务于把它们分开：
#
#   · **开发路径** ＝ 现在这棵树的全部（含走不通的、含还没决定的岔路口）。给自己查问题。
#   · **定稿流程** ＝ 真正产出成果的那一条链。给别人照着做、给论文 Methods 用。
#
# 推导全在 core.compute_pipeline 里（成员清单一个字都不存，P1）。这里只做两件事：
# 把两个门面要的那份 payload 组装成**同一个形状**，以及把它渲染成人/agent/论文
# 各自要的样子。一条判据都不重写——重写就等于 agent 看到的流程和网页上的不是一条。
#
# ═══ 三样导出的**唯一一份实现**就在下面（fmt_pipeline / pipeline_methods /
# pipeline_svg / pipeline_page）。CLI 的 `pipeline`、REST 的 `/pipeline/*`、
# MCP 的 `trace_pipeline`、`build` 的静态产物、以及网页上那三个按钮和那张图，
# 全部拿的是这几个函数的输出。
#
# **它们绝不允许有第二份实现。** 曾经有过：web/app.js 里另有一套 JS 的 SVG 和
# markdown 生成器。两份都能跑、都通过了各自的测试，输出却是两份不同的文件——
# 屏幕上讨论的是一张图，`trace_cli.py pipeline --svg` 出的是另一张，
# **而其中一份会进论文**。那一份已经删掉，网页改成取这一份的产物
# （同源 GET；静态导出没有服务端，`build` 把同一批字节灌进页面）。
#
# 上一轮这里标着「⚠ 待搬到 trace_core.py」。**这一轮判定：不搬，它就住在这。**
# 理由是那条最容易被忘掉的部署路径：`trace_mcp.py` 对 trace_core 一律**软 import**，
# 于是「只把这一个文件拷到一台只有 TRACE_URL 的机器上」今天是通的（有测试拿
# MetaPathFinder 真的挡掉 trace_core 验过）。搬进 core 之后那条路会断在 Methods
# 草稿上——而那台机器（超算上的 agent）恰恰是最需要 Methods 草稿的一台。
# 「按分工该住 core」是个整洁性论据；「远端后端拿不到草稿」是个功能损失。
# 何况 core 是**内核**（scan/parse/validate/order/lanes/tree），而这几个是**排版**：
# 它们只用到 LEVELS / ROLE_LABEL / CODE_KIND_LABEL / _fmt_attrs / _fmt_bytes
# 这几张显示用的表，一条判据都不持有——判据全在 core.compute_pipeline 里，
# 这里一个字都没重写。

def empty_pipeline() -> dict[str, Any]:
    """forest 里没有 `pipeline` 键时（＝这个项目一个 `result:` 都没声明）的兜底形状。

    键要齐，渲染侧才不必到处 `.get(..., [])`；`declared: False` 是唯一的判据。
    做成函数而不是模块常量：常量的浅拷贝会共享里面那几个空列表，一次误改就污染
    进程里所有后来的空态。

    调用方**应当**传 `core.compute_pipeline({}, [])` 当 fallback —— 只有那一份带着
    「教你怎么办」的 info 级诊断，而空态最需要的恰恰是那句话。这个函数是那条路
    走不通时（远端后端、拿不到 core）的退路。
    """
    return {
        "declared": False, "results": [], "order": [], "edges": [], "why": {},
        "levels": {}, "level": "", "weakest": "", "weak": [], "dead": [],
        "excluded": [], "included": [], "diagnostics": [],
    }

# 空态那句话的 agent 版。**刻意不镜像 core.PIPELINE_EMPTY_HINT 的原文**：那一句是
# 说给人听的（「在 project.md 里写一行」），agent 手上有工具，该被告知的是调哪个。
# 两句话说的是同一件事，但收信人不同——照抄一份反而会让 agent 去手写文件。
PIPELINE_EMPTY_TIP = (
    "这个项目还没声明成果，所以推不出定稿流程。**这是常态，不是缺陷**——\n"
    "多数项目在拿到结果之前本来就没有「哪一步是产出」这回事。\n"
    "真的有结果了，用 trace_result 把那一步标成成果，流程会自己长出来："
    "从成果沿 `input:` 反向做闭包（一步没写 input: 时退回它的 parent），剔掉 dead。\n"
    "**成员清单一个字都不存**，所以你永远不用维护它：移动一步、补一条 input:、"
    "把某支标 dead，流程下一次读就跟着变了。"
)

WHY_LABEL = {
    "result": "它就是声明出来的那个成果",
    "include": "人手写了 `pipeline: include`（闭包够不到它，但它确实是流程的一环）",
    "input": "{id} 把它的产物声明成了输入（`input:`）",
    "parent": "{id} 一条 `input:` 都没写，于是退回它接着的前一步",
}


def _chapter_slice(pipe: dict[str, Any], group: dict[str, Any],
                   of: dict[str, str]) -> dict[str, Any]:
    """把整份定稿流程**切**成一章。切分，不是重算。

    重算（对着这一章的成果再跑一遍闭包）会得到同一批步骤，但那是第二份实现——
    N 章就 N 次闭包，而 N 个闭包必然相交（同一份清洗好的数据集既喂了主结果也喂了
    消融）。core 已经在 `compute_pipeline` 里切好了（`pipeline["chapters"]`），
    这里只把那一组的 id 拿去筛整张图的边、why、levels，于是**同一步在总图和分章图
    里的位置逐字一致**——屏幕上讨论的和投出去的是同一张图。

    `level` / `weakest` / `weak` / `dead` 一律取那一组自己算好的：整份流程的等级
    是全项目最弱的一步，拿它当消融那一章的等级就是在替消融背别的章的锅。
    """
    keep = set(group["order"])
    at = {r["step"]: r for r in (pipe.get("results") or [])}
    out = dict(pipe)                       # 键序原样保留：静态导出要逐字节确定
    out["results"] = [at[s] for s in group["results"] if s in at]
    out["order"] = list(group["order"])
    out["edges"] = [e for e in (pipe.get("edges") or [])
                    if e.get("from") in keep and e.get("to") in keep]
    out["why"] = {k: v for k, v in (pipe.get("why") or {}).items() if k in keep}
    out["levels"] = {k: v for k, v in (pipe.get("levels") or {}).items() if k in keep}
    out["level"] = group["level"]
    out["weakest"] = group["weakest"]
    out["weak"] = list(group["weak"])
    out["dead"] = list(group["dead"])
    # 被剔掉的 / 人手保留的：只留属于本章的。别的章自己判死的旁支不是这一章的事，
    # 摆进消融那份 Methods 的「试过没走通」一节，读的人会以为那是消融试的。
    out["excluded"] = [x for x in (pipe.get("excluded") or [])
                       if of.get(x.get("step", ""), "") == group["name"]]
    out["included"] = [s for s in (pipe.get("included") or [])
                       if of.get(s, "") == group["name"]]
    return out


def pipeline_payload(forest: dict[str, Any], project: str,
                     fallback: dict[str, Any] | None = None,
                     name: str = "", chapter: str = "") -> dict[str, Any]:
    """两个门面（REST 的 /pipeline、MCP 的 LocalBackend.pipeline）共用的那份数据。

    和 step_context 同一个理由：各拼一遍的话，远端后端（照着 REST 拿数据）和本地
    后端渲染出来的流程会在某个字段上分家，而分家的那一份正好是要写进论文的。

    `steps` 是**成员的完整步骤字典**，按 `pipeline.order` 排好。带全量而不是只带
    id，是为了让 Methods 草稿和那张图在远端模式下也生成得出来——它们要正文、要
    `code:`、要 `path:` 上的校验和，缺一样就只能退回「请自己去翻记录」。

    `project` 是**目录名**（slug，用来拼路由），`name` 是**显示名**。三样导出的
    抬头一律用 `name`：那是一份要进论文的产物，抬头写成 `pipeline-demo` 而不是
    课题名，收到的人只会以为拿错了文件。没给 `name` 就退回 slug。

    `chapter` 给了就**只出那一章**（`CHAPTER_NONE` 指未分章那一组）。按章节导出的
    入口**只有这一个**：三样导出、REST、CLI、build 全部拿这里切好的 payload，
    谁都不许在自己那侧再筛一遍——其中一份产物会进论文，两份实现迟早不一致。
    章节名不认识时抛 ToolError 并把有哪几章摆出来：章节名是人起的中文，
    打错一个字是最常见的失败方式，而「没有这一章」和「这一章是空的」长得一模一样。
    """
    pipe = forest.get("pipeline")
    if pipe is None:
        pipe = dict(fallback) if fallback is not None else empty_pipeline()
    ch_meta: dict[str, Any] | None = None
    if chapter:
        of = ((forest.get("chapters") or {}).get("of")) or {}
        known = [c["name"] for c in ((forest.get("chapters") or {}).get("chapters") or [])]
        group = _pick_chapter(pipe.get("chapters") or [], chapter)
        if group is not None:
            want = group["name"]        # 真名优先，所以这里不能反过来由记号推名字
        elif chapter in known:
            want = chapter              # 有这一章，只是它一条 `result:` 都没有
        elif chapter == CHAPTER_NONE:
            want = ""                   # 未分章那一组，而它也没有成果
        else:
            raise ToolError(
                f"这个项目里没有叫「{chapter}」的章节。"
                + (f"有的是：{'、'.join(chapter_label(k) for k in known)}"
                   f"（未分章那一组用 \"{CHAPTER_NONE}\"）。章节名一个字符不同就是两个章节，"
                   "这里**不做**大小写折叠或近似匹配——替你猜一次，导出的是哪一章就取决于猜法"
                   if known else "它一个章节都还没分。" + CHAPTER_EMPTY_TIP))
        ch_meta = {
            "name": want,
            "label": chapter_label(want),
            # 这一章借来的上游（消融吃着主实验的 023）：不是本章的成员，但少了它们
            # Methods 里就是一句断了的话。值是它**原本属于哪一章**，导出时照实标。
            "external": {sid: of.get(sid, "") for sid in (group or {}).get("external", [])},
            "known": known,
            # 这一章一条 `result:` 都没有 → 推不出它自己那段 Methods。这不是错，
            # 只是这一章还没有终点（core 那条 chapter_no_result 说的是同一件事）。
            "no_result": group is None,
        }
        if group is None:
            pipe = empty_pipeline()
        else:
            pipe = _chapter_slice(pipe, group, of)
    at = {sid: i for i, sid in enumerate(pipe.get("order") or [])}
    steps = sorted((s for s in (forest.get("steps") or []) if s.get("id") in at),
                   key=lambda s: at[s["id"]])
    out = {
        "project": project,
        "name": name or project,
        "declared": bool(pipe.get("declared")),
        "pipeline": pipe,
        "steps": steps,
        # 全部步骤的标题，不只是成员的：诊断里点名的那些（悬空的 result、被排除
        # 却仍被消费的那一步）多半**不在**流程里，只给成员标题的话，最该说清楚
        # 是哪一步的地方只剩一个光秃秃的 id。
        "titles": {s.get("id", ""): s.get("title", "") for s in (forest.get("steps") or [])},
    }
    # 没按章节要就**整个键都不出现**：一个 `chapter:` 都没写的项目，它这份 payload
    # 必须和这一轮改动之前逐字节一样（和 forest 里那两个键同一条规矩）。
    if ch_meta is not None:
        out["chapter"] = ch_meta
    return out


def _why_line(payload: dict[str, Any], sid: str) -> str:
    """「这一步凭什么在流程里」——读者第一个会问的问题。"""
    w = (payload["pipeline"].get("why") or {}).get(sid) or {}
    tpl = WHY_LABEL.get(w.get("kind") or "")
    if not tpl:
        return "闭包里够到了它"
    return tpl.format(id=w.get("id") or "?")


# ── 按章节导出时，三样导出（和文本视图）共用的这三小块 ────────────────────
#
# 它们只有这一份：抬头写哪个名字、空态说哪句话、借来的上游怎么标。三样导出各写
# 一遍的话，同一条流程在图上、在 Methods 里、在那一页 HTML 上会用三种说法称呼
# 同一件事——而收到它们的是同一个人。


def _pipe_head(payload: dict[str, Any]) -> str:
    """抬头。按章节导出时必须带上章节名，否则两份产物的抬头一模一样，
    收到「主实验」和「消融」两份 Methods 的人分不出哪份是哪份。"""
    project = payload.get("name") or payload["project"]
    ch = payload.get("chapter")
    return f"{project} · {ch['label']}" if ch else project


def _pipe_empty_tip(payload: dict[str, Any]) -> str:
    """空态那句话。按章节问的时候要说的是**这一章**没有成果，
    而不是「这个项目还没声明成果」——后者在别的章有成果时是句假话。"""
    ch = payload.get("chapter")
    if not ch:
        return PIPELINE_EMPTY_TIP
    return (f"章节「{ch['label']}」里一条 `result:` 都没有，所以推不出它自己那段流程。"
            "**这不是缺陷**——一章可以只是探索，成果在别的章。\n"
            "论文里主实验和消融本来就是两段 Methods；想要这一段，"
            "用 trace_result 把这一章的那一步标成成果（id 不按章节重编号，"
            "写它在整个项目里的那个 id）。")


def _external_tag(payload: dict[str, Any], sid: str) -> str:
    """这一步是**借来的**（属于别的章节，但本章的流程够到了它）。

    标出来而不是藏起来：消融当然要吃主实验的产物，那条跨章节的边说的正是
    「消融是对着主结果测的」。不标的话，读消融那份 Methods 的人会以为
    那几步是消融自己做的。
    """
    src = ((payload.get("chapter") or {}).get("external") or {}).get(sid)
    if src is None:
        return ""
    return f"（借自 {chapter_label(src)}）"


def _pipe_diag_lines(payload: dict[str, Any]) -> list[str]:
    """诊断，**按后果分栏**而不是按 level 字段分。

    `pipeline_no_result` 是一句邀请（info），`pipeline_weak_step` 是投稿前的待办，
    而「你的结果依赖着一条自己已经放弃的路」是必须当场说出来的事。三种混成一栏打，
    人会以为它们一样严重，然后开始整体略过这一段——那正是警告失效的方式。
    """
    chapter = payload.get("chapter")
    # 按章节导出时，**先把只提到别章步骤的那几条筛掉**。
    #
    # 这不是重算（重算就是第二份判据，而它算的是等级和「谁踩着 dead」——
    # 这两件事上出现两种说法，人会去怀疑推导本身）；这是从 core 算好的那一份里
    # 挑出与本文档有关的。一句免责声明救不了实际发生的事：一份要递给合作者的
    # 主实验 Methods，里面同时印着「L0，卡在 005」而 005 从头到尾不出现——
    # 读的人只会以为这份文档漏了两步。
    here = set(payload["pipeline"].get("order") or []) if chapter else None

    def about_here(d: dict[str, Any]) -> bool:
        if here is None:
            return True
        v = d.get("vars") or {}
        named = {x.strip() for key in ("ids", "id") for x in str(v.get(key, "")).split(",")}
        named.discard("")
        return not named or bool(named & here)   # 不点名步骤的（比如"没声明成果"）照留

    out: list[str] = []
    kept_wide = False
    for d in payload["pipeline"].get("diagnostics") or []:
        if not about_here(d):
            continue
        mark = "ⓘ " if d.get("level") == "info" else "⚠ "
        out.append(mark + str(d.get("message", "")))
        kept_wide = True
    # 留下来的那几条措辞仍是整张图的（"整条流程的等级"指的是全项目），说清这一点。
    if kept_wide and chapter:
        out.insert(0, "（下面这几条的措辞说的是**整个项目**这张流程图，"
                      "只是已经筛掉了与本章无关的那些；本章自己的等级和最弱一环见上面那一行。）")
    return out


def fmt_pipeline(payload: dict[str, Any], *, with_diagnostics: bool = True) -> str:
    """定稿流程的文本视图。MCP 的 trace_pipeline 和 CLI 的 `pipeline` 共用。

    `with_diagnostics=False` 是给 CLI 的：那一侧要把诊断**按后果**分成三栏
    （影响能不能复现 / 记录自相矛盾 / 纯提示），自己打一遍。这里再打一遍就是
    同一批话说两次，而说两次的直接后果是人开始整段略过。
    """
    p = payload["pipeline"]
    project = _pipe_head(payload)
    if not payload["declared"]:
        # 这里**不**打 diagnostics：空态时那一条（pipeline_no_result）和上面这段话
        # 说的是同一件事，只是一个说给人听、一个说给 agent 听。两句都打出来，
        # 读者会以为自己犯了两个错。
        return f"{project} · 定稿流程：还没有。\n\n" + _pipe_empty_tip(payload)

    order = p.get("order") or []
    levels = p.get("levels") or {}
    titles = payload["titles"]
    by_id = {s["id"]: s for s in payload["steps"]}
    lines = [
        f"{project} · 定稿流程 · {len(order)} 步",
        "（这是**给别人照着做**的那一条路：只有真正产出成果的步骤。"
        "全部记录——含走不通的、含还没决定的岔路口——在**开发路径**上，用 trace_read 看。）",
    ]
    if payload.get("chapter"):
        # 按章节看的时候第一句就得说清这是**一章**，不是整个项目的全部流程——
        # 否则「这个项目的定稿流程只有 4 步」会被当成事实读走。
        n_ext = len(payload["chapter"]["external"])
        lines.append(f"（只有「{payload['chapter']['label']}」这一章。"
                     "章节之间**不互斥**，别的章有它们自己的成果和自己那段 Methods；"
                     "整个项目的一张总图去掉 chapter 参数就是。"
                     + (f"其中 {n_ext} 步标着「借自…」：它们属于别的章节，"
                        "本章的数据依赖够到了它们——那正是「这一章是对着那个结果测的」。）"
                        if n_ext else "）"))
    lines += ["", "已声明的成果（**唯一写下来的事，其余全是算出来的**）："]
    for r in p.get("results") or []:
        lines.append(f"  ★ {r['step']}  {titles.get(r['step'], '')}"
                     + (f"  —— {r['note']}" if r.get("note") else "")
                     + f"   （追到 {len(r.get('members') or [])} 步）")
    lines += ["", "流程（按数据依赖拓扑排序，平局按 id 序 —— 于是两次导出可以直接 diff）："]

    # 边按「被谁吃了」索引，好在每一步下面说清它的上游从哪来。`via` 非空表示
    # 中间那几步被剔掉了（dead / pipeline: exclude），上游是**接过去**的——
    # 不说的话，读的人会以为 013 直接喂给了 023，而事实是中间隔着一条废掉的路。
    incoming: dict[str, list[dict[str, Any]]] = {}
    for e in p.get("edges") or []:
        incoming.setdefault(e["to"], []).append(e)

    for i, sid in enumerate(order, 1):
        s = by_id.get(sid) or {}
        lv = levels.get(sid, "")
        badge = []
        if sid in {r["step"] for r in (p.get("results") or [])}:
            badge.append("★成果")
        if sid == p.get("weakest"):
            badge.append("◆最弱一环")
        if s.get("status") == "dead":
            badge.append("▣dead")
        if (s.get("pipeline") or {}).get("rule") == "include":
            badge.append("人手保留")
        ext = _external_tag(payload, sid)
        if ext:
            badge.append(ext)
        lines.append(f"  {i:>2}. {sid:<6} [{lv} {LEVELS.get(lv, '')}] {s.get('title', '')}"
                     + ("   " + " ".join(badge) if badge else ""))
        lines.append(f"        凭什么在流程里: {_why_line(payload, sid)}")
        for e in incoming.get(sid, []):
            via = e.get("via") or []
            note = "，".join(e.get("notes") or [])
            lines.append(f"        ← {e['from']}"
                         + (f"  {note}" if note else "")
                         + (f"   [中间经过 {' / '.join(via)}，那几步不算流程的一部分]" if via else ""))

    scope = "这一章" if payload.get("chapter") else "整条流程"
    lines += ["", f"{scope}的可溯源等级 = 最弱的一步 = {p.get('level')} "
                  f"{LEVELS.get(p.get('level') or '', '')}"
                  + (f"，卡在 {p.get('weakest')}（{titles.get(p.get('weakest') or '', '')}）"
                     if p.get("weakest") else ""),
              "（一条链值多少看最弱的那一环——别人能不能照着做出来，由它决定。）"]
    if p.get("excluded"):
        lines.append("")
        lines.append("闭包里被剔掉的（上游已经接过去了，图上不留断口）：")
        for x in p["excluded"]:
            why = "status=dead（走不通的路不进说明书）" if x["why"] == "dead" \
                else "这一步自己写着 `pipeline: exclude`"
            lines.append(f"  ✕ {x['step']}  {titles.get(x['step'], '')}  —— {why}")
    diags = _pipe_diag_lines(payload) if with_diagnostics else []
    if diags:
        lines += ["", "诊断："] + ["  " + d for d in diags]
    return "\n".join(lines)


# ================================================================ 三个导出
#
# **这三样只有这一份实现。** CLI（trace pipeline --svg/--methods/--page）、
# 服务端（GET .../pipeline/figure.svg 等）、静态导出（build 时写进 dist/）
# 全部调下面这三个函数；网页那侧是去服务端取，不自己生成。
#
# 为什么这件事要专门说：其中一份产物**会进论文**。CLI 一份、网页一份的话，
# 两份迟早不一致，而不一致的那天你不会知道自己投出去的是哪一份。
#
# 为什么住在 trace_mcp.py 而不是 trace_core.py：
#   * 它们不属于内核那条 scan→parse→compile 的流水线——渲染一张图不是"编译";
#   * 但 CLI 和服务端都得够得着，而 pyproject 只打包三个模块
#     （trace_mcp / trace_core / trace_write），新开一个模块就得改打包清单，
#     pip 装的那条路上还会多一个能装漏的东西。
# 它们仍然是**纯函数**：只吃 payload、不碰磁盘、同样的输入逐字节相同（P3）。
# trace_core.py 顶部留了一行指路，免得下一个人在内核里再写一份。

# ------------------------------------------------ 导出①：Methods 草稿（markdown）

METHODS_PREFACE = (
    "> **这是初稿，不是成品。** 下面只把记录里**已有的事实**按 Methods 的骨架排了一遍，"
    "一句论文腔的句子都没有替你写——编出来的那种句子读着像成品，"
    "而它描述的是一次没人核对过的实验。发出去之前请逐段自己读一遍。\n"
    ">\n"
    "> 它是**派生**的：改一行 `input:`、把某一步标 dead、补一条 `result:`，"
    "重新生成就跟着变。所以别把这份文件存进仓库当第二份真相——"
    "同样的记录重新生成一次逐字节一致。"
)


def _methods_step(payload: dict[str, Any], sid: str, n: int) -> list[str]:
    s = {x["id"]: x for x in payload["steps"]}.get(sid) or {}
    p = payload["pipeline"]
    out = [f"### {n}. `{sid}` — {s.get('title', '')}", ""]
    meta = [f"凭什么在流程里：{_why_line(payload, sid)}"]
    ext = _external_tag(payload, sid)
    if ext:
        # 借来的上游必须逐步标出来，不能只在抬头说一句「有几步是借的」：读 Methods
        # 的人是一步一步读的，而「这一步是我们自己做的还是引用的主实验」正是
        # 消融那一节最容易被读错的地方。
        meta.append(f"**{ext}** —— 它不属于本章，是本章的数据依赖够到的上游"
                    "（这正是「本章是对着那个结果测的」）。")
    if s.get("date"):
        meta.append(f"日期：{s['date']}")
    if s.get("author"):
        meta.append(f"记录者：{s['author']}")
    if s.get("status") == "dead":
        meta.append("**status: dead** —— 这一步在记录里是「此路不通」，"
                    "却出现在成果的上游。写进论文之前必须解释清楚。")
    out += [f"- {m}" for m in meta] + [""]

    what = _section(s.get("body") or "", "what")
    out += ["**做了什么**（记录原文，未改写）", ""]
    out += [what.strip() if what.strip() else "_记录里这一节是空的——别人照着做不出来，补它。_", ""]

    code = s.get("code") or []
    out += ["**代码在哪**", ""]
    if code:
        for c in code:
            # `commit:` 在文件里就是一行 `commit: c1d2e3f`；core 把它折算成一条
            # **派生**的 `code: git`，位置那一段是空的。照 code 的格式印出来是
            # 「git commit=c1d2e3f」——收到草稿的人 grep 的是 `commit:`，
            # 而 G4 说的就是「删掉全部程序，grep 还能把人带回 note.md」。
            if c.get("from") == "commit":
                sha = (c.get("attrs") or {}).get("commit") or c.get("location") or ""
                out.append(f"- `commit:` {sha}"
                           + (f" — {c['note']}" if c.get("note") else ""))
                continue
            attrs = " ".join(f"{k}={v}" for k, v in (c.get("attrs") or {}).items())
            out.append(f"- {CODE_KIND_LABEL.get(c.get('kind'), c.get('kind'))}"
                       + (f" `{c['location']}`" if c.get("location") else "")
                       + (f" {attrs}" if attrs else "")
                       + (f" — {c['note']}" if c.get("note") else ""))
    else:
        out.append("- _没记_ —— 「用的是哪一版代码」答不出来，这一步就重跑不了。")
    out.append("")

    if s.get("inputs"):
        out += ["**输入（数据依赖）**", ""]
        out += [f"- `{i['step']}`" + (f" — {i['note']}" if i.get("note") else "")
                for i in s["inputs"]]
        out.append("")

    paths = s.get("paths") or []
    out += ["**产物与位置**", ""]
    if paths:
        for x in paths:
            attrs = _fmt_attrs(x)
            out.append("- " + (f"[{ROLE_LABEL[x['role']]}] " if x.get("role") in ROLE_LABEL else "")
                       + f"`{x['location']}`"
                       + (f" — {x['note']}" if x.get("note") else "")
                       + (f" {attrs}" if attrs else ""))
    else:
        out.append("- _没记_")
    out.append("")

    t = s.get("trace") or {}
    lv = (p.get("levels") or {}).get(sid, "")
    out.append(f"**可溯源**：{lv} {LEVELS.get(lv, '')}"
               + ("（**这一步是整条流程的最弱一环**）" if sid == p.get("weakest") else ""))
    for m in t.get("missing") or []:
        out.append(f"- 缺：{m}")
    out.append("")
    return out


def pipeline_methods(payload: dict[str, Any]) -> str:
    """Methods 草稿。**逐字节确定**（P3）：同样的记录永远得到同样的文本。"""
    p = payload["pipeline"]
    project = _pipe_head(payload)
    if not payload["declared"]:
        return (f"# Methods（草稿）\n\n{project} 还没有声明成果，推不出定稿流程。\n\n"
                + _pipe_empty_tip(payload) + "\n")
    titles = payload["titles"]
    order = p.get("order") or []
    out = [f"# Methods（草稿） — {project}", "", METHODS_PREFACE, ""]
    ch = payload.get("chapter")
    if ch:
        # 一份按章节导出的草稿会被直接贴进论文的某一节，所以它必须自己说清
        # 「这是哪一节、别的节在别的文件里」——否则收到两份草稿的人会以为
        # 后一份是前一份的修订版，然后只留一份。
        out += [f"> **本文只是「{ch['label']}」这一章。** 章节之间**不互斥**，"
                "别的章有它们自己的成果和自己那段 Methods（论文里本来就是两段），"
                "各自单独导一份。步骤 id 是**整个项目**里的 id，不按章节重编号，"
                "所以两段草稿里的编号可以直接对照。", ""]
    out += ["## 0. 这条流程能被追到多远", ""]
    out.append(f"- {'这一章' if ch else '整条流程'}：**{p.get('level')} "
               f"{LEVELS.get(p.get('level') or '', '')}** = 其中最弱的一步"
               + (f"，也就是 `{p['weakest']}`（{titles.get(p['weakest'], '')}）"
                  if p.get("weakest") else "")
               + "。别人能不能照着做出来，由它决定。")
    if ch and ch["external"]:
        out.append("- **借自别的章节的步骤**："
                   + "、".join(f"`{sid}`（{chapter_label(src)}）"
                               for sid, src in ch["external"].items())
                   + "。它们不属于本章，是本章的数据依赖够到的上游——"
                   "这一条正是「本章是对着那个结果测的」，别把它们写成本章自己做的。")
    if p.get("weak"):
        out.append(f"- 别人跑不起来的步骤（L0/L1）：{'、'.join('`%s`' % x for x in p['weak'])}"
                   "。补记录要从这几步补起，不是从最新那一步补起。")
    if p.get("dead"):
        out.append(f"- **流程里有已经放弃的步骤**：{'、'.join('`%s`' % x for x in p['dead'])}"
                   "。结果依赖着一条自己判定走不通的路——要么那个 dead 下错了，"
                   "要么这个依赖该换一步。这件事必须在投稿前解决。")
    out += ["", "## 0.1 声明出来的成果", "",
            "（这是整份流程里**唯一写下来的事**，其余每一步都是从它沿 `input:` "
            "反向算出来的，没有任何地方存着一份成员清单。）", ""]
    for r in p.get("results") or []:
        out.append(f"- `{r['step']}` {titles.get(r['step'], '')}"
                   + (f" — {r['note']}" if r.get("note") else "")
                   + f"（追到 {len(r.get('members') or [])} 步）")
    diags = _pipe_diag_lines(payload)
    if diags:
        out += ["", "## 0.2 生成时发现的问题", ""] + [f"- {d}" for d in diags]
    out += ["", f"## 1. 流程（{len(order)} 步，按数据依赖拓扑序）", ""]
    for i, sid in enumerate(order, 1):
        out += _methods_step(payload, sid, i)
    if p.get("excluded"):
        out += ["## 2. 闭包里被剔掉的步骤（不属于方法的一部分）", "",
                "上游已经接过去了，所以流程上没有断口；列在这里是因为"
                "「这条路试过、没走通」正是别人最想知道、而论文里最常缺的一段。", ""]
        for x in p["excluded"]:
            why = "status: dead" if x["why"] == "dead" else "`pipeline: exclude`"
            out.append(f"- `{x['step']}` {titles.get(x['step'], '')} — {why}")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# ------------------------------------------------ 导出②：一张能放进论文的图（SVG）
#
# 硬约束三条，一条都不许为了好看让步：
#   1. **自包含**——不引外部字体、图片、样式，也没有任何脚本。审稿系统会把带脚本的
#      SVG 直接拒掉，而引了外部字体的图在别人机器上会换一套字宽、排版全乱。
#   2. **黑白打印可读**——这次不许只靠颜色。全图只有黑白灰，关系靠**线型**
#      （实线/虚线）和**文字标注**表达，色觉障碍和影印件上读到的是同一张图。
#   3. **逐字节确定**（P3）——不写时间戳、不写版本号，几何全部由数据算出来。

_SVG_W = 900
_SVG_BOX_X = 48
_SVG_BOX_W = 596
_SVG_BOX_H = 52
_SVG_GAP = 44
_SVG_HEAD = 78


def _x(s: Any) -> str:
    """SVG / HTML 文本转义。`&` 必须第一个换，否则会把后面换出来的实体再转一遍。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _clip(s: str, n: int) -> str:
    """按字符数截断。CJK 比 ASCII 宽，所以这里按 2 记宽度，估宽只用来防溢出。"""
    w = 0
    out: list[str] = []
    for ch in str(s):
        w += 2 if ord(ch) > 0x2E80 else 1
        if w > n:
            out.append("…")
            break
        out.append(ch)
    return "".join(out)


def pipeline_svg(payload: dict[str, Any]) -> str:
    """定稿流程的一张图。自包含 SVG，无外部资源、无脚本，黑白打印可读。"""
    p = payload["pipeline"]
    order = p.get("order") or []
    if not payload["declared"] or not order:
        h = 120
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_SVG_W} {h}" '
                f'width="{_SVG_W}" height="{h}" font-family="ui-monospace, Menlo, Consolas, '
                f'monospace" font-size="13">\n'
                f'<rect width="{_SVG_W}" height="{h}" fill="#ffffff"/>\n'
                f'<text x="24" y="44" font-size="16">{_x(_pipe_head(payload))} · 定稿流程</text>\n'
                f'<text x="24" y="72" fill="#333333">'
                + ('这一章里还没有哪一步被声明成成果，所以推不出它自己那段流程'
                   if payload.get("chapter") else '还没有哪一步被声明成成果，所以推不出流程')
                + f'（这是常态，不是缺陷）。</text>\n</svg>\n')

    levels = p.get("levels") or {}
    results = {r["step"] for r in (p.get("results") or [])}
    by_id = {s["id"]: s for s in payload["steps"]}
    top = {sid: _SVG_HEAD + i * (_SVG_BOX_H + _SVG_GAP) for i, sid in enumerate(order)}
    at = {sid: i for i, sid in enumerate(order)}
    height = _SVG_HEAD + len(order) * (_SVG_BOX_H + _SVG_GAP) + 74

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_SVG_W} {height}" '
           f'width="{_SVG_W}" height="{height}" font-family="ui-monospace, Menlo, Consolas, '
           f'monospace" font-size="13">',
           f'<rect width="{_SVG_W}" height="{height}" fill="#ffffff"/>',
           '<defs><marker id="tip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
           'markerHeight="7" orient="auto-start-reverse">'
           '<path d="M 0 0 L 10 5 L 0 10 z" fill="#000000"/></marker></defs>',
           f'<text x="24" y="32" font-size="17">{_x(_pipe_head(payload))} · 定稿流程 · '
           f'{len(order)} 步</text>',
           f'<text x="24" y="54" fill="#333333">'
           f'{"这一章" if payload.get("chapter") else "整条流程"} {_x(p.get("level"))} '
           f'{_x(LEVELS.get(p.get("level") or "", ""))}'
           + (f'，最弱的一步是 {_x(p.get("weakest"))}' if p.get("weakest") else "")
           + '　·　★＝声明的成果　◆＝最弱一环'
           # 借来的那几步在图上必须看得出来，否则一张消融的流程图会让人以为
           # 前半截也是消融做的。灰底框，不靠颜色区分（黑白打印同样读得出）。
           + ('　·　灰底＝借自别的章节' if (payload.get("chapter") or {}).get("external") else "")
           + '</text>']

    # 边：相邻两步之间走正中的直箭头；跨步的绕到右边走一条折线，各占一条道，
    # 免得两条边叠在一起分不清谁连谁。虚线**只**表示「中间经过了被剔掉的步骤」。
    lane = 0
    for e in p.get("edges") or []:
        a, b = e.get("from"), e.get("to")
        if a not in at or b not in at:
            continue
        dashed = ' stroke-dasharray="6 4"' if e.get("via") else ""
        y1 = top[a] + _SVG_BOX_H
        y2 = top[b]
        if at[b] - at[a] == 1:
            mx = _SVG_BOX_X + _SVG_BOX_W // 2
            out.append(f'<path d="M {mx} {y1} L {mx} {y2 - 8}" stroke="#000000" fill="none"'
                       f'{dashed} marker-end="url(#tip)"/>')
            label_x, label_y = mx + 8, (y1 + y2) // 2 + 4
        else:
            rx = _SVG_BOX_X + _SVG_BOX_W + 16 + (lane % 5) * 16
            lane += 1
            out.append(f'<path d="M {_SVG_BOX_X + _SVG_BOX_W} {y1 - 16} L {rx} {y1 - 16} '
                       f'L {rx} {y2 + 16} L {_SVG_BOX_X + _SVG_BOX_W + 8} {y2 + 16}" '
                       f'stroke="#000000" fill="none"{dashed} marker-end="url(#tip)"/>')
            label_x, label_y = rx + 6, (y1 + y2) // 2
        if e.get("via"):
            out.append(f'<text x="{label_x}" y="{label_y}" font-size="11" fill="#444444">'
                       f'经 {_x(" ".join(e["via"]))}（已剔除）</text>')

    for i, sid in enumerate(order, 1):
        s = by_id.get(sid) or {}
        y = top[sid]
        # 成果用**双线框**而不是换个颜色：影印件上颜色全没了，框还在。
        width = "2.5" if sid in results else "1"
        ext = _external_tag(payload, sid)
        # 借来的那几步用灰底。灰度在影印件和黑白打印上照样在（硬约束 2 说的是
        # 「不许只靠颜色」，不是「不许有底色」），而且文字标注同时也在框里。
        fill = "#f0f0f0" if ext else "#ffffff"
        out.append(f'<rect x="{_SVG_BOX_X}" y="{y}" width="{_SVG_BOX_W}" height="{_SVG_BOX_H}" '
                   f'rx="4" fill="{fill}" stroke="#000000" stroke-width="{width}"/>')
        badge = ""
        if sid in results:
            badge += " ★成果"
        if sid == p.get("weakest"):
            badge += " ◆最弱一环"
        if s.get("status") == "dead":
            badge += " ▣dead"
        if ext:
            badge += " " + ext
        out.append(f'<text x="{_SVG_BOX_X + 12}" y="{y + 21}" font-size="13">'
                   f'{i}. {_x(sid)}　[{_x(levels.get(sid, ""))} '
                   f'{_x(LEVELS.get(levels.get(sid, ""), ""))}]{_x(badge)}</text>')
        out.append(f'<text x="{_SVG_BOX_X + 12}" y="{y + 41}" font-size="13" fill="#222222">'
                   f'{_x(_clip(s.get("title", ""), 64))}</text>')

    ly = _SVG_HEAD + len(order) * (_SVG_BOX_H + _SVG_GAP) + 8
    out.append(f'<line x1="24" y1="{ly}" x2="{_SVG_W - 24}" y2="{ly}" stroke="#888888"/>')
    out.append(f'<text x="24" y="{ly + 22}" font-size="11" fill="#333333">'
               '实线＝上一步的产物直接喂给下一步　虚线＝中间经过了不算流程的步骤'
               '（dead 或 pipeline: exclude），标注写着是哪几步</text>')
    out.append(f'<text x="24" y="{ly + 40}" font-size="11" fill="#333333">'
               '顺序＝数据依赖的拓扑序（平局按 id）。这张图是从记录算出来的，'
               '不是画出来的：改一行 input: 重新生成即可。</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ------------------------------------------------ 导出③：能发给合作者的独立页面

def pipeline_page(payload: dict[str, Any], title: str = "") -> str:
    """一页 HTML：那张图 + Methods 草稿。**只含定稿流程**，不含开发路径。

    没有脚本、没有外部资源、没有构建步骤——发过去就能双击打开，断网也行。
    正文用 `<pre>` 原样摆着那份 markdown 而不是再渲染一遍：多一个渲染器就多一份
    会和 Methods 草稿分家的实现，而分家的那一份正好是合作者读到的那一份。
    """
    head = title or f"{_pipe_head(payload)} · 定稿流程"
    return (
        '<!doctype html>\n<html lang="zh"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_x(head)}</title>\n<style>\n"
        "body{margin:0;padding:32px;font:15px/1.7 system-ui,-apple-system,'Segoe UI',sans-serif;"
        "color:#111;background:#fff;max-width:960px}\n"
        "h1{font-size:22px;margin:0 0 4px}\n"
        "p.lead{color:#555;margin:0 0 24px}\n"
        "svg{max-width:100%;height:auto;border:1px solid #ddd;border-radius:6px}\n"
        "pre{white-space:pre-wrap;word-break:break-word;background:#f7f7f7;border:1px solid #e5e5e5;"
        "border-radius:6px;padding:16px;font:13px/1.6 ui-monospace,Menlo,Consolas,monospace;"
        "overflow-x:auto}\n"
        "@media print{body{padding:0}svg,pre{border:none;background:#fff}}\n"
        "</style></head><body>\n"
        f"<h1>{_x(head)}</h1>\n"
        '<p class="lead">这一页只有<strong>产出成果的那条链</strong>——给别人照着做用。'
        "走不通的路、还没决定的岔路口都在开发路径上，不在这里。"
        # 一页 HTML 是**发给合作者**的那一份，收到的人手上没有别的上下文。
        # 不说这一句，他会把「消融那一章」读成「这个课题的全部方法」。
        + (f"<br>本页只是<strong>{_x(payload['chapter']['label'])}</strong>这一章；"
           "章节之间不互斥，别的章各有各的成果，各自单独导一份。"
           if payload.get("chapter") else "")
        + "</p>\n"
        + pipeline_svg(payload)
        + f"\n<pre>{_x(pipeline_methods(payload))}</pre>\n</body></html>\n"
    )


def _section(body: str, key: str) -> str:
    """按语义键取正文的一节（中英标题都认）。

    走 core.section_text 而不是写死中文标题，理由和 _why_is_blank 一字不差：
    一份 `lang: en` 的记录会被中文正则整篇判成空，于是 Methods 草稿里
    「做了什么」全是「记录里这一节是空的」——而记录明明写满了。
    """
    try:
        import trace_core as _core  # noqa: PLC0415
        return _core.section_text(body, key)
    except Exception:
        zh = {"why": "为什么", "what": "做了什么", "result": "结果",
              "conclusion": "结论", "next": "下一步"}.get(key, key)
        en = {"why": "Why", "what": "What", "result": "Result",
              "conclusion": "Conclusion", "next": "Next"}.get(key, key)
        for name in (zh, en):
            m = re.search(r"##\s*%s\s*\n(.*?)(?=\n##\s|\Z)" % re.escape(name), body, re.S)
            if m and m.group(1).strip():
                return m.group(1)
        return ""


# ---------------------------------------------------------------- 工具

TOOLS: list[dict[str, Any]] = [
    {
        "name": "trace_projects",
        "description": "列出所有科研项目及其步骤数与 done/wip/dead 分布。不确定该记到哪个项目时先调这个。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "trace_new_project",
        "description": (
            "新建一个项目。装好插件后的**第一步**——没有项目就没地方记步骤。\n"
            "一个项目 = 一条独立的研究线（一篇论文、一个课题），每个项目的步骤 id 都从 001 开始。"
            "不确定该不该新建就先 trace_projects 看一眼；同一个课题的不同尝试应当是**分叉的步骤**，"
            "不是不同的项目。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "项目名，显示用，可以是中文"}},
            "required": ["name"],
        },
    },
    {
        "name": "trace_insight",
        "description": (
            "往项目的「洞察」里记一条。**这是项目级的沉淀，不属于任何单独一步**——"
            "「回译在这个数据集上一直没用」是三次尝试之后的判断，挂在哪一步都不对。\n"
            "什么时候写：一条线走完得出总体结论时、发现一个会反复咬人的坑时、"
            "冒出一个还没验证但值得记下来的想法时。\n"
            "写完一步之后顺手想一想：这一步有没有产生「项目级」的教训？有就记一条。\n"
            "带上 step 让它指回证据来源，正文里会渲染成可跳转的链接。\n"
            "**每条洞察都会拿到一个 id**（`p1`、`p2`…，写在行首的反引号里），返回值里会告诉你。"
            "后来发现当时的判断不准时，别再手写一条重复的，有两条路：\n"
            "  · 同一件事说得更准了（数字更正、指回的步骤换了）→ 给 id，就地改那一行；\n"
            "  · 结论被**新的结论取代**了 → 新记一条并给 supersedes。被取代的那条"
            "**不删除，只折叠**——「当时是这么以为的」本身是信息，删掉它，"
            "半年后的人会以为一开始就查清楚了，然后重走一遍那条弯路。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "kind": {
                    "type": "string", "enum": ["idea", "works", "fails", "pitfall"],
                    "description": ("idea＝核心想法（还没验证的方向）；works＝有效（确认管用的）；"
                                    "fails＝无效（确认不管用的，和 works 一样重要）；"
                                    "pitfall＝坑（会反复咬人的问题，比如数据里的陷阱、环境的雷）。"
                                    "给了 id（改既有那条）时可以不填"),
                },
                "text": {"type": "string", "description": "一句话说清楚。有数字就带上数字"},
                "step": {"type": "string", "description": "证据来自哪一步，如 002c。会渲染成可跳转链接"},
                "id": {"type": "string",
                       "description": "给了就是**就地改写**这一条既有洞察（重新锚定、更正措辞），不新增。"
                                      "id 是行首反引号里那个，如 p1"},
                "supersedes": {"type": "string",
                               "description": "这条取代了哪几条（逗号分隔，如 \"p1, p2\"）。"
                                              "被取代的那条**不会被删**，只在界面上折叠起来。"
                                              "「取代了谁」只写在取代者身上，反向关系是算出来的"},
                "lang": {"type": "string",
                         "description": "写进 project.<lang>.md 那一份（洞察的译文）。不给就是原文"},
            },
            "required": ["project"],
        },
    },
    {
        "name": "trace_delete_step",
        "description": (
            "**真删**一个步骤：整个目录连同附件一起移除。这是「只追加」原则的一处例外，"
            "只用来处理**这条记录本身就不该存在**的情况——误建、测试数据、"
            "不小心粘进去的令牌或敏感信息。\n"
            "**不要用它处理失败的实验。** 试过、走不通，那是 status=dead —— 研究结论，"
            "是这套系统里最有价值的东西。往里塞垃圾会毁掉这个信号；反过来，"
            "把真实的失败删掉等于抹掉了后来人最需要的那条线索。\n"
            "两个已知代价，调用前要清楚：\n"
            "  · **id 会被重用** —— 删掉最大号之后，下一个新建的步骤会拿到同一个号，"
            "于是旧笔记里的「见 002」可能指向另一个东西。\n"
            "  · **子步骤会变成孤儿** —— 它们的 parent 指向一个不存在的 id，"
            "会被降级为根并给出警告。\n"
            "返回值会告诉你这两件事各自发生了多少。删之前先 trace_read 确认一眼。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "step": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": ("为什么删。**必填** —— 目录一删，这句话就是唯一留下来的东西，"
                                    "它会被记进项目的 project.md。写「误建的测试步骤」这种具体的，"
                                    "不要写「清理」。"),
                },
                "date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["project", "step", "reason"],
        },
    },
    {
        "name": "trace_read",
        "description": (
            "读一个项目的步骤树；给了 step 就读那一步的全文（含到根的溯源链、子步骤、"
            "被引用、附件清单）。**开始任何新实验之前先调这个**，看看这条线走到哪了、"
            "有没有人试过。\n"
            "树上的父子边有两种意思，输出里都标出来了：普通延伸（默认）和**互斥候选**"
            "（标了「候选」的那些，同一个父节点底下的候选**只能选一条走下去**）；"
            "分叉点上会标 `⑂ 岔路口` 和它定了没有。另外还有一种边不在树上——**汇回**"
            "（`汇回→` / `汇回←`），那是某条支线的产物又参与了另一条线上的某一步。\n"
            "`forks=true` 只列**还没做决定的岔路口**（一组候选里还有两个以上没被标 dead）。"
            "**那是待办，不是缺陷**——同时开几条线是研究的常态；但隔了几天回到一个项目，"
            "「我还有几个岔路口悬着」是最该先问的一句。\n"
            "`chapters=true` 列**章节**：这个项目内部并列的几块（主实验 / 消融实验 / 数据准备），"
            "各自多少步、能被追到哪一级、有没有自己的成果声明，以及跨章节的那几条边。"
            "章节**互不排斥**（都要留着，论文里本来就是两段 Methods），"
            "和「只能选一条」的分叉是两回事。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "项目 slug，见 trace_projects"},
                "step": {"type": "string", "description": "步骤 id，如 004 或 004b。不给就返回整棵树"},
                "forks": {"type": "boolean", "default": False,
                          "description": "只列岔路口（互斥候选组）：在决定什么、有哪些候选、"
                                         "定了没有。默认只列**未决**的那些；"
                                         "配 all=true 连已经定了的一起列。和 step 同时给时以 step 为准"},
                "chapters": {"type": "boolean", "default": False,
                             "description": "只列章节（同一个项目里并列的几块：主实验 / 消融 / 数据准备）："
                                            "各自的步骤、入口、步数与 done/wip/dead、可溯源等级与最弱一步、"
                                            "有没有自己的 `result:`，以及跨章节的边。"
                                            "和 step / forks 同时给时以 step > forks > chapters 为准"},
                "all": {"type": "boolean", "default": False,
                        "description": "配合 forks 用：连已经做完决定的岔路口一起列出来"},
            },
            "required": ["project"],
        },
    },
    {
        "name": "trace_search",
        "description": ("在标题、正文、标签、**产物与代码的位置**、以及各语言的译文里搜关键词。"
                        "用来回答「之前是不是试过 X」「为什么放弃了 Y」"
                        "「/orange/…/best.pt 是哪一步产出的」「谁用了 20260809 那个快照」。"
                        "英文词搜得到英文译文，命中落在译文里时结果行上会标出是哪个语言；"
                        "命中只落在位置上时会把 `path:` / `code:` / `input:` 那一行摆出来。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "project": {"type": "string", "description": "限定项目；不给就搜全部项目"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "trace_new_step",
        "description": (
            "新建一步。**开跑之前就建（status=wip），跑完再用 trace_update_step 改成 done/dead**"
            "——等跑完才记的话，跑挂的那一步就永远不存在了。\n"
            "body 里必须写「为什么」：日志能自动存、commit 能自动记，只有「我当时为什么决定"
            "试这个」必须写出来，没有这段这条记录半年后就是废的。\n"
            "可能重试的调用请带 key（幂等键），同 key 重发返回既有步骤而不是造重复。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "title": {"type": "string", "description": "一行摘要。数字放正文的「结果」小节，别塞进标题"},
                "parent": {"type": "string", "description": "从哪一步派生。必须是同项目内已存在的 id；不给就是新开一棵树"},
                "status": {"type": "string", "enum": ["wip", "done", "dead"], "default": "wip"},
                "body": {"type": "string", "description": "markdown 正文，建议五个小节：为什么 / 做了什么 / 结果 / 结论 / 下一步"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "commit": {"type": "string"},
                "author": {"type": "string", "description": f"默认 {DEFAULT_AUTHOR}"},
                "key": {"type": "string", "description": "幂等键，防止重试造出重复步骤"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "paths": {"type": "array", "items": {"type": "string"}, "description": PATHS_DESC},
                "inputs": {"type": "array", "items": {"type": "string"}, "description": INPUTS_DESC},
                "code": {"type": "array", "items": {"type": "string"}, "description": CODE_DESC},
                "lang": {"type": "string", "description": "这份记录用什么语言写的（en / zh / ja …）。**声明**出来，别让读的一侧去猜——没有它，界面对没翻译的记录只能说「这是原文」，说不出是哪种语言的原文。"},
                "branch": {"type": "string", "description": BRANCH_DESC},
                "decision": {"type": "string", "description": DECISION_DESC},
                "pipeline": {"type": "string", "description": PIPELINE_DESC},
                "chapter": {"type": "string", "description": CHAPTER_DESC},
            },
            "required": ["project", "title"],
        },
    },
    {
        "name": "trace_update_step",
        "description": (
            "改一个已有步骤：status / title / body / date / commit / tags / paths / inputs / code / "
            "branch / decision / chapter。\n"
            "**「回头把两步标成互斥候选」是 branch 的主要用法**：两条路多半是各自建出来"
            "跑了几天之后，才想明白当初那是同一个问题的两个答案。对每个候选各调一次 "
            "`branch=\"alternative\"`，再对它们的**父节点**调一次 `decision=\"…\"` 说清在决定什么。"
            "标错了也能改回来（`branch=\"\"` 就是取消候选身份）。\n"
            "**id 改不了**（会返回 409）——笔记里的 `[[003b]]` 和论文脚注里的引用永远有效，"
            "靠的就是它不动。**parent 不从这条路改**：它可以改，但必须写原因，请用 trace_move_step。\n"
            "跑完之后典型用法：status 改成 done 或 dead，同时用 append 把「结果」和「结论」追加进去。\n"
            "**失败也要记：标 dead 并写清为什么放弃**——死胡同是这个系统最有价值的部分。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "step": {"type": "string"},
                "status": {"type": "string", "enum": ["wip", "done", "dead"]},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "整段替换正文"},
                "append": {"type": "string", "description": "追加到正文末尾（比整段重写安全，推荐）"},
                "date": {"type": "string"},
                "commit": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "paths": {"type": "array", "items": {"type": "string"}, "description": "整组替换。" + PATHS_DESC},
                "lang": {"type": "string", "description": "这份记录用什么语言写的（en / zh / ja …）。补一句声明，界面就不必对读者说「这是原文」而说不出是哪种语言；空串是撤回声明。"},
                "add_paths": {"type": "array", "items": {"type": "string"},
                              "description": "追加（按位置去重），比整组替换安全。" + PATHS_DESC},
                "inputs": {"type": "array", "items": {"type": "string"},
                           "description": "整组替换。" + INPUTS_DESC},
                "add_inputs": {"type": "array", "items": {"type": "string"},
                               "description": "追加（按「步骤+说明」去重），比整组替换安全。" + INPUTS_DESC},
                "code": {"type": "array", "items": {"type": "string"},
                         "description": "整组替换。" + CODE_DESC},
                "add_code": {"type": "array", "items": {"type": "string"},
                             "description": "追加，比整组替换安全。" + CODE_DESC},
                "branch": {"type": "string",
                           "description": "空串＝取消候选身份、退回普通延伸（标错了要能改回来）。"
                                          + BRANCH_DESC},
                "decision": {"type": "string",
                             "description": "空串＝撤回这句话。" + DECISION_DESC},
                "pipeline": {"type": "string",
                             "description": "空串＝撤销这个例外，回到算出来的结论。" + PIPELINE_DESC},
                "chapter": {"type": "string",
                            "description": "空串＝撤销这条声明，回到沿 parent 继承"
                                           "（整棵子树跟着回到上一章，磁盘上不留一行空的 `chapter:`）。"
                                           "**回头补一句「这一整支其实是消融」是这个字段的主要用法**："
                                           "只改那一支的**第一步**，底下几十步跟着换章，一个字都不用动。"
                                           + CHAPTER_DESC},
                "repro": {
                    "type": "string",
                    "description": (
                        "追加一条复现记录，格式 `结果 | 日期 | 谁 | 说明`。结果三选一：\n"
                        "  runnable —— 查过了，命令/环境/种子齐全，理论上能重跑（对应 L3）\n"
                        "  verified —— 真跑过，数字在容差内对上了（L4）\n"
                        "  failed   —— 试过，跑不起来或对不上。**这条和成功一样重要**，"
                        "「checkpoint 被清了」本身就是溯源结论\n"
                        "只追加不覆盖：去年失败、今年成功是两条事实。说明里写清判据和容差。\n"
                        "例：`verified | 2026-08-08 | agent:claude | 干净 split 上重跑 3 个种子，"
                        "0.9468±0.0011，原记录 0.947`"
                    ),
                },
            },
            "required": ["project", "step"],
        },
    },
    {
        "name": "trace_move_step",
        "description": (
            "把一步（**连同它下面的整棵子树**）改挂到另一个父节点下，并留下一条永久的移动审计。\n"
            "**这不是「改错了就改」的口子，是「树形本来就画错了」的口子。** 典型场景：016 当时"
            "顺手挂在 014 下面，后来发现 014 那一支的产物从没进过 016 的计算，016 真正接着的是 013b。\n"
            "没有这个工具的时候，人会去**对调两个节点的正文**——那才是真的毁记录：创建日期和"
            "内容从此对不上号，而且一条审计都没有。能移动 + 强制写原因，历史反而完整。\n"
            "  · reason **必填**，它会永久留在 front-matter 的 `moved:` 那一行里（只追加，可以移动多次）。"
            "半年后看到一棵和创建顺序对不上的树，唯一能解释它的就是这句话——写清楚是**哪条数据依赖**"
            "决定了新的父子关系，不要写「修正结构」。\n"
            "  · **id 仍然不可改**，移动的只是它挂在哪。笔记里的 `[[016]]` 照样指得到。\n"
            "  · 移动**不改变 inputs**。数据依赖是另一件事，它本来就允许有多个来源"
            "（见 trace_update_step 的 inputs）——树形改对了，数据流该怎么写还是怎么写。\n"
            "  · 不能挂到自己或自己的后代下面（那会让整棵子树从森林上掉下来）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "step": {"type": "string", "description": "要移动哪一步，如 016"},
                "parent": {"type": "string",
                           "description": "新的父节点 id；空串 / \"root\" 表示提为根（自己开一棵树）"},
                "reason": {"type": "string",
                           "description": "**为什么移**。必填，会永久写进 note.md 的 moved: 那一行。"
                                          "写「016 的输入全部来自 013b 的口袋组成，014 的补原子产物"
                                          "从未进过下游计算」，不要写「修正结构」"},
                "date": {"type": "string", "description": "YYYY-MM-DD，不给就是今天"},
            },
            "required": ["project", "step", "reason"],
        },
    },
    {
        "name": "trace_flow",
        "description": (
            "顺着**数据依赖**看一步的上下游：它的数字是从哪些步骤的产物算出来的，"
            "以及谁又拿它的产物往下算。\n"
            "和 trace_read 看到的树是两张不同的图，别混：树画的是「我当时接着哪一步想」"
            "（parent，单父），这里画的是「这些字节从哪来」（input，DAG，可以有多个来源）。\n"
            "什么时候用：要改某一步的产物、想知道**谁会跟着错**（下游）；"
            "或者拿到一个数字，要一路问到底**它到底是怎么来的**（上游）。\n"
            "结果是**传递闭包**（上游的上游也算进来），按 id 序排，环会被自动截断。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "step": {"type": "string"},
                "direction": {"type": "string", "enum": ["up", "down", "both"], "default": "both",
                              "description": "up＝这一步消费了谁的产物；down＝谁消费了这一步的产物"},
            },
            "required": ["project", "step"],
        },
    },
    {
        "name": "trace_check_paths",
        "description": (
            "**在你这台机器上**逐条 stat 一遍某一步记的外部路径，把结果写回 `checked=` / `missing=`。\n"
            "为什么需要它：外部产物不在仓库里，只记了位置——而位置会失效。"
            "用户上一次手工核对 164 条路径时发现三个目录已经被删了（其中一个 57 GB），"
            "本该由机器自己发现。\n"
            "三条硬规矩：\n"
            "  · **记录不删**。路径没了是溯源结论（「这份数据当年在这儿，现在没了」），"
            "和 dead 一样有价值；size 也会保留——「没了的那个有 57 GB」正是要留下的信息。\n"
            "  · **够不着 ≠ 不存在**。`s3://` / `https://` 一律不探测（那等于让工具替人去访问网络），"
            "本机看不见的挂载点（比如这台机器上根本没有 /blue）也只报「够不着」、什么都不写。"
            "只有「上级目录看得见、这一条没了」才写 missing。\n"
            "  · 只动机器字段，**不碰 role 和说明**——那两样是人写的判断。\n"
            "**在能看到那些路径的机器上跑才有意义**：agent 在超算上跑就核对得了 /blue/…，"
            "跑在你笔记本上就全是「够不着」。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "step": {"type": "string"},
                "path": {"type": "string",
                         "description": "只核对这一条（写位置本身，和记录里逐字一致）。不给就核对这一步的全部路径"},
                "count": {"type": "boolean", "default": False,
                          "description": "目录还要不要数条目数（写进 n=）。默认不数：数一个"
                                         "几十万文件的目录要遍历整棵树，在网络盘上能卡很久"},
                "date": {"type": "string", "description": "YYYY-MM-DD，不给就是今天"},
            },
            "required": ["project", "step"],
        },
    },
    {
        "name": "trace_attach",
        "description": (
            "给一个步骤加附件（日志、脚本、图）。给 path 从本地磁盘读，或给 text 直接写文本。\n"
            "**图片必须给 caption**：读这条记录的人和 agent 都看不到图里的内容，"
            "图注是这张图唯一的信息来源。要写「这张图说明了什么」，不是「这是一张 loss 曲线」。\n"
            "给了 caption 就会自动在正文末尾插入引用。\n"
            "大文件（checkpoint、数据集）不要传，留在仓库外，正文里记路径 + 校验和 + 大小。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "step": {"type": "string"},
                "path": {"type": "string", "description": "本地文件路径"},
                "text": {"type": "string", "description": "直接写文本内容（和 path 二选一）"},
                "name": {"type": "string", "description": "存成什么文件名；给 text 时必填"},
                "caption": {"type": "string", "description": "图注 / 说明。图片必填"},
            },
            "required": ["project", "step"],
        },
    },
    {
        "name": "trace_translate",
        "description": (
            "给一步（或项目笔记）补一份翻译，落成 `note.<lang>.md` / `project.<lang>.md`。\n"
            "**这是唯一碰翻译的写入口，而且它永远不动原文**——一个字节都到不了 note.md。"
            "反过来，trace_new_step / trace_update_step 也没有 body_en 这类参数，"
            "原文和译文各走各的路。于是「建完步骤马上调」就是立刻翻译，"
            "「过几天回来再调」就是延迟翻译，**同一条路径，只是时机不同**，"
            "不需要预先决定用哪一种。\n"
            "省略 step 就是翻译**项目笔记**（project.<lang>.md），这时 title 写的是项目显示名。\n"
            "翻译文件的 front-matter 里**只准有 title:**（项目笔记是 name:），而且由 title 参数写，"
            "不要自己在 body 里拼 `---` 那一段。id / parent / status / date / commit / author / "
            "tags / path / repro / key 这些结构键写进译文会被**一律忽略**并产出一条警告——"
            "它们在原文里已经有了，写两份就是双真相源（改一处漏一处，两边永远不知道谁对）。\n"
            "正文的小节名要用**目标语言的那一套**，逐字一致（评级和 check 就是按这几个名字"
            "去正文里找内容的，写成 `## Why not` 等于没写）：\n" + SECTION_TABLE + "\n"
            "还欠哪些翻译用 trace_untranslated 查。缺翻译不是缺陷，不报警告；"
            "只写了中文的记录照样是可溯源的。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "lang": {"type": "string",
                         "description": "短语言码：en / ja / zh-Hant。它会直接变成文件名的一段"},
                "step": {"type": "string",
                         "description": "步骤 id，如 004b。**省略就是翻译项目笔记** project.<lang>.md"},
                "title": {"type": "string",
                          "description": "译好的标题；翻译项目笔记时它是项目显示名（写进 name:）"},
                "body": {"type": "string",
                         "description": "译好的正文，**不要带 front-matter**。翻译项目笔记时"
                                        "只会替换那四个洞察小节，`## 已删除` / `## Deleted` 逐字保留"},
                "expect": {"type": "string",
                           "description": "乐观并发控制：**这份译文自己**的 digest（不是 note.md 的）。"
                                          "不给就是不检查"},
            },
            "required": ["project", "lang"],
        },
    },
    {
        "name": "trace_pipeline",
        "description": (
            "读**定稿流程**：真正产出成果的那一条链，按数据依赖排好序，"
            "外加整条链的可溯源等级、最弱的是哪一步、以及必须说出来的几条诊断。\n"
            "**一个项目有两条路径，别把它们当成同一件事的详略两版**：\n"
            "  · **开发路径**（trace_read 看到的那棵树）＝**全部**记录，含走不通的 dead、"
            "含还没决定的岔路口。它回答「当时是怎么走到那儿的」，给自己和查问题用。\n"
            "  · **定稿流程**（这个工具）＝只有产出成果的那条链。它回答「该怎么做」，"
            "给别人照着做、给论文 Methods 用。\n"
            "**要复现一个结果、要写 Methods、要告诉别人「照着这个做」时，用这个工具，"
            "不要照着开发路径那棵树走**——那棵树上有 dead 的步骤，照着它复现，"
            "你会去重跑一条作者自己已经判定走不通的路。\n"
            "反过来，「这一步当时有 3 个候选，为什么选了它」只有开发路径答得出来，"
            "那正是两条路径都留着的意义。\n"
            "成员清单**一个字都不存**（没有任何地方维护它）：从每个 `result:` 沿 "
            "`input:` 反向做闭包，一步没写 `input:` 时退回它的 parent，剔掉 dead 与 "
            "`pipeline: exclude`。所以移动一步、补一条 input:、把某支标 dead，"
            "流程下一次读就跟着变了。\n"
            "一个成果都没声明时它会说明怎么办（用 trace_result 标）。\n"
            "**分了章节的项目：每一章各有一条自己的定稿流程**（`result:` 指的那一步在哪一章，"
            "这条流程就属于哪一章）——论文里主实验一段 Methods、消融一段，本来就是两段。"
            "给 chapter 参数就只出那一章；不给就是整个项目的一张总图（章节之间共用的准备步骤"
            "只出现一次）。有哪几章用 trace_read(chapters=true) 看。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "chapter": {
                    "type": "string",
                    "description": "只出这一章的流程（Methods 草稿也跟着只出这一章）。"
                                   "章节名照抄记录里那个字符串，**不做大小写折叠或近似匹配**；"
                                   f"未分章那一组写 \"{CHAPTER_NONE}\"（多数项目的主线没起过名字，"
                                   "它常常就是主实验）。不给＝整个项目。\n"
                                   "本章够到的、但属于别的章节的上游会标成「借自 <章节>」"
                                   "——消融当然吃着主实验的产物，那句标注正是"
                                   "「这一章是对着那个结果测的」，别把它们写成本章自己做的。",
                },
                "methods": {
                    "type": "boolean", "default": False,
                    "description": "输出 **Methods 草稿**（markdown）而不是流程概览："
                                   "按流程顺序，每一步的「做了什么」原文、代码位置"
                                   "（commit / 快照 + manifest）、产物路径与校验和、"
                                   "可溯源等级。**写论文时用这个。**\n"
                                   "它是初稿不是成品：里面只有记录里已经有的事实，"
                                   "**不要**替用户把它改写成论文腔的句子再交出去——"
                                   "编出来的句子读着像成品，而它描述的是一次没人核对过的实验。",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "trace_result",
        "description": (
            "把某一步声明成**成果**（写进 project.md 的一行 `result: <id> | <这是什么>`），"
            "或撤销一条这样的声明（drop=true）。\n"
            "**这个动作比它看起来重得多。** 它是整件事里**唯一写下来的**信息，"
            "定稿流程的每一步都是从它算出来的：它决定那条流程长什么样、"
            "决定论文 Methods 和附录里出现哪几步、决定那张导出的图上画着谁。"
            "改一次成果声明，整份方法学描述跟着变——所以它不是「顺手改个字段」，"
            "调用之前先确认这一步真的就是要报的那个结果。\n"
            "什么时候调：一条线跑通、拿到了要写进论文的那个数字时。"
            "可以声明**好几个**（主结果一条、每张消融图一条），一步一行；"
            "同一步再写一次是就地改写那句说明，不会多出一条。\n"
            "两条会被当场拒绝的写法：指向**不存在**的步骤（悬空的成果会让整条流程"
            "静默变空，而页面上一个字都不报）；把 **dead** 的一步定成成果"
            "（那是一句结论：此路不通）。反过来是允许的——已经声明的成果后来被标 dead "
            "不会被拦住（那是真会发生的事），但 trace_pipeline 会 warn 级点名。\n"
            "撤销不需要理由，也不留审计：`result:` 不是一段历史，是一个**当前指针**"
            "（「论文现在报的是哪一步」）。撤掉它，那一步和它的记录一个字都没动，"
            "开发路径上它还在原处。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "step": {"type": "string", "description": "哪一步是成果，如 023"},
                "note": {"type": "string",
                         "description": "这是什么成果，一句话。会显示在流程和 Methods 草稿的"
                                        "开头，例：\"主结果：亲和力预测 AUC 0.91\"、\"图 4 的消融\""},
                "drop": {"type": "boolean", "default": False,
                         "description": "撤销这一条成果声明（不删任何记录，只是这一步不再是终点）"},
            },
            "required": ["project", "step"],
        },
    },
    {
        "name": "trace_untranslated",
        "description": (
            "列出还没有某个语言版本的步骤（id + 标题），以及项目笔记有没有。\n"
            "**「延迟翻译」靠它落地**：隔几天回到一个项目，你得先知道还欠哪些，"
            "才谈得上补——「还没翻译」是文件不存在这个派生事实，没有任何地方存着一张待办表。\n"
            "原文自己就声明了这个语言的步骤不算缺（给它写同语言译文会被拒绝）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "lang": {"type": "string", "default": "en",
                         "description": "短语言码，默认 en"},
            },
            "required": ["project"],
        },
    },
]


def t_projects(be, _args) -> str:
    ps = be.projects()
    if not ps:
        return "还没有任何项目。"
    lines = ["项目一览：", ""]
    for p in ps:
        c = p["counts"]
        lines.append(f"  {p['slug']:<20} {p['name']:<24} {p['steps']:>3} 步  "
                     f"done {c['done']} / wip {c['wip']} / dead {c['dead']}"
                     + (f"   最近 {p['latest']}" if p.get("latest") else "")
                     + (f"   ⚠{p['warnings']}" if p.get("warnings") else ""))
    return "\n".join(lines)


def t_read(be, args) -> str:
    project = args["project"]
    if args.get("step"):
        return _fmt_step(project, be.step(project, args["step"]))
    if args.get("forks"):
        # 「还有几个岔路口没定」在整棵树里是一条要人自己扫出来的信息，而它恰好是
        # 隔几天回到一个项目时最该先问的一句。没有独立工具承载它（工具数是插件清单
        # 对外宣称的规格），所以做成 trace_read 的一种视角。
        return fmt_forks(be.forest(project), f"项目 {project} · 岔路口",
                         only_open=not args.get("all"))
    if args.get("chapters"):
        # 章节清单走同一条路、同一个理由：它是「消融那部分做到哪了、别人能不能
        # 重做」的入口，而工具数是插件清单对外宣称的规格，不为一个视角多开一个工具。
        return fmt_chapters(be.chapters(project))
    f = be.forest(project)
    out = _fmt_tree(f, f"项目 {project} · {len(f['steps'])} 步"
                       f"（● done / ○ wip / ▣ dead，缩进表示派生关系；"
                       f"「候选」＝和兄弟只能选一条，⑂＝决策分叉点，汇回＝跨支线的数据依赖）")
    # 项目级的洞察放最前面：它是这个项目里已经沉淀下来的判断，
    # 比逐步去读更快让人（和你）进入状态。
    info = next((p for p in be.projects() if p["slug"] == project), None)
    if info and (info.get("body") or "").strip():
        out = "【本项目已沉淀的洞察】\n" + info["body"].strip() + "\n\n" + out
    return out


def t_search(be, args) -> str:
    """id / 标题 / 正文 / 标签 / **产物与代码的位置** / **各语言的译文**里搜。

    译文也搜，理由和 REST 那侧的 search_hits 一字不差：这套系统的底线是
    「删掉全部程序，grep -r 还能回答『为什么放弃了 X』」，双语之后英文的 grep
    也要能回答同一个问题。`grep -r abandoned` 命中 note.en.md 而 trace_search
    命中不了的话，agent 会得到「没搜到」，而它会把这四个字读成「没试过」，
    然后重跑一条已经走死的路。命中落在译文里时结果行上会标出是哪个语言。

    `path:` / `code:` 里的位置和说明同样进搜索范围，理由是同一条：
    「/orange/…/run042/best.pt 是哪一步产出的」「谁用了 20260809 那个快照」
    是这两个键存在的**主要**用途，而 `grep -rn best.pt projects/` 一秒就答得出。
    工具比 grep 弱的那部分，恰好就是 agent 唯一够得到的那部分。

    `decision:`（在决定什么）和候选自己那句说明同理，而且更亏：`decision:` 是整套
    东西里**唯一**推导不出来、只能人写的一句话，搜不到它，agent 就会把「没搜到」
    读成「没记过」，然后重新纠结一遍同一个已经做过的决定。
    """
    q = args["query"].strip().lower()
    if not q:
        raise ToolError("query 不能为空")
    slugs = [args["project"]] if args.get("project") else [p["slug"] for p in be.projects()]
    hits = []
    for slug in slugs:
        for s in be.forest(slug)["steps"]:
            tr = s.get("tr") or {}
            hay = " ".join([s["id"], s["title"], s["body"], " ".join(s["tags"]),
                            _locations_haystack(s), _fork_haystack(s),
                            _chapter_haystack(s)]).lower()
            langs = [c for c in sorted(tr)
                     if q in ((tr[c].get("title") or "") + "\n" + (tr[c].get("body") or "")).lower()]
            if q not in hay and not langs:
                continue
            # 摘要优先取原文（它才是权威），原文没命中时才退到第一份命中的译文。
            src, where = s["body"], s["body"].lower().find(q)
            if where < 0 and langs:
                src = tr[langs[0]].get("body") or ""
                where = src.lower().find(q)
            snippet = ""
            if where >= 0:
                a = max(0, where - 60)
                snippet = ("…" if a else "") + src[a:where + 140].replace("\n", " ") + "…"
            else:
                # 命中只落在位置上（正文里一个字都没提这个路径），这时必须把命中的
                # 那一行摆出来：光给一个 id 和标题，agent 没法判断这是不是误命中，
                # 而「说不清为什么命中」的搜索结果会被当成噪音整体忽略。
                for line in _matched_locations(s, q):
                    snippet = line
                    break
            hits.append(f"{slug}/{s['id']}  [{s['status']}]  {s['title']}"
                        + (f"   （命中 {'/'.join(langs)} 译文）" if langs else "")
                        + (f"\n    {snippet}" if snippet else ""))
    if not hits:
        return f"没有搜到「{args['query']}」。"
    return f"搜到 {len(hits)} 条：\n\n" + "\n".join(hits)


_WHY = None


def _why_is_blank(body: str) -> bool:
    """「为什么」这一节是不是还空着。按小节内容判断，不靠正文长度这种粗糙启发式。

    走 core.section_text 而不是一条写死中文的正则：小节名是**中英两套**
    （`## 为什么` 和 `## Why` 是同一节），认死中文的话，一份用英文写的 note.md
    会被整篇判成「没写为什么」，于是每建一步都吃一条假警告——而假警告的代价是
    真警告也跟着被忽略。core 拿不到时（只把 trace_mcp.py 单独拷过去、走远端后端的
    机器）退回原来那条中文正则，那种机器上的记录本来也只有中文。
    """
    global _WHY
    try:
        import trace_core as _core
        text = _core.section_text(body, "why").strip()
    except Exception:
        if _WHY is None:
            _WHY = re.compile(r"##\s*为什么\s*\n(.*?)(?=\n##\s|\Z)", re.S)
        m = _WHY.search(body)
        text = m.group(1).strip() if m else ""
    return not text or text.startswith(("（", "("))   # 空的，或者还是模板里的占位括号


INSIGHT_LABEL = {"idea": "核心想法", "works": "有效", "fails": "无效", "pitfall": "坑"}


def t_insight(be, args) -> str:
    """记一条项目级洞察，或就地改写一条既有的。

    `kind` / `text` 的必填判断放在这里而不是 schema 的 required 里：给了 id 就是
    「改那一条」，这时 kind 是多余的（小节由 id 所在的位置决定），text 不给就是
    「只补一句『取代了谁』、正文不动」。写死在 required 里会逼调用方把原文抄一遍，
    而抄错一个字就成了另一条洞察。
    """
    iid = (args.get("id") or "").strip()
    text = (args.get("text") or "").strip()
    if not iid and not (args.get("kind") and text):
        raise ToolError("新记一条洞察要同时给 kind 和 text；要改既有的那条请给 id。")
    if args.get("step"):
        text = f"{text} —— [[{args['step'].strip()}]]"
    payload = {"kind": args.get("kind"), "text": text,
               "id": iid, "supersedes": args.get("supersedes") or "",
               "lang": args.get("lang") or ""}
    p = be.update_project(args["project"], {"add_insight": payload})
    info = p.get("insight") or {}
    where = f"{p['slug']}" + (f" 的 {info['lang']} 译文" if info.get("lang") else "")
    if iid:
        return (f"已改写 {where} 里的洞察 `{iid}`：{info.get('line', text)}\n"
                "被它取代的那条（如果有）一个字都没删——「当时是这么以为的」本身是信息。")
    label = INSIGHT_LABEL.get(args.get("kind"), args.get("kind"))
    out = f"已记入 {where} 的「{label}」，id 是 `{info.get('id', '?')}`：{text}"
    if args.get("supersedes"):
        out += (f"\n它取代了 {args['supersedes']}——那几条仍然留在 project.md 里，"
                "只是在界面上折叠起来。")
    else:
        out += "\n以后要更正这条判断，用 id 改写它，或者新记一条并写 supersedes，别再手写一条重复的。"
    return out


def t_delete_step(be, args) -> str:
    info = be.delete(args["project"], args["step"],
                     {"reason": args["reason"], "by": DEFAULT_AUTHOR, "date": args.get("date", "")})
    out = [f"已删除 {args['project']}/{info['id']}「{info['title']}」"
           f"（连同 {info['files_removed']} 个文件），原因已记进项目的 project.md。"]
    if info["orphaned"]:
        out.append("⚠ " + "、".join(info["orphaned"])
                   + " 的 parent 现在指向一个不存在的 id，会被降级为根并报警告。")
    if info["dangling_refs"]:
        out.append("⚠ " + "、".join(info["dangling_refs"])
                   + f" 的正文里写了 [[{info['id']}]]，现在指不到东西了。")
    if info.get("dangling_inputs"):
        # 这条比上面那条重：`[[006]]` 是给人读的一句话，`input: 006 | x.csv` 是
        # 「这些字节从哪来」的声明，可溯源性正沿着它上溯。而 id 会被重用——
        # 下一个拿到这个号的步骤会**静悄悄地**接手这些边。
        out.append("⚠ " + "、".join(info["dangling_inputs"])
                   + f" 声明了 `input: {info['id']}`（数据依赖），现在指不到东西了。"
                     "这些步骤的可溯源链会断在这里；等 id 被重用之后，它们会无声地"
                     "指向一个不相干的步骤。请去把那几行 input 改掉或删掉。")
    if info.get("dangling_results"):
        # 三条里最重的一条：这一步被声明成了**成果**，整条定稿流程从它长出来。
        # 它一没，流程静默变空（trace_pipeline 会报 dangling_result），而 id 会被
        # 重用——下一个拿到该号的步骤会无声地变成论文报的那个结果。
        out.append("⚠ project.md 里这几行成果声明现在指空了："
                   + "、".join(f"`{x}`" for x in info["dangling_results"])
                   + "。整条定稿流程就是从它们长出来的，现在推不出东西了。"
                     "用 trace_result 重新指一步，或者 drop=true 撤掉那一行——"
                     "**别放着不管**：id 会被重用，下一个拿到这个号的步骤会无声地"
                     "变成论文报的那个结果。")
    out.append(f"⚠ id {info['id']} 可能被下一个新建的步骤重用——旧笔记里对它的引用会指向别的东西。")
    return "\n".join(out)


def t_new_project(be, args) -> str:
    p = be.create_project(args["name"])
    return (f"已建项目 {p['slug']}（显示名 {p['name']}）。"
            f"接下来用 trace_new_step 记第一步，别忘了写「为什么」。")


def t_new_step(be, args) -> str:
    project = args["project"]
    existing = [p["slug"] for p in be.projects()]
    if project not in existing:
        if existing:
            raise ToolError(f"项目 {project} 不存在。已有：{', '.join(existing)}。"
                            f"确实要开一条新研究线才用 trace_new_project。")
        # 全新的数据仓，一个项目都还没有：直接建出来，省掉一次来回。
        # 已经有项目时不这么做——那种情况下项目名对不上多半是笔误。
        be.create_project(project)

    payload = {k: args[k] for k in ("parent", "title", "status", "body", "date", "commit", "key",
                                    "tags", "paths", "inputs", "code", "lang",
                                    "branch", "decision", "pipeline", "chapter")
               if k in args and args[k] not in (None, "")}
    payload.setdefault("status", "wip")
    payload["author"] = args.get("author") or DEFAULT_AUTHOR
    payload.setdefault("body", BODY_TEMPLATE)
    s = be.create(args["project"], payload)
    if s.get("created") is False:
        return f"已存在同 key 的步骤 {s['id']}（{s['title']}），没有新建。"
    tip = ""
    if _why_is_blank(payload.get("body") or ""):
        tip = ("\n⚠「为什么」还是空的。请用 trace_update_step 补上——日志能自动存、commit 能自动记，"
               "只有「我当时为什么决定试这个」必须写出来，没有这段这条记录半年后就是废的。")
    lines = [f"已创建 {args['project']}/{s['id']}  [{s['status']}]  {s['title']}" + tip]
    if payload.get("branch") or payload.get("decision"):
        lines += _fork_feedback(be, args["project"], s["id"])
    return "\n".join(lines)


def _fork_feedback(be, project: str, sid: str) -> list[str]:
    """刚动过分叉语义（或状态）之后，把**这一组现在长什么样**当场说出来。

    候选组是派生的：写下去的只有这一步自己那一行 `branch:`，「这一组有谁、定了没有」
    要扫完兄弟才知道。不当场说，调用方就得再拉一遍森林才看得见自己刚做了什么——
    而最该被看见的两种情况恰恰是**没报错**的那两种：只标了一个候选（一个候选不成其
    为选择），以及标完 dead 之后这个岔路口其实已经定了。
    """
    try:
        f = be.forest(project)
    except Exception:                       # 反馈是加分项，拿不到就闭嘴，别把主操作说成失败
        return []
    out: list[str] = []
    for g in f.get("branch_groups") or []:
        if sid not in (g.get("options") or []) and g.get("at") != sid:
            continue
        at = g.get("at") or "（森林的根之间）"
        out.append(f"⑂ {at} 这一组候选: {' / '.join(g.get('options') or [])} —— {fork_label(g)}")
        if len(g.get("options") or []) == 1:
            out.append("  一组只有一个候选＝还不是选择。另一条支多半也要标 branch=alternative，"
                       "否则这个岔路口在图上和清单里都立不起来。")
        if g.get("at") and not g.get("decision"):
            out.append(f"  {g['at']} 上还没写 decision（在决定什么）—— 那句话推导不出来，"
                       "只能人写，现在补最省事。")
    if out:
        return out
    # 一个组都没碰到，而这一步身上有 `decision:` —— 那句话刚写下去，但底下一个
    # `branch: alternative` 都还没有，它现在什么都不做。回执里不说的话，agent
    # 得到的是一句干净的「已更新」，然后在 trace_read 里再也找不到自己写的那句话
    # （候选组是派生的：没有候选就没有组），于是把它读成「没写进去」。
    me = next((x for x in f.get("steps") or [] if x.get("id") == sid), None)
    if me and (me.get("decision") or "").strip():
        out.append(f"⑂ {sid} 上记下了「在决定什么」: {me['decision'].strip()}")
        out.append("  但底下还没有任何一步声明自己是候选，所以它现在**还不是**一个岔路口。"
                   "对每个候选各调一次 trace_update_step(branch=\"alternative\") 才算立起来"
                   "——「这一组有谁」永远是从孩子自己那行 branch: 扫出来的，"
                   "父节点上绝不写候选清单。")
    return out


def t_update_step(be, args) -> str:
    project, sid = args["project"], args["step"]
    # 静默忽略比报错更糟：agent 会以为改成功了。这里和服务端保持一致。
    if "id" in args:
        raise ToolError(
            "id 不可修改。只追加是这套系统的地基——笔记里写的「见 003b」、"
            "论文脚注里的引用能一直有效，靠的就是 id 写下之后不再动。")
    if "parent" in args:
        # 语义变了：parent 可以改，但必须留下审计。这条路收不到原因，所以指过去，
        # 而不是像以前那样一口回绝——回绝的后果是人跑去对调两个节点的正文。
        raise ToolError(
            "parent 不从这条路改。它**可以**改，但必须写清为什么——请用 trace_move_step"
            "（reason 必填，会永久留在 note.md 的 moved: 那一行里）。"
            "顺带一提：如果你真正想说的是「这一步的数据来自那一步」，那是 inputs，不是 parent。")
    patch = {k: args[k] for k in ("status", "title", "date", "commit", "tags", "paths", "add_paths",
                                  "inputs", "add_inputs", "code", "add_code", "lang",
                                  # 空串是有意义的取值（取消候选身份 / 撤回那句话 /
                                  # 撤销那个例外），所以这里过滤的是 None 而不是假值。
                                  # 这张白名单是**按名字挑**的：漏一个不会报错，只会
                                  # 静默丢掉——agent 会以为改成功了，然后在导出里找不到。
                                  "branch", "decision", "pipeline", "chapter")
             if k in args and args[k] is not None}
    if args.get("repro"):
        patch["add_repro"] = [args["repro"]]
    if args.get("body") is not None and args.get("append"):
        raise ToolError("body 和 append 只能给一个")
    if args.get("body") is not None:
        patch["body"] = args["body"]
    elif args.get("append"):
        cur = be.step(project, sid)["body"] or ""
        patch["body"] = cur.rstrip("\n") + "\n\n" + args["append"].strip("\n") + "\n"
    if not patch:
        raise ToolError("没有要改的字段")
    s = be.update(project, sid, patch)
    lines = [f"已更新 {project}/{s['id']}  [{s['status']}]  {s['title']}"]
    # status 也算：把一个候选标成 dead **就是**做出选择——「已定」全靠它派生。
    # 改完不说一声，做决定的那一刻反而是整条链上唯一没有回执的一步。
    if {"branch", "decision", "status"} & set(patch):
        lines += _fork_feedback(be, project, sid)
    if "pipeline" in patch:
        # `pipeline:` 除了改变一份导出之外**在界面上不留任何痕迹**，所以这一行必须
        # 当场说清它做了什么。不说的话，回执是一句干净的「已更新」，而人唯一能验证
        # 自己没写反的办法是再去生成一遍 Methods。
        rule = str(patch["pipeline"]).split("|")[0].strip()
        lines.append({"exclude": f"{sid} 从此**不算**定稿流程的一部分（上游会被接过去，图上不留断口）。",
                      "include": f"{sid} 从此**留在**定稿流程里，连同它的上游一起。",
                      }.get(rule, f"{sid} 的 pipeline 例外已撤销，回到算出来的结论。"))
        lines.append("用 trace_pipeline 看一眼现在的流程——这个键唯一的作用就是改变它。")
    if "chapter" in patch:
        # 换章**磁盘上只动这一行**，而后果是**整棵子树**的归属跟着变（继承）。
        # 不当场说清「带走了几步」，回执就是一句干净的「已更新」，而人要发现
        # 二十步集体换了章，只能靠重新拉一遍森林。
        try:
            of = be.chapters(project)["chapters"]
        except Exception:                   # 回执是加分项，拿不到就闭嘴，别把主操作说成失败
            of = []
        now = next((c for c in of if sid in c["steps"]), None)
        if now:
            # 「跟着继承过来的」只算**自己一个字都没写**的那些：同一个章节可以在
            # 好几处各声明一次（它横跨几棵树时本来就该这样），把那几处也数进来，
            # 后面半句「它们自己一个字都没写」就成了假话。
            declared = set(now.get("declared_at") or ())
            carried = [x for x in now["steps"] if x != sid and x not in declared]
            lines.append(f"{sid} 起，这条线属于章节「{now['name']}」"
                         + (f"，同章的还有 {len(carried)} 步（沿 parent 继承，"
                            "它们自己一个字都没写）" if carried else
                            "（现在只有它自己——一个章节刚被开启时本来就只有一步）"))
            lines.append("按章节导出：trace_pipeline(chapter=\"%s\")；"
                         "有哪几章：trace_read(chapters=true)。" % now["name"])
        else:
            lines.append(f"{sid} 现在**不属于任何章节**（这一行撤销了，"
                         "它和它底下的子树回到沿 parent 继承的那一章，或者未分章）。")
    return "\n".join(lines)


def t_move_step(be, args) -> str:
    project, sid = args["project"], args["step"]
    info = be.move(project, sid, {
        "parent": args.get("parent", ""), "reason": args.get("reason", ""),
        "author": DEFAULT_AUTHOR, "date": args.get("date", "")})
    out = [f"已把 {project}/{info['id']} 从 {info['old_parent'] or '（根）'} 移到 "
           f"{info['new_parent'] or '（根）'}。",
           f"审计已写进 note.md：{info['moved']}"]
    if info.get("subtree"):
        # 「你移的是一步」和「你移的是一步和它下面的 9 步」是两个决定，
        # 事后才发现的话，已经没有第二次机会说「我不是这个意思」了。
        out.append(f"⚠ 跟着一起走的还有 {len(info['subtree'])} 个后代："
                   + "、".join(info["subtree"]))
    # 换章是移动的常见用意之一（「把这一支挪进消融」），而它**磁盘上一个字节都没变**：
    # 二十步集体转章，diff 里只有一行 `moved:`。不当场说，事后只能靠重新拉一遍森林
    # 才看得见。两头都没有章节时 move_step 给的是 None，那时一个字都不多说。
    ch = info.get("chapter")
    if ch:
        # 「还有」说的是**这一步之外**的那些：`steps` 里第一个多半就是它自己
        # （它也是靠继承换的章），照数会让「移一步」听起来像「带走了一支」。
        others = [x for x in ch["steps"] if x != info["id"]]
        out.append(f"章节：{chapter_label(ch['from'])} → {chapter_label(ch['to'])}"
                   + (f"（跟着换章的还有 {len(others)} 步：{'、'.join(others)}"
                      "——它们自己没写 chapter:，归属是继承来的）" if others else "")
                   if ch["changed"] else
                   f"章节没变：还在「{chapter_label(ch['to'])}」里。")
    out.append("id 没变，inputs 也没动——数据依赖是另一件事，要改用 trace_update_step 的 inputs。")
    return "\n".join(out)


def _flow_closure(steps: list[dict], sid: str, direction: str) -> list[str]:
    """沿数据依赖求传递闭包。返回值按 forest 的既有顺序排，不另发明一套排序。

    环是残缺数据里真实存在的（两个人分别手改了 note.md），seen 兜住它：
    「构建器必须能在残缺输入上产出部分结果」这条对查询同样适用。
    """
    by_id = {s["id"]: s for s in steps}
    if sid not in by_id:
        raise ToolError(f"步骤 {sid} 不存在")
    seen, stack = {sid}, [sid]
    while stack:
        cur = by_id.get(stack.pop())
        if cur is None:
            continue
        nxts = ([i["step"] for i in (cur.get("inputs") or [])] if direction == "up"
                else list(cur.get("consumers") or []))
        for n in nxts:
            if n in by_id and n not in seen:
                seen.add(n)
                stack.append(n)
    return [s["id"] for s in steps if s["id"] in seen and s["id"] != sid]


def t_flow(be, args) -> str:
    project, sid = args["project"], args["step"]
    f = be.forest(project)
    steps = f["steps"]
    by_id = {s["id"]: s for s in steps}
    if sid not in by_id:
        raise ToolError(f"步骤 {sid} 不存在")
    want = (args.get("direction") or "both").lower()
    me = by_id[sid]
    # 哪几条数据依赖是**汇回**（对端在另一条支线上、谁都不是谁的祖先）。判据在
    # trace_core.compute_merges 里，这里只把它标出来：同样一条 input 边，
    # 「顺着往下走的那一步读了上一步的产物」和「另一条支线的产物回到了这条路上」
    # 在图上是两件事，混成一样的话「那条废掉的支其实还在喂着主线」就永远看不见。
    rejoin = {m["from"]: m for m in (f.get("merges") or []) if m["to"] == sid}
    rejoin.update({m["to"]: m for m in (f.get("merges") or []) if m["from"] == sid})
    out = [f"{project}/{sid}  {me['title']}",
           "（数据依赖 input，不是树上的 parent —— 前者是「这些字节从哪来」，"
           "后者是「我当时接着哪一步想」）"]

    def block(head: str, ids: list[str], direct: list[str], empty: str) -> None:
        out.append("")
        out.append(f"{head}（{len(ids)} 步）:" if ids else f"{head}: {empty}")
        for i in ids:
            s = by_id[i]
            m = rejoin.get(i)
            out.append(f"  {'●' if i in direct else '·'} {i:<6} [{s['status']}] {s['title']}"
                       + (f"   ⇢ 汇回：另一条支线，两条线在 {m['at']} 分开" if m else ""))
        if ids:
            out.append("  （● 是直接相邻的一层，· 是再往外的传递依赖）")
        if any(i in rejoin for i in ids):
            out.append("  （标了「汇回」的那几条不在同一条路上：它们是从另一条支线"
                       "汇过来的，树上看不见这条边）")

    if want in ("up", "both"):
        block("上游 · 这一步消费了谁的产物",
              _flow_closure(steps, sid, "up"),
              [i["step"] for i in (me.get("inputs") or [])],
              "没写 input —— 如果这一步真的读了别人的产物，补上，"
              "否则「这个数字怎么来的」只能靠猜")
        for i in (me.get("inputs") or []):
            if i.get("note"):
                out.append(f"    ← {i['step']}  {i['note']}")
    if want in ("down", "both"):
        block("下游 · 谁消费了这一步的产物",
              _flow_closure(steps, sid, "down"),
              list(me.get("consumers") or []),
              "还没有别的步骤声明用了它（这是派生结果，扫全项目算出来的）")
    return "\n".join(out)


def count_entries(p: Path) -> int | None:
    """目录里有多少个直接条目。**只数一层**。

    递归数是这件事里唯一会慢到不可接受的部分：那个 57 GB 的目录底下几十万个 inode，
    在网络盘上遍历一遍能跑几分钟，而 stat 本身是毫秒级的。所以 n= 记的是
    「这一层有多少条」，要更细的数字请人自己去数了再写。
    """
    try:
        return sum(1 for _ in p.iterdir())
    except OSError:
        return None


def t_check_paths(be, args) -> str:
    project, sid = args["project"], args["step"]
    s = be.step(project, sid)
    rows = list(s.get("paths") or [])
    only = (args.get("path") or "").strip()
    if only:
        rows = [p for p in rows if p["location"] == only]
        if not rows:
            raise ToolError(f"{sid} 上没有记着 {only} 这条路径。核对结果只写在已经记下来的路径上。")
    if not rows:
        return f"{project}/{sid} 没有记任何外部路径，没什么可核对的。"

    date = args.get("date", "")
    done, gone, skipped = [], [], []
    for p in rows:
        loc = p["location"]
        state, size = probe_path(loc)
        if state == PROBE_UNREACHABLE:
            # **一个字都不写。** 这台机器够不着，不代表那份数据没了。
            skipped.append(loc)
            continue
        n = None
        if args.get("count") and state == PROBE_PRESENT and Path(loc).is_dir():
            n = count_entries(Path(loc))
        be.check_path(project, sid, {"loc": loc, "exists": state == PROBE_PRESENT,
                                     "date": date, "size": size, "n": n})
        (done if state == PROBE_PRESENT else gone).append(
            loc + (f"（{_fmt_bytes(size)}）" if size is not None else "")
            + (f"（{n} 条）" if n is not None else ""))

    out = [f"{project}/{sid} 的 {len(rows)} 条路径，在这台机器上核对完了："]
    if done:
        out.append(f"  ✓ 还在（{len(done)}）: " + "、".join(done))
    if gone:
        out.append(f"  ✕ 已确认不存在（{len(gone)}）: " + "、".join(gone))
        out.append("    记录**没有删**——一个曾经装着东西、现在空了的位置是一条溯源结论，"
                   "不是要顺手清掉的笔误。要说清它是怎么没的，请在正文里补一句。")
    if skipped:
        out.append(f"  ? 这台机器够不着，什么都没写（{len(skipped)}）: " + "、".join(skipped))
        out.append("    远端位置（s3:// / https://）一律不探测；本机看不见的挂载点也只报够不着。"
                   "**够不着 ≠ 不存在** —— 要核对它们，请到看得见那些路径的机器上再跑一次。")
    return "\n".join(out)


def _md_ref(path: str, caption: str, is_img: bool) -> str:
    """拼进正文的 markdown 引用。

    caption 和文件名都是外部输入，零转义直接拼会把语法撑破：
    引号会提前关掉 title，括号会提前关掉 url。
    """
    cap = re.sub(r'["\r\n]+', "'", caption).strip()
    target = f"<{path}>" if re.search(r"[ ()<>]", path) else path
    return f'![]({target} "{cap}")' if is_img else f"[{cap}]({target})"


def t_attach(be, args) -> str:
    project, sid = args["project"], args["step"]
    name = (args.get("name") or "").strip()

    if args.get("path"):
        if args.get("text") is not None:
            raise ToolError("path 和 text 只能给一个")   # 原来是静默丢掉 text
        p = Path(args["path"]).expanduser()
        try:
            if not p.is_file():
                raise ToolError(f"文件不存在: {p}")
            # 先看大小再读。原来是先 read_bytes 把整个文件读进内存，
            # 大小闸门在后面才生效——一个 40 GB 的 checkpoint 会先把内存打爆。
            if p.stat().st_size > MAX_ATTACH_BYTES:
                raise ToolError(
                    f"{p.name} 有 {p.stat().st_size / 1048576:.0f} MB，超过 "
                    f"{MAX_ATTACH_BYTES // 1048576} MB 上限。大文件不要传进来，"
                    f"用 paths 记下它在哪就行。"
                )
            data = p.read_bytes()
        except OSError as e:
            # 权限不足、被别的进程独占、路径太长……这些是工具层失败，
            # 要让模型看得到并改，不该变成协议级错误。
            raise ToolError(f"读不了 {p}：{e.strerror or e}") from None
        name = name or p.name
    elif args.get("text") is not None:
        data = args["text"].encode("utf-8")
        if not name:
            raise ToolError("给 text 时必须同时给 name")
    else:
        raise ToolError("要么给 path（本地文件），要么给 text（文本内容）")

    is_img = Path(name).suffix.lower() in IMG_EXT
    caption = (args.get("caption") or "").strip()
    if is_img and not caption:
        raise ToolError(
            "图片必须给 caption。读这条记录的人和 agent 都看不到图里的内容，"
            "图注是这张图唯一的信息来源——写「这张图说明了什么」，不是「这是一张什么图」。"
        )

    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    info = be.attach(project, sid, data, name, mime)
    path = info["path"]
    msg = f"已{'复用已有' if info.get('reused') else '上传'}附件 {project}/{sid}/{path}（{info['size']} 字节）"

    if caption:
        ref = _md_ref(path, caption, is_img)
        cur = be.step(project, sid)["body"] or ""
        if path not in cur:
            be.update(project, sid, {"body": cur.rstrip("\n") + "\n\n" + ref + "\n"})
            msg += "，并已在正文末尾插入引用"
    return msg


def _reject_front_matter(body: str) -> None:
    """body 里自己拼了 front-matter 就当场拒绝，而不是让它变成正文里的一坨文本。

    结构键从函数形状上就进不了译文的 front-matter（trace_write 的渲染器只接受
    一个键名），所以 agent 拼的那一段 `---` 会原样落进**正文**——不报错、不生效、
    看着却像已经写上了。这种「静默地什么都没发生」比报错难查得多，
    所以在这里就说清楚：title 走参数，body 只放正文。
    """
    lines = str(body or "").lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != "---":
        return
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key = line.split(":", 1)[0].strip().lower()
        if key in TR_STRUCT_KEYS or key in ("title", "name"):
            raise ToolError(
                f"body 里带了 front-matter（`{key}:`）。翻译文件的 front-matter 只允许一个 "
                "title:（项目笔记是 name:），而且由 title 参数写。"
                "结构字段一律只认原文，写进译文会被忽略并报警告——"
                "把开头那段 `---` 删掉，body 只放正文。")


def t_translate(be, args) -> str:
    project, lang = args["project"], args["lang"]
    sid = (args.get("step") or "").strip()
    _reject_front_matter(args.get("body") or "")
    common = {"body": args.get("body", ""), "expect": args.get("expect", "")}
    if sid:
        info = be.translate(project, sid, lang, {"title": args.get("title", ""), **common})
        what = f"{project}/{info['id']}"
    else:
        # 项目笔记那一侧的键叫 name（它是项目显示名），但工具只暴露一个 title 参数：
        # 多一个只在省略 step 时才有意义的参数，等于多一处能填错的地方。
        info = be.translate_project(project, lang, {"name": args.get("title", ""), **common})
        what = f"{project} 的项目笔记"
    return (f"已写入 {what} 的 {info['lang']} 译文（{info['path']}，digest {info['digest']}）。"
            f"原文一个字节都没动。")


def t_untranslated(be, args) -> str:
    lang = (args.get("lang") or "en").strip()
    r = be.untranslated(args["project"], lang)
    lang = r["lang"]
    head = (f"{r['project']} · {lang} 翻译：{r['total']} 步里已有 {r['translated']} 份译文"
            + (f"，另有 {r['native']} 步原文就是 {lang}" if r["native"] else "")
            + f"，还缺 {len(r['missing'])} 份。")
    lines = [head]
    if r["missing"]:
        lines += ["", f"还没有 {lang} 版的步骤："]
        lines += [f"  {m['id']:<6} [{m['status']}]  {m['title']}" for m in r["missing"]]
    note = r["project_note"]
    lines += ["", "项目笔记 project.{}.md：{}".format(
        lang, "还没有" if note["missing"] else ("原文就是这个语言" if note["native"] else "已有"))]
    if r["missing"] or note["missing"]:
        lines.append("补一份用 trace_translate（省略 step 就是翻译项目笔记）。")
    return "\n".join(lines)


def t_pipeline(be, args) -> str:
    chapter = str(args.get("chapter") or "")
    payload = be.pipeline(args["project"], chapter)
    # 要了某一章，回执里却没有「我编的是这一章」那个键 —— 这台服务端还不认
    # `?chapter=`，它忽略了参数、把**整个项目**那一份原样回了过来（本地后端不会
    # 走到这里：名字不认识它当场就抛了）。这不能当成成功：agent 接着会把这份
    # 草稿当成消融那一段 Methods 写进论文，而它里面有主实验的每一步。
    # 症状本来是无声的，所以这里必须出声。
    if chapter and not payload.get("chapter"):
        raise ToolError(
            f"这台服务端还不支持按章节导（要的是「{chapter_label(chapter)}」，"
            "回来的是整个项目那一份）。升级服务端，或者去掉 chapter 参数拿整份流程"
            "——把整份流程当成某一章的 Methods 交出去，读的人不会发现。")
    if args.get("methods"):
        return pipeline_methods(payload)
    out = fmt_pipeline(payload)
    if payload["declared"]:
        # 每次都说一遍这两句，因为最容易犯的错**不会报错**：拿开发路径当唯一真相去
        # 复现（照着一棵含 dead 的树跑），以及把这份草稿当成品发出去。
        out += ("\n\n要写论文就加 methods=true（Methods 草稿）。"
                "「当时有几个候选、为什么选了这一条」在**开发路径**上，用 trace_read 看"
                "——那两条路径都留着，正是为了这个问题。")
        if not chapter and len(payload["pipeline"].get("chapters") or []) > 1:
            # 分了章而且不止一组时才说：一章的项目说这句是噪音。
            names = [chapter_label(g["name"]) for g in payload["pipeline"]["chapters"]]
            out += ("\n这个项目分了章节，上面是**合起来的一张图**（共用的准备步骤只出现一次）。"
                    f"论文里要分段写就一章一章导：{'、'.join(names)}"
                    f"——chapter=\"<章节名>\"（未分章那组是 \"{CHAPTER_NONE}\"）。")
    return out


def t_result(be, args) -> str:
    project, sid = args["project"], args["step"]
    if args.get("drop"):
        info = be.drop_result(project, sid)
        left = info.get("results") or []
        return (f"已撤销 {project}/{sid} 的成果声明。**记录一个字都没动**——"
                f"它只是不再是定稿流程的终点，开发路径上还在原处。\n"
                + (f"现在声明的成果：{'、'.join(r['step'] for r in left)}"
                   if left else "现在一个成果都没有了，定稿流程也就推不出来了。"))
    info = be.set_result(project, sid, args.get("note", ""))
    verb = "已声明" if info.get("created") else "已改写"
    lines = [f"{verb} {project}/{sid} 为成果：{info.get('line', '')}",
             "定稿流程会从它沿 `input:` 反向算出来（一步没写 input: 时退回 parent，"
             "剔掉 dead）。**成员清单一个字都不存**，所以你不用维护它。"]
    left = info.get("results") or []
    if len(left) > 1:
        lines.append(f"这个项目现在有 {len(left)} 个成果："
                     + "、".join(r["step"] for r in left)
                     + "。它们合成**一张**图，共用的准备步骤只出现一次。")
    # 声明完当场把算出来的流程摆出来。不摆的话，调用方拿到的是一句干净的「已声明」，
    # 而这个动作真正改变的东西（哪几步进了 Methods、整条链的等级、有没有踩着 dead）
    # 一样都看不见 —— 而它们恰恰是这一次调用的全部后果。
    try:
        lines += ["", fmt_pipeline(be.pipeline(project))]
    except Exception:                       # 回执是加分项，拿不到就闭嘴，别把主操作说成失败
        lines.append("（流程现算不出来，用 trace_pipeline 单独看一眼。）")
    return "\n".join(lines)


HANDLERS = {
    "trace_projects": t_projects,
    "trace_new_project": t_new_project,
    "trace_insight": t_insight,
    "trace_delete_step": t_delete_step,
    "trace_read": t_read,
    "trace_search": t_search,
    "trace_new_step": t_new_step,
    "trace_update_step": t_update_step,
    "trace_move_step": t_move_step,
    "trace_flow": t_flow,
    "trace_pipeline": t_pipeline,
    "trace_result": t_result,
    "trace_check_paths": t_check_paths,
    "trace_attach": t_attach,
    "trace_translate": t_translate,
    "trace_untranslated": t_untranslated,
}


def dispatch(backend, name: str, args: dict[str, Any]) -> str:
    fn = HANDLERS.get(name)
    if fn is None:
        raise ToolError(f"未知工具: {name}")
    return fn(backend, args or {})


# ---------------------------------------------------------------- MCP


# MCP 是一份开放协议规范，`mcp` 那个 pip 包只是它的官方 Python SDK 之一。
# stdio 这一侧要实现的东西很小——换行分隔的 JSON-RPC 2.0，加上
# initialize / tools/list / tools/call / ping 四个方法——所以这里直接说协议，
# 不依赖 SDK。好处是实打实的：
#   * 零依赖，任何裸 Python 3.10+ 都能跑（HiperGator 上不用往 conda 环境里装东西）
#   * 不会被 SDK 的破坏性改版牵连（mcp 2.0 就删掉了 1.x 的整套装饰器 API）
# 代价是协议细节得自己守住，所以 tests/test_mcp.py 里除了自测，还会用官方 SDK
# 的客户端连上来跑一遍互操作。

SERVER_NAME = "trace"
SERVER_VERSION = "1.8.0"

# 收到客户端要的版本就原样回它（前提是我们认识），否则回我们最新的。
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

# 格式标准的可执行摘要。
#
# 为什么内联而不是只指路：README 主推的「只要 MCP」装法是
# `pip install git+…`，而 pyproject.toml 只打包三个 .py —— 那台机器上根本
# 不存在 FORMAT.md，也不存在 skills/。原来的 instructions 却写着「完整的写作
# 格式标准在插件根目录的 FORMAT.md」，agent 去找、找不到，然后手上只剩九个
# 工具描述：指标表怎么写、图注要承载结论、dead 要写放弃理由、L0–L4 怎么判、
# repro 三种状态是什么，一样都拿不到。initialize 的 instructions 是**唯一**
# 无论怎么装都一定送达的通道，所以要点必须放在这里。
#
# 没有做成第十个工具（比如 trace_format），是因为工具数量本身是插件清单里
# 对外宣称的规格（.claude-plugin/plugin.json 与 marketplace.json 都写着
# 「9 个 MCP 工具」，tests/test_plugin.py 机械地钉着这个数），加一个就要连带
# 改两份不属于本次改动范围的清单文件；而且多一次工具往返才拿到格式标准，
# 意味着「没想起来调」的 agent 照样写歪 —— 送到眼前比放在货架上有效。
FORMAT_ESSENTIALS = (
    "【写作格式标准 v1 · 要点】总原则：可视化必须从已经可读的文本里长出来。"
    "一张 PNG 人看得见、你看不见，一个 markdown 表格人和你读到的是同一批数字——"
    "所以优先级是 表格 > front-matter 字段 > 围栏代码块 > 带图注的图 > 裸图（不允许）。"
    "① 五个小节固定为 为什么 / 做了什么 / 结果 / 结论 / 下一步。"
    "「为什么」写**假设**（承接上一步的什么发现、想验证什么），不是写动作；"
    "「做了什么」里的命令要完整到别人能照着跑；done / dead **必须**写「结论」。"
    "② 指标一律用 markdown 表格，不要用图：主指标放第一个数值列，基线在最上面，"
    "最好的一行用 **加粗**；不用手写 `---:`，整列是数字就会自动右对齐并加底纹条"
    "（底纹纯属加成，你读到的还是原数字）；带单位的值（`40 s`）会让整列变文字列，"
    "这是对的，不要为了对齐去掉单位；有方差就写成 `0.943 ± 0.004`。"
    "③ 图独占一段并带图注，写成 `![](loss.png \"第 12 轮后验证集回升，所以 epoch 定在 3\")`——"
    "图注写这张图**说明了什么**，不是「这是一张 loss 曲线」；没图注的图 check 会报"
    "figure_without_caption，评级卡在 L0。"
    "④ 正文里 `[[003b]]` 是交叉引用，会渲染成跳转链接并在对方页面显示反向链接。"
    "⑤ 外部产物用 paths 记成 `位置 | 角色 | 说明 | k=v …`：第一段永远是位置；"
    "之后每一段按内容认领——整段**恰好**是 input/script/output/evidence 之一就是角色，"
    "整段的空白分隔 token **全部**形如 k=v 就是机器属性，其余都拼进说明"
    "（所以老写法 `位置 | 说明` 一个字都不用改，而「lr=3e-4 的那次运行」也照样落进说明）。"
    "已知属性：size=字节数（写整数）、n=条目数、md5=/sha256=、checked=YYYY-MM-DD（最后一次确认还在）、"
    "missing=YYYY-MM-DD（最后一次确认已经没了；两个都在时看日期，同一天算 missing）。"
    "不认识的属性照样保留。**路径没了不要删那一行**——「这份数据当年在这儿、现在没了」是溯源结论。"
    "GB 级的东西不要传进来，只记它在哪 —— 这是溯源的一半。"
    "⑤b 记录的派生关系（`parent`）和数据依赖（`input`）是两件事：前者是「我当时接着哪一步想」，"
    "树上只能有一个；后者是「这些字节从哪来」，可以有好几个（`input: 013 | pocket_composition.csv`）。"
    "一步的输入同时来自 013 和 014 时，树上只表达得了一个，另一个必须写进 input。"
    "`parent` **可以改**，但要走 trace_move_step 并写清原因，那次移动会追加一行 "
    "`moved: 日期 | 原 parent | 新 parent | 谁 | 原因`；`id` 仍然永不可改。"
    "代码位置用 `code: <kind> | <位置> | <k=v …>`，kind ∈ git / snapshot / container；"
    "`commit:` 等价于一条 code: git，两者只写一份。"
    # ⑤c 三种关系。没有这一条，agent 只会写普通的 parent，于是「A/B 只能选一个」
    # 和「顺着往下做」在树上长得一模一样 —— 而这正是这一轮要分开的东西。
    "⑤c **树上的父子边有两种意思，另外还有一种边根本不在树上**，三者别混："
    "（1）**普通延伸**：接着上一步继续做。默认，`branch:` 那一行不用写。"
    "（2）**互斥候选**：`branch: alternative`（可以带说明 `alternative | 只调采样权重`）。"
    "意思是「我和我的兄弟们是同一个问题的几个答案，**只能选一条走下去**」——"
    "注意「从 A 又分出一条支线去试别的」**不是**这个，那就是普通延伸。"
    "一组候选是**扫出来的**（同一个父节点底下所有 alternative 的孩子），"
    "父节点上绝不写孩子清单、兄弟之间也不互相登记（那是双真相源，一次 move 就过期）。"
    "分叉点（父节点）上写一句 `decision: 类别不平衡怎么处理？只能选一条走下去`，"
    "说清**在决定什么**——候选有谁、选了谁都算得出来，唯独这句话只能人写。"
    "「**选了哪个**」不需要任何新字段：**其余候选标 `status: dead` 并写清为什么放弃**，"
    "「已定」就是从这里推出来的；一组里还有两个以上没标 dead ＝ 这个岔路口还没决定"
    "（那是待办，不是错——同时开几条线是常态）。"
    "（3）**汇回**：某条支线的产物后来又参与了另一条线上的某一步。**它就是 `input:`**，"
    "没有新键，也**不要**拿 branch 去表达（branch 只说得了「我和我 parent 那条边」）。"
    "四条 warn 级提醒，一条都不降级：`lone_alternative`（一组只有一个候选）、"
    "`fork_without_decision`（有候选却没写 decision）、`undecided_fork`（还有两个以上候选活着）、"
    "`decision_without_candidates`（写了 decision 却一个候选都没标 —— 那一行现在什么都不做，"
    "不成组、不进 forks 清单，看着像没保存上）。"
    # ⑤d 章节。放在三种关系（⑤c）紧后面，因为 agent 在这里犯的错就是**把三样搞混**，
    # 而三样里有两样已经在上一条讲完了。格式本身一行就说完，篇幅全花在分界上。
    "⑤d **一个项目内部还能分章节**：主实验 / 消融实验 / 数据准备，各有独立的探索路径。"
    "写法是在**开启那条线的第一步**上写一行 `chapter: 消融实验 | 逐个拿掉模块，对着主实验的 023 比`"
    "（竖线右边是**这个章节**的说明，可选），底下整棵子树**沿 parent 自动继承**——"
    "**不要给每一步各写一遍**：写满了的代价是改一次章节名要改二十个文件、"
    "移走一支还带着一行过期的声明，而继承来的归属一个字都不用维护。"
    "想让某一步脱离，让它自己声明一个新的章节名；一路到根都没写就是「未分章」（不是错，多数项目如此）。"
    "**三样别混，选错了不报错、只会让记录说出你没打算说的话**："
    "**项目**＝不同的研究（id 各自从 001 开始）；**章节**＝同一个研究里**并列**的几块，"
    "**互不排斥、都要留着**（论文里主实验一段 Methods、消融一段）；"
    "**分叉**（`branch: alternative`）＝同一个问题的**互斥候选**，**只能选一个**，其余标 dead。"
    "「A 方案 / B 方案最后二选一」是分叉不是两个章节。"
    "章节之间互相引用是正常的：消融当然吃主实验的产物，照常写 `input:`，那条**跨章节的边**"
    "会被标出来——它说的正是「消融是对着主结果测的」。"
    "两件**刻意不做**的事，别当成漏了去补：**id 不按章节重编号**（消融不从 001 重新开始，"
    "`[[007]]` 和论文脚注要在整个项目里唯一）；**章节不嵌套**（名字里可以写 `主实验/数据准备`，"
    "那只是显示时按 `/` 分组，语义上仍是一层）。"
    "每个章节各有自己的**定稿流程**（`result:` 指的那一步在哪一章，流程就属于哪一章）和"
    "自己的**可溯源等级**（本章成员里最弱的一步）。"
    "⑥ 可溯源性等级：L0 不可溯源（缺「为什么」/「做了什么」，或有图没图注，或 done/dead 没结论）；"
    "L1 可读；L2 可定位（L1 + 代码找得回来 + 记了产物位置）——代码找得回来指**任何一条** code 记录"
    "（commit / 有位置的 snapshot / container），快照目录加逐文件校验和一样算数，"
    "不必为了上 L2 把不是 commit 的东西塞进 commit 字段；L3 可重跑（repro: runnable）；"
    "L4 已复现（repro: verified）。**等级受祖先制约**——祖先没记数据在哪，后代再全，"
    "整条链也追不到底，所以补记录要从**最弱的那一环**补起，不是从最新那一步补起。"
    "⑦ repro 三种状态：runnable＝查过、命令环境种子齐全；verified＝真跑过、数字在容差内对上；"
    "failed＝试过、跑不起来或对不上（和成功一样重要，「checkpoint 被清了」本身就是溯源结论）。"
    "只追加不覆盖。"
    # 下面两条只在这份摘要里，不在 FORMAT.md 的摘要范围之外——
    # pip 装的机器上根本没有 FORMAT.md（pyproject 只打包三个 .py），
    # 而 agent 手写 project.md 或者判断某段 markdown 会不会渲染，靠的就是这两条。
    "⑧ 每个项目还有一个 project.md，装项目级沉淀（「回译在这个数据集上一直没用」"
    "是三次尝试之后的判断，挂在哪一步都不对）。小节名**逐字固定**为 "
    "`## 核心想法` / `## 有效` / `## 无效` / `## 坑`，外加一个由系统自己写的 "
    "`## 已删除`（删掉一步之后，「为什么删的」只剩那一行——**绝不要手工改它**）。"
    "写它请用 trace_insight，别整段覆盖。"
    "⑨ 渲染器是手写的，认 CommonMark 的一个子集：标题 / 段落 / 强调 / 行内代码 / "
    "围栏代码块 / 列表（含嵌套、有序、任务）/ 引用块 / 表格 / 链接 / 图片 / "
    "删除线 / 水平线 / `[[id]]` 交叉引用。**有意不做**：引用式链接 `[a][ref]`、"
    "脚注、setext 标题、四空格缩进代码块（用围栏）。数学公式不做渲染，原样保留——"
    "`$\\alpha$` 人和你都读得到原文，但不会变成排版好的公式，所以别指望它显示效果。"
    "文件名带空格时图片要写成 `![](<loss curve.png> \"图注\")`。"
    # ⑩ 双语。这一条也只在这份摘要里 —— 译文的写法（哪个文件、哪些键、小节名叫什么）
    # 一样是「照文档写」才写得对的东西，而 pip 装的机器上没有文档。
    "⑩ 双语是**另一个文件**，不是原文里的字段：一步的英文版是同目录下的 "
    "`note.en.md`，项目笔记的是 `project.en.md`（`note.<短语言码>.md`，ja / zh-Hant 同理）。"
    "写它只有 trace_translate 一条路，它碰不到原文；还欠哪些用 trace_untranslated 查。"
    "译文的 front-matter **只准有 `title:`**（项目笔记是 `name:`），其余结构一律不重复——"
    "id / parent / status / date / commit / author / tags / path / repro / key / input / code / "
    "moved / branch / decision / result / pipeline / chapter "
    "写进去会被忽略并报一条警告，因为那些在原文里已经有了，写两份就是双真相源。"
    "（`chapter` 尤其要留神：它**沿树继承**，译文里多写一行改的不是这一步，"
    "是它底下整棵子树在那种语言的页面上的归属——同一个项目按两种分法各导出一份 Methods。）"
    "译文正文的小节名用目标语言那一套，和原文一一对应：为什么=Why、做了什么=What、"
    "结果=Result、结论=Conclusion、下一步=Next；项目洞察 核心想法=Ideas、有效=Works、"
    "无效=Doesn't work、坑=Pitfalls。"
    "「还没翻译」是「文件不存在」这个派生事实，不存储、不报警告、也不影响评级——"
    "一个小节只要 note.md **或任一译文**里写了就算写了，只有中文的记录照样可溯源。"
)

INSTRUCTIONS = (
    "这是一棵只追加的科研步骤树。规矩："
    "① 动手之前先 trace_read 看这条线走到哪了；"
    "② 开跑之前就建 wip 步骤，跑完再改成 done/dead——等跑完才记的话，跑挂的那一步就不存在了；"
    "③ 正文必须写「为什么」，这是唯一无法自动生成的字段；"
    "④ 失败也要记，标 dead 并写清放弃理由，死胡同是这里最有价值的部分；"
    "⑤ 图必须给 caption，你看不到图，图注是它唯一的信息来源；"
    "⑥ 产物落在哪（超算路径、GitHub、对象存储）用 paths 记下来，位置会失效，"
    "定期用 trace_check_paths 在**看得见那些路径的机器上**核对一遍；"
    "⑦ id 写下就不可改；parent 可以改，但只能走 trace_move_step 并写清原因（会留下审计）；"
    "别把 parent 当数据依赖用 —— 「这些字节从哪来」是 inputs，能有好几个，用 trace_flow 看；"
    "⑧ 两条路只能选一条时（同一个问题的两个答案），给每个候选写 `branch: alternative`，"
    "并在它们的**父节点**上写一句 `decision:` 说清在决定什么；走通一条之后把其余的标 dead —— "
    "「选了哪个」就是这么派生出来的，没有第二个字段。**「又分出一条支线去试别的」不是互斥候选**，"
    "那是普通延伸；**「那条支线的产物后来回到了主路径上」也不是**，那是 inputs。"
    "隔几天回到一个项目先 `trace_read(forks=true)` 看还有几个岔路口没定；"
    "⑨ 要双语就用 trace_translate 单独补一份译文（`note.en.md`），它碰不到原文——"
    "建完步骤马上调就是立刻翻译，过几天再调就是延迟翻译，同一条路径；"
    "隔了几天先用 trace_untranslated 看还欠哪些；"
    "⑩ **一个项目有两条路径，别混**：trace_read 那棵树是**开发路径**（全部记录，"
    "含 dead、含还没决定的岔路口，回答「当时是怎么走到那儿的」）；trace_pipeline 是"
    "**定稿流程**（只有产出成果的那条链，回答「该怎么做」）。"
    "**要复现结果或写 Methods 时读 trace_pipeline，不要照着那棵树走**——"
    "树上有 dead 的步骤，照着它复现等于去重跑一条作者自己判定走不通的路。"
    "流程是算出来的，唯一要写下来的是「哪一步是成果」（trace_result）；"
    "⑪ **同一个项目里并列的几块用章节**（主实验 / 消融 / 数据准备）：只在**开启那条线的第一步**上"
    "写一次 `chapter:`（trace_new_step / trace_update_step 的 chapter 参数），整棵子树自动继承，"
    "**别给每一步各标一遍**。**项目 / 章节 / 分叉是三样东西**：项目＝不同的研究；"
    "章节＝同一个研究里并列的几块，**互不排斥、都要留着**（论文里本来就是两段 Methods）；"
    "分叉＝同一个问题的互斥候选，**只能选一个**。"
    "有哪几章用 trace_read(chapters=true)，按章节导 Methods 用 trace_pipeline(chapter=…)。\n\n"
    + FORMAT_ESSENTIALS
)


def _format_doc() -> Path | None:
    """这台机器上有没有完整的 FORMAT.md（clone / 插件安装才有，pip 装没有）。"""
    p = Path(__file__).resolve().parent / "FORMAT.md"
    return p if p.is_file() else None


if _format_doc() is not None:
    INSTRUCTIONS += (
        f"\n\n完整版（含示例与出处）在 {_format_doc()} —— 上面这份是它的摘要，"
        "两者冲突时以文件为准。")

_JSON_TYPES: dict[str, Any] = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "array": list, "object": dict,
}


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> None:
    """按 inputSchema 校验。官方 SDK 会做这件事，自己说协议就得自己做。

    只覆盖 required / type / enum / array-items —— 这几样就是工具参数会出错的全部形式。
    """
    props = schema.get("properties") or {}
    for k in schema.get("required") or []:
        if k not in args or args[k] is None or args[k] == "":
            raise ToolError(f"缺少必填参数 {k}")
    for k, v in args.items():
        spec = props.get(k)
        if spec is None:
            raise ToolError(f"不认识的参数 {k}；这个工具接受：{', '.join(props) or '（无）'}")
        if v is None:
            continue
        want = spec.get("type")
        # Python 里 bool 是 int 的子类，裸 isinstance 会让 true 混进 integer/number。
        if want in ("integer", "number") and isinstance(v, bool):
            raise ToolError(f"参数 {k} 应当是 {want}，收到 boolean")
        py = _JSON_TYPES.get(want)
        if py and not isinstance(v, py):
            raise ToolError(f"参数 {k} 应当是 {want}，收到 {type(v).__name__}")
        if want == "array":
            item = (spec.get("items") or {}).get("type")
            ipy = _JSON_TYPES.get(item)
            if ipy and any(not isinstance(x, ipy) for x in v):
                raise ToolError(f"参数 {k} 的每一项都应当是 {item}")
        if spec.get("enum") and v not in spec["enum"]:
            raise ToolError(f"参数 {k} 只能是 {' / '.join(map(str, spec['enum']))}")


def _result(mid: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": payload}


def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


class Session:
    """一次连接的全部状态。后端延迟创建，配置错了要在 tools/call 时报出来而不是启动就死。"""

    def __init__(self) -> None:
        self.protocol = PROTOCOL_VERSIONS[0]
        self.backend: Any = None

    def get_backend(self) -> Any:
        if self.backend is None:
            self.backend = make_backend()
        return self.backend


def handle(msg: Any, session: Session) -> dict[str, Any] | None:
    """处理一条 JSON-RPC 消息。返回要回的对象；通知（没有 id）返回 None。

    纯函数式的形状，所以协议逻辑可以脱离子进程直接单测。
    """
    if not isinstance(msg, dict):
        return _error(None, -32600, "请求必须是 JSON 对象")
    has_id = "id" in msg
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

    # MCP 在 JSON-RPC 之上收紧了一条：请求的 id 不允许是 null。
    if has_id and mid is None:
        return _error(None, -32600, "请求的 id 不能是 null")
    is_notification = not has_id

    if not isinstance(method, str):
        return None if is_notification else _error(mid, -32600, "缺少 method")

    # JSON-RPC 2.0 §4.1：通知一律不回，**也不执行**。
    # 这道闸必须在所有分支之前——只在"未知方法"处判的话，一条没有 id 的
    # tools/call 会既回一个 id:null 的幽灵响应（官方客户端解析不了），
    # 又真的把步骤写进磁盘。
    if is_notification:
        return None

    try:
        if method == "initialize":
            want = params.get("protocolVersion")
            session.protocol = want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return _result(mid, {
                "protocolVersion": session.protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            })

        if method == "ping":
            return _result(mid, {})

        if method == "tools/list":
            return _result(mid, {"tools": [
                {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
                for t in TOOLS
            ]})

        if method == "tools/call":
            name = params.get("name")
            raw = params.get("arguments", {})
            if raw is None:
                raw = {}
            if not isinstance(raw, dict):
                # 静默当成 {} 的话，报出来的会是"缺少必填参数 project"，
                # 和真正的毛病（arguments 根本不是对象）对不上，很难查。
                return _error(mid, -32602, "arguments 必须是 JSON 对象")
            spec = next((t for t in TOOLS if t["name"] == name), None)
            if spec is None:
                return _error(mid, -32602, f"未知工具: {name}")
            try:
                validate_args(spec["inputSchema"], raw)
                text = dispatch(session.get_backend(), name, raw)
            except ToolError as e:
                # 工具层的失败用 isError 回，让模型看得到、能改；
                # JSON-RPC 的 error 只留给协议层的问题。
                return _result(mid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
            except Exception as e:
                # 工具里冒出来的任何异常也走 isError（官方 SDK 就是这么做的）。
                # 回 JSON-RPC error 会让客户端当成协议级故障直接抛给上层，
                # 模型既看不到原因也没法自我纠正。真实触发路径：远端超时、
                # 代理返回 HTML 页导致 JSONDecodeError、读文件时的 PermissionError……
                traceback.print_exc(file=sys.stderr)
                return _result(mid, {"content": [{"type": "text",
                                                  "text": f"工具执行失败：{type(e).__name__}: {e}"}],
                                     "isError": True})
            return _result(mid, {"content": [{"type": "text", "text": text}], "isError": False})

        return _error(mid, -32601, f"不支持的方法: {method}")

    except Exception as e:                                 # 兜底，绝不让连接因为一条消息断掉
        traceback.print_exc(file=sys.stderr)
        return _error(mid, -32603, f"内部错误: {type(e).__name__}: {e}")


def serve_stdio(stream_in=None, stream_out=None) -> None:
    sin = stream_in if stream_in is not None else sys.stdin
    sout = stream_out if stream_out is not None else sys.stdout
    session = Session()

    def emit(obj: Any) -> None:
        # ensure_ascii=True：输出全是 ASCII，Windows 上的终端编码就影响不到协议通道。
        sout.write(json.dumps(obj) + "\n")
        sout.flush()

    for line in sin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, RecursionError, ValueError):
            # RecursionError 不是 JSONDecodeError 的子类：嵌套上万层的数组
            # 会让它逃出去、一路把整个循环掀翻，连接直接断。
            emit(_error(None, -32700, "JSON 解析失败"))
            continue
        if isinstance(msg, list):                          # 老版本协议允许批量，逐条处理
            out = [r for r in (handle(m, session) for m in msg) if r is not None]
            if out:
                emit(out)
            continue
        resp = handle(msg, session)
        if resp is not None:
            emit(resp)


# ---------------------------------------------------------------- 自检

# 插件把用户在 /plugin 里填的四项灌进这四个环境变量。名字写在这里是为了
# 让自检能在「一个都没设」的时候，指名道姓地说清缺的是哪几个。
PLUGIN_ENV = ("TRACE_ROLE", "TRACE_DATA", "TRACE_URL", "TRACE_TOKEN")
PLUGIN_NAME = "research-trace"


def plugin_user_config() -> tuple[Path | None, dict[str, str]]:
    """尽力找出 Claude Code 里 research-trace 插件**实际填了什么**。

    为什么需要它：插件的 TRACE_* 只注入给 Claude Code 拉起的 MCP 子进程，
    **不进入 shell 环境**。用户在一台已经配好的机器上按 README 跑
    `trace_mcp.py --selfcheck`，看到的是「没有配置后端」——在配好的机器上报错，
    正砸在「换机器要能自证接通」这条需求上。

    这是**尽力而为**：插件配置存在哪、用什么形状存，是宿主的实现细节，不是契约。
    所以找不到不算失败，只是走到 explain_plugin_env() 那条「你现在这个上下文
    看不到插件的配置，要这样查」的路上去。任何解析失败一律当作没找到。
    """
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    cands = [base / "plugins" / "config.json", base / "settings.json",
             base / "settings.local.json", Path.home() / ".claude.json"]
    keys = {"role", "data_dir", "url", "token", "python"}

    def dig(node, depth=0):
        if depth > 8 or not isinstance(node, dict):
            return None
        for k, v in node.items():
            if PLUGIN_NAME in str(k) and isinstance(v, dict):
                flat = {kk: vv for kk, vv in v.items() if kk in keys and isinstance(vv, (str, int))}
                if flat:
                    return {kk: str(vv) for kk, vv in flat.items()}
            hit = dig(v, depth + 1)
            if hit:
                return hit
        return None

    for p in cands:
        try:
            if not p.is_file():
                continue
            found = dig(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError, RecursionError):
            continue
        if found:
            return p, found
    return None, {}


def explain_plugin_env() -> None:
    """当前 shell 里一个 TRACE_* 都没有时，说清这是为什么、以及该怎么查真值。"""
    print("\n  ⓘ 这个 shell 里一个 TRACE_* 都没设。如果你是**通过插件**装的，这属于正常现象：")
    print(f"      /plugin 里填的四项是灌给 Claude Code 拉起的 MCP 子进程的（{', '.join(PLUGIN_ENV)}），")
    print("      不会出现在你手工敲命令的这个环境里。也就是说：**本次自检看不到插件的配置**，")
    print("      它报的「没配后端」不代表插件没配好。")
    p, vals = plugin_user_config()
    if vals:
        shown = {k: ("****" if k == "token" and v else v) for k, v in vals.items()}
        print(f"      我在 {p} 里找到了一份 {PLUGIN_NAME} 的配置：{shown}")
        print("      照它跑一遍就能验到真实配置：")
        print(f"        python {Path(__file__).resolve()} --selfcheck"
              + "".join(f" --{k.replace('data_dir', 'data')} \"{v}\""
                        for k, v in vals.items() if k in ("role", "data_dir", "url") and v))
    else:
        print("      要验插件实际生效的配置，两条路，任选其一：")
        print("        (a) 在 Claude Code 会话里直接调 mcp__plugin_research-trace_trace__trace_projects")
        print("            —— 那个调用跑在**带着插件 env 的子进程**里，是唯一的地面真值；")
        print("        (b) 打开 /plugin → research-trace → 配置，把里面的值抄下来重跑本自检：")
        print(f"            python {Path(__file__).resolve()} --selfcheck "
              "--role server --data \"<你填的数据仓目录>\"")
        print(f"            python {Path(__file__).resolve()} --selfcheck "
              "--role client --url \"<远端地址>\" --token \"<写入令牌>\"")


SELFCHECK_FLAGS = {"--role": "TRACE_ROLE", "--data": "TRACE_DATA",
                   "--url": "TRACE_URL", "--token": "TRACE_TOKEN",
                   "--config": "TRACE_CONFIG"}


def apply_selfcheck_flags(argv: list[str]) -> list[str]:
    """把 `--role/--data/--url/--token/--config` 写进本进程的环境变量。

    存在的理由就是上面那条：插件的配置在 shell 里看不见，用户只能从 /plugin
    界面把值抄出来。抄出来之后总得有个地方能喂进去——不然「自证接通」这件事
    在插件安装路径下根本没有出口。写进 os.environ 而不是另开一条配置通路，
    是为了让被验的东西和真实运行时**逐字**走同一条 make_backend 代码路径。
    """
    rest, i = [], 0
    while i < len(argv):
        a = argv[i]
        key, val = (a.split("=", 1) + [None])[:2] if "=" in a else (a, None)
        if key in SELFCHECK_FLAGS:
            if val is None:
                i += 1
                val = argv[i] if i < len(argv) else ""
            os.environ[SELFCHECK_FLAGS[key]] = val
        else:
            rest.append(a)
        i += 1
    return rest


# 写权限探针用的项目名。它不会存在，也不该存在：探针只想知道
# 「令牌过不过得了门」，不想在任何人的记录里留下一个字节。
WRITE_PROBE = "__trace_selfcheck_probe__"


def probe_write(be) -> tuple[bool, str]:
    """确认这台机器**写得进去**，且不留任何垃圾。返回 (行不行, 说明)。

    为什么必须验写：原来的自检三项（列项目、JSON-RPC 握手、Python 版本）全是读路径，
    而服务端的读是不要令牌的。于是「客户端没填令牌」这种最常见的漏配也能拿到满屏 ✓ +
    「全部通过」，直到 agent 真正开始记录、第一次写入撞上 401 —— 恰好是最不该卡住的时刻。

    不留垃圾的做法：
      · 远端：PATCH 一个**必然不存在**的项目。require_token 排在业务逻辑前面，
        所以令牌不对 → 401，令牌对 → 404「项目不存在」。两种回答都不写一个字节。
      · 本地：在数据仓根下建一个点开头的空探针文件再删掉。点开头的条目
        scan/list_files/signature 全都跳过，即使进程被 kill 留下它也不进任何视图。
        不用 os.access：Windows 上它只看只读属性，不看 ACL，会给出假阳性。
    """
    if isinstance(be, HttpBackend):
        try:
            status = be._call("GET", "/api/status")
        except ToolError as e:
            return False, f"连不上服务端，写入无从谈起：{e}"
        protected = bool(status.get("write_protected"))
        if not protected:
            return True, "服务端没设写入令牌（谁都能写）——公网部署请务必设上"
        try:
            be._call("PATCH", f"/api/p/{WRITE_PROBE}/steps/{WRITE_PROBE}", {})
        except ToolError as e:
            msg = str(e)
            if msg.startswith("401"):
                return False, ("令牌不对或没填。后果很具体：读（浏览、trace_read）一切正常，"
                               "第一次写入才 401。去 /plugin → research-trace → 写入令牌，"
                               "填服务端 `python trace_cli.py url` 打印的那一串。")
            if msg.startswith("404"):
                return True, "令牌通过了服务端的写入闸门（探针项目不存在，回 404，没写任何东西）"
            return False, f"写入探针得到意料之外的回答：{msg}"
        # 走到这里说明探针项目真的存在且被改成功了——这不该发生，但也没造成损坏
        return True, f"写入闸门通过（注意：竟然真有一个叫 {WRITE_PROBE} 的项目）"

    probe = be.root / f".trace-write-probe-{os.getpid()}"
    try:
        probe.write_bytes(b"")
    except OSError as e:
        return False, f"数据仓目录写不进去（{e.strerror or e}）——检查属主和权限"
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return True, "数据仓目录可写（探针文件已删除，没留痕迹）"


def selfcheck() -> int:
    """`trace-mcp --selfcheck`：一条命令确认这台机器上能不能用。

    不需要 Claude、不需要网络（本地模式下）、不需要任何额外依赖。
    新机器装完跑一次，通不通、哪一项要改，它自己会说。
    """
    import io as _io

    ok = True

    def say(good, label, detail=""):
        nonlocal ok
        ok = ok and good
        print(f"  {'✓' if good else '✗'} {label}" + (f"  {detail}" if detail else ""))

    print("trace-mcp 自检\n")
    v = sys.version_info
    say(v >= (3, 10), f"Python {v.major}.{v.minor}.{v.micro}",
        "" if v >= (3, 10) else "→ 需要 3.10 以上，换一个解释器")
    print(f"    解释器: {sys.executable}")

    cfg, src = read_config()
    role = (os.environ.get("TRACE_ROLE") or cfg.get("role") or "auto").strip().lower() or "auto"
    where = []
    for k in ("TRACE_ROLE", "TRACE_URL", "TRACE_TOKEN", "TRACE_DATA", "TRACE_CONFIG"):
        if os.environ.get(k, "").strip():
            where.append(k)
    print(f"    配置来源: {'环境变量 ' + '/'.join(where) if where else '（无环境变量）'}"
          + (f" + 文件 {src}" if src else ""))
    say(role in ROLES, f"角色: {role}", "" if role in ROLES else f"→ 只能是 {'/'.join(ROLES)}")
    # 角色不是摆设：client 会把本地目录**清空**，server 会把远端地址清空，
    # 两样都填了也不是「远端优先」。选错的症状是「读到的是另一头的数据」，
    # 而不是报错，所以这里必须把生效语义和改法一起说出来。
    print("    " + {
        "auto": "两样都填时远端优先；只填一样就用那一样",
        "server": "只认数据仓目录，远端地址即使填了也会被忽略",
        "client": "只认远端地址 + 写入令牌，本地目录即使填了也会被忽略",
    }.get(role, "未知角色"))
    print("    改角色: /plugin → research-trace → 配置 → 角色（server / client / auto）；"
          "或设 TRACE_ROLE，或改配置文件里的 \"role\"")
    # 判据是「插件灌的那四个」一个都没有，而不是「一个环境变量都没有」：
    # TRACE_CONFIG 指的是配置文件，和插件那条注入通路无关，设了它照样看不见插件的配置。
    if not any(os.environ.get(k, "").strip() for k in PLUGIN_ENV):
        explain_plugin_env()

    try:
        be = make_backend()
    except ToolError as e:
        say(False, "后端", "")
        print(f"\n    {e}\n")
        return 1
    say(True, "后端: " + ("远端 " + be.base if isinstance(be, HttpBackend) else f"本地 {be.root}"))
    if isinstance(be, LocalBackend) and be.root.name == "projects":
        print("    ⚠ 数据仓目录本身就叫 projects —— 多半是指深了一层。"
              f"应当填它的父目录 {be.root.parent}，否则会造出 projects/projects。")
    # 「这是全新的空仓」必须说出口。不说的话，路径打岔一个字符 → 凭空造出一棵空树 →
    # 自检照报「全部通过」→ 用户在假树上记几十步才发现老项目一个都不见了。
    if isinstance(be, LocalBackend) and be.root_state != DATA_ROOT_READY:
        print(f"    ⚠ {be.root_note}")
        print("      如果这台机器上本来就有记录，说明数据仓目录填错了 —— "
              "现在改还来得及，等记了几十步再改就要手工搬目录了。")

    try:
        ps = be.projects()
        say(True, f"读取正常，{len(ps)} 个项目",
            " · ".join(f"{p['slug']}({p['steps']}步)" for p in ps[:4]) or "（还没有项目，正常）")
        # 既没有步骤、也没写过洞察和名字的目录，多半是 data_dir 指错了一层
        # （指到 projects/ 本身而不是它的父目录），会造出一个 projects/projects。
        ghost = [p for p in ps if not p["steps"] and not (p.get("body") or "").strip()
                 and p["name"] == p["slug"]]
        if ghost:
            print(f"    ⚠ 有 {len(ghost)} 个空壳项目：{', '.join(p['slug'] for p in ghost)}")
            print("      如果里面有个叫 projects 的，说明数据仓目录指到 projects/ 本身了——"
                  "应当指它的**父目录**。")
    except ToolError as e:
        say(False, "读取失败", str(e))
        return 1

    good, detail = probe_write(be)
    say(good, "写入" + ("正常" if good else "会失败"), detail)

    # 走一遍真实的 JSON-RPC，确认协议层没问题
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": PROTOCOL_VERSIONS[0]}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    sout = _io.StringIO()
    serve_stdio(_io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n"), sout)
    lines = [json.loads(l) for l in sout.getvalue().splitlines() if l.strip()]
    tools = lines[1]["result"]["tools"] if len(lines) > 1 and "result" in lines[1] else []
    say(len(lines) == 2 and len(tools) == len(TOOLS),
        f"MCP 协议握手正常，{len(tools)} 个工具",
        ", ".join(t["name"] for t in tools[:3]) + " …")

    print("\n" + ("全部通过。把这个解释器和本文件的绝对路径填进 MCP 配置即可。" if ok
                 else "有问题，见上面的 ✗。"))
    return 0 if ok else 1


def main() -> int:
    if "--selfcheck" in sys.argv[1:]:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
        # 只在自检这条路上解析这几个开关：它们改的是本进程的 TRACE_*，
        # 让「从 /plugin 抄出来的值」能走和真实运行时完全相同的 make_backend。
        apply_selfcheck_flags([a for a in sys.argv[1:] if a != "--selfcheck"])
        return selfcheck()
    if "--version" in sys.argv[1:]:
        print(SERVER_VERSION)
        return 0
    # stdout 是协议通道：关掉 Windows 的 \n → \r\n 转换，诊断一律走 stderr。
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    except (AttributeError, OSError):
        pass
    try:
        serve_stdio()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"trace-mcp: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
