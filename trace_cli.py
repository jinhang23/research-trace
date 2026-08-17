"""trace_cli — 命令行入口。

    python trace_cli.py init                             初始化
    python trace_cli.py projects                         列出项目
    python trace_cli.py new-project --name "SMARTAffinity"
    python trace_cli.py new -P <项目> --title "..."       新建一步
    python trace_cli.py mv <id> --parent 013b --reason "…" 改挂到别的父节点下（原因必填）
    python trace_cli.py fork 012 012b --decision "…"     把几步标成互斥候选（只能选一条）
    python trace_cli.py forks                            还有几个岔路口没做决定
    python trace_cli.py paths --check                    逐条核对外部路径还在不在
    python trace_cli.py result 023 --note "主结果…"       声明「这一步是成果」
    python trace_cli.py chapter                          列出章节（主实验 / 消融 / …）
    python trace_cli.py pipeline [--methods|--svg 图.svg] 定稿流程 / Methods 草稿 / 那张图
    python trace_cli.py pipeline --chapter 消融实验       只出那一章（论文里本来就是两段）
    python trace_cli.py check [-P <项目>]                 校验不变量
    python trace_cli.py tr [-P <项目>] [--lang en]        还缺哪些语言版本 / 补一份译文
    python trace_cli.py build [--out dist]               静态导出，file:// 可直接打开
    python trace_cli.py serve [--port 8100]              起服务
    python trace_cli.py url                              打印访问地址与写入令牌
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import trace_core as core
import trace_mcp as mcp
import trace_write as W
from trace_server import CONFIG_PATH, ROOT, WEB, load_config, make_config

# 静态导出要拷进去的前端文件。**漏一个就是白屏**：index.html 里那几个 <script>
# 是写死的，i18n.js 缺席时 window.i18n 是 undefined，app.js 第一次调 t() 就抛，
# 整页什么都不画（而 file:// 下没有服务端日志可看）。
STATIC_ASSETS = ("style.css", "app.js", "md.js", "i18n.js")

# 默认把数据仓放在代码仓**外面**。项目自己的不变量是「代码仓公开、数据仓私有」，
# 而 data_dir="." 正好违反它：README 的「30 秒上手」照默认跑一遍，未发表的实验记录
# 就躺在一个公开仓库的工作区里，只等某一次 `git add -A`。放在同级目录既不需要用户
# 想清楚路径，又天然满足那条不变量。
DEFAULT_DATA_DIR = "../trace-data"

LEVELS = ("L0", "L1", "L2", "L3", "L4")
LEVEL_LABEL = {"L0": "不可溯源", "L1": "可读", "L2": "可定位", "L3": "可重跑", "L4": "已复现"}

# 纯写法提示：内核发的是 warn 级，但它们**一条都不影响 L0–L4**，也不该让 --strict 失败。
# 单独列出来而不是靠 level 字段区分，是因为 level 说的是「有多确定」，
# 这里要分的是「有什么后果」——两者不是一回事，混用会让真正的警告被一起忽略。
#
# 后三条是分叉的写法诊断：「一组只有一个候选」「有候选却没写在决定什么」，以及它的
# 镜像「写了在决定什么却一个候选都没标」。它们说的是这条记录还差一句人写的话，
# 和评级无关——一个岔路口写不写得清楚，不改变「这个结果追不追得到」。
HINT_CODES = ("section_without_prose", "table_without_explanation", "code_without_explanation",
              "lone_alternative", "fork_without_decision", "decision_without_candidates")

# 「还没做决定的岔路口」既不是缺陷也不是写法问题，是**待办**。它从警告和提示两栏里
# 都摘出去，由下面那一段专门说——同一件事说两遍会让人以为自己犯了错，而人消除
# 「错误」最省事的办法是随手把一条支标成 dead，那是拿假结论换一屏干净的输出。
TODO_CODES = ("undecided_fork",)


def data_root(cfg: dict) -> Path:
    """解析 data_dir 并确保布局在。**路径不对劲会当场说出来。**

    以前这里是无条件 ensure_layout：填错一个字符 → 凭空建出一棵合法的空树 →
    什么都不报。区分不出「首次安装」和「路径打岔」的根子在于两者的观测一模一样，
    所以这里的做法不是禁止创建（换机首装时数据仓本来就不存在），而是把状态说出来。
    """
    raw = cfg.get("data_dir", DEFAULT_DATA_DIR)
    try:
        root, state, note = mcp.check_data_root(ROOT / raw)
    except mcp.ToolError as exc:
        raise W.WriteError(str(exc)) from None
    core.ensure_layout(root)
    if state != mcp.DATA_ROOT_READY:
        print(f"⚠ {note}")
    return root


def pick_project(root: Path, slug: str | None) -> str:
    ps = core.scan_projects(root)
    if slug:
        if slug not in {p.slug for p in ps}:
            raise W.NotFound(f"项目 {slug} 不存在。已有: {', '.join(p.slug for p in ps) or '(无)'}")
        return slug
    if len(ps) == 1:
        return ps[0].slug
    if not ps:
        raise W.NotFound("还没有任何项目，先跑 new-project")
    raise W.WriteError("有多个项目，请用 -P 指定: " + ", ".join(p.slug for p in ps))


def inline(payload) -> str:
    """内联进 <script type="application/json">。JSON 里 `<` 只可能出现在字符串中。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


# ---------------------------------------------------------------- init


def git_worktree_of(p: Path) -> Path | None:
    """p 落在哪个 git 工作区里（自下而上找 .git）。找不到返回 None。

    不调 `git rev-parse` 是因为这条判断必须在**没装 git 的机器上**也给出答案，
    而且它决定的是「要不要允许自动同步」这种安全默认值 —— 拿不到答案时不能默默放行。
    """
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d
    return None


def shares_repo_with_code(root: Path) -> bool:
    """数据仓和这份代码在同一个 git 仓库里吗。

    这是「自动同步能不能开」的唯一闸门。项目自己的不变量是「代码仓公开、数据仓私有」，
    而 GitSync 跑的是 `git add -A && git commit && git push` —— 两者同仓时，
    第一次建步骤 45 秒后，未发表的实验记录就被推进了公开仓库，而且推送成功时
    **一个字都不会打印**（失败才写进 last）。所以这不是提醒，是禁止。
    """
    if root == ROOT or ROOT in root.parents or root in ROOT.parents:
        return True
    a, b = git_worktree_of(root), git_worktree_of(ROOT)
    return a is not None and a == b


def _ask_yes(question: str) -> bool:
    """只在真的连着终端时才问。非交互环境（CI、测试、脚本）一律走安全默认值。"""
    try:
        if not (sys.stdin and sys.stdin.isatty()):
            return False
        return input(question).strip().lower() in ("y", "yes", "是")
    except (EOFError, OSError):
        return False


def cmd_init(args) -> int:
    if CONFIG_PATH.is_file() and not args.force:
        print(f"config.json 已存在（--force 覆盖）: {CONFIG_PATH}")
        return 1

    want_git = bool(getattr(args, "git", False)) and not args.no_git
    if getattr(args, "git", False) and args.no_git:
        print("--git 和 --no-git 只能给一个。", file=sys.stderr)
        return 2

    # 先把路径体检做完再落盘：配置写下去之后才发现路径不对，用户得先删 config.json
    # 才能重来，而 --force 会连令牌一起换掉。
    try:
        root, state, note = mcp.check_data_root(ROOT / args.data_dir)
    except mcp.ToolError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    same_repo = shares_repo_with_code(root)
    if want_git and same_repo:
        print(f"错误: 拒绝在 {root} 上开自动 git 同步 —— 它和这份代码在同一个 git 仓库里。",
              file=sys.stderr)
        print("      自动同步跑的是 `git add -A && git commit && git push`，"
              "代码仓一旦是公开的，", file=sys.stderr)
        print("      你未发表的实验记录就会被推到公网上，而且推送成功时不打印任何东西。",
              file=sys.stderr)
        print("      正确做法：另建一个**私有**仓库，用 --data-dir 指过去，再 --git。",
              file=sys.stderr)
        return 2

    # 没明确表态时问一句。git 同步的后果（把私有笔记推到某个 remote）不该由默认值决定。
    if not want_git and not args.no_git and not same_repo and (root / ".git").exists():
        want_git = _ask_yes(f"数据仓 {root} 是个 git 仓库。要开自动 git 同步（commit + push）吗？[y/N] ")

    cfg = make_config(args.title)
    cfg["data_dir"] = args.data_dir
    # 默认关。「只在确认数据仓不是这个代码仓时才可能开启」这条闸门在上面，
    # 这里只是把结论写下去。
    cfg["git"]["enabled"] = want_git
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    core.ensure_layout(root)
    if not core.scan_projects(root):
        W.create_project(root, args.project)

    separate = not same_repo
    print(f"已写入 {CONFIG_PATH}")
    print(f"  数据仓    {root}" + ("" if separate else "   ← 和代码在同一个 git 仓库"))
    if state != mcp.DATA_ROOT_READY:
        print(f"            ⓘ {note}")
    print(f"  访问路径  /t/{cfg['space']}/")
    print(f"  写入令牌  {cfg['token']}")
    print("\nconfig.json 含密钥，已在 .gitignore 中。请另行备份。")

    if not cfg["git"]["enabled"]:
        print("\n自动 git 同步：**关**（默认）。")
        if same_repo:
            print("  这个数据仓和代码在同一个 git 仓库里，所以它也开不了 —— "
                  "开了等于把科研笔记推进代码仓。")
            print(f"  要开：另建一个私有仓库，重新跑 init --data-dir <私有仓库> --git"
                  f"（现在的默认值是 {DEFAULT_DATA_DIR}）。")
        else:
            print("  要开：把数据仓做成 git 仓库、配好**私有** remote，然后重跑 "
                  "init --force --git，或把 config.json 里的 git.enabled 改成 true。")
        return 0

    # 自动同步 commit 的是**数据仓**，所以要检查的也是数据仓。
    if not (root / ".git").exists():
        print(f"\n⚠ 自动 git 同步开着，但 {root} 不是 git 仓库，同步会一直是 no-op。")
        print("  先在数据仓里 git init 并 git remote add origin <私有仓库>。")
    else:
        code = subprocess.run(["git", "remote", "get-url", cfg["git"].get("remote", "origin")],
                              cwd=str(root), capture_output=True, text=True,
                              encoding="utf-8", errors="replace").returncode
        print("\n自动 git 同步已开启，目标是数据仓。"
              + ("" if code == 0 else f"\n⚠ 但数据仓还没有名为 {cfg['git'].get('remote', 'origin')} 的 remote，push 会失败（commit 仍然正常）。"))
        print("  再确认一次：那个 remote 必须是**私有**仓库。")
    return 0


def cmd_url(args) -> int:
    cfg = load_config()
    print(f"路径: /t/{cfg['space']}/" if cfg.get("space") else "路径: /")
    print(f"令牌: {cfg.get('token') or '(未设置 — 写入不设防)'}")
    return 0


# ---------------------------------------------------------------- 项目


def cmd_projects(args) -> int:
    cfg = load_config()
    root = data_root(cfg)
    ps = core.scan_projects(root)
    if not ps:
        print("还没有任何项目。跑 new-project 建一个。")
        return 0
    for p in ps:
        f = core.compile_forest(core.steps_dir_of(root, p.slug), with_files=False)
        c = {"wip": 0, "done": 0, "dead": 0}
        for s in f["steps"]:
            c[s["status"]] = c.get(s["status"], 0) + 1
        print(f"  {p.slug:<24} {p.name:<28} {len(f['steps']):>3} 步  "
              f"(done {c['done']} / wip {c['wip']} / dead {c['dead']})")
    return 0


def cmd_new_project(args) -> int:
    cfg = load_config()
    p = W.create_project(data_root(cfg), args.name)
    print(f"已创建项目 {p.slug}（显示名 {p.name}）")
    print(core.project_dir(data_root(cfg), p.slug))
    return 0


# ---------------------------------------------------------------- 步骤


def cmd_new(args) -> int:
    cfg = load_config()
    root = data_root(cfg)
    slug = pick_project(root, args.project)
    sd = core.steps_dir_of(root, slug)
    step, created = W.create_step(
        sd,
        parent=args.parent,
        title=args.title,
        status=args.status,
        date=args.date or "",
        commit=args.commit or "",
        author=args.author or "human",
        tags=[t.strip() for t in (args.tags or "").split(",") if t.strip()],
        paths=args.path or None,
        inputs=args.input or None,
        code=args.code or None,
        branch=args.branch or "",
        decision=args.decision or "",
        # 章节写在**开启那条线的第一步**上，所以它必须在 new 这里就能给：
        # 建完再改一次的话，这一步已经在上一章里躺过一轮。
        chapter=args.chapter or "",
    )
    print(("已创建 " if created else "已存在 ") + f"{slug}/{step.id}")
    print(sd / step.dirname / core.NOTE_NAME)
    if args.branch or args.decision:
        for line in _fork_lines(core.compile_forest(sd), step.id):
            print(line)
    if args.chapter:
        print(f"章节「{W.norm_chapter(args.chapter)['name']}」从这一步开始"
              "——底下整棵子树自动继承，**别给每一步各写一遍**。"
              f"看全部章节：chapter -P {slug}")
    return 0


# ---------------------------------------------------------------- 岔路口


def _fork_lines(forest: dict, sid: str) -> list[str]:
    """刚动过分叉语义之后，把**这一组现在长什么样**当场说出来。

    候选组是派生的：落盘的只有这一步自己那一行 `branch:`，「这一组有谁、定了没有」
    要扫完兄弟才知道。不当场说，人就得再跑一次 check 才看得见自己刚做了什么——
    而最该被看见的恰恰是**不报错**的那两种：只标了一个候选（一个候选不成其为选择），
    以及标完 dead 之后这个岔路口其实已经定了。
    """
    out = []
    for g in forest.get("branch_groups") or []:
        if sid not in (g.get("options") or []) and g.get("at") != sid:
            continue
        at = g.get("at") or "（森林的根之间）"
        out.append(f"⑂ {at} 这一组候选: {' / '.join(g['options'])} —— {mcp.fork_label(g)}")
        if g.get("at") and not g.get("decision"):
            out.append(f"  {g['at']} 上还没写 decision（在决定什么）——"
                       "候选有谁、选中了谁都算得出来，唯独这句话只能人写。")
    return out


def cmd_fork(args) -> int:
    """把几个兄弟成组标成互斥候选，顺手把「在决定什么」写在它们的父节点上。

    这个子命令的意义不是「能标」——`new --branch` 和一次 PATCH 都做得到。它的意义
    是**同父校验**：候选组是「同一个父节点底下所有 alternative 的孩子」现算出来的，
    一次只标一个的话，把两个不同父节点下的步骤各标一次，得到的是两个各含一个候选
    的组，一条错都不报，而人以为自己刚记下了一个岔路口。
    """
    cfg = load_config()
    root = data_root(cfg)
    slug = pick_project(root, args.project)
    sd = core.steps_dir_of(root, slug)
    notes = {}
    for raw in (args.note or []):
        sid, _, text = raw.partition("=")
        if not text.strip():
            raise W.WriteError(f"--note 要写成 步骤id=这个候选自己的角度，收到 {raw!r}")
        notes[sid.strip()] = text.strip()
    info = W.mark_alternatives(sd, args.ids, decision=args.decision or "", notes=notes or None)
    print(f"已把 {'、'.join(info['marked'])} 标成互斥候选"
          + (f"（挂在 {info['parent']} 底下）" if info["parent"] else "（都是根）"))
    if info.get("decision"):
        print(f"  在决定什么: {info['decision']}")
    for line in _fork_lines(core.compile_forest(sd), info["marked"][0]):
        print("  " + line)
    print("  「选了哪个」不用另写：走通哪条，把其余的标 dead 并写清为什么放弃"
          "（rm 是给误建用的，失败的实验是结论）。")
    return 0


def cmd_forks(args) -> int:
    """列出岔路口。默认只列**还没做决定**的那些。

    **它不是 check 的一部分。**「还有三个岔路口悬着」是待办，不是缺陷：同时开几条
    线是研究的常态，把它塞进警告栏只会稀释警告的分量，人很快就会为了让输出干净
    随手把一条标成 dead —— 那是拿假结论换绿色。所以它自己一条命令，也不进退出码。
    """
    cfg = load_config()
    root = data_root(cfg)
    slugs = [pick_project(root, args.project)] if args.project \
        else [p.slug for p in core.scan_projects(root)]
    for slug in slugs:
        f = core.compile_forest(core.steps_dir_of(root, slug))
        print(mcp.fmt_forks(f, f"[{slug}]", only_open=not args.all))
        print()
    return 0


def cmd_rm(args) -> int:
    """真删一步。

    在服务器那台机器上，`rm -rf steps/002_xxx` 本来就能达到同样的效果——
    这个子命令的意义不是"能删"，是**逼你把原因留下来**。目录一删，
    "为什么删的"就只剩 project.md 里的那一行了。
    """
    cfg = load_config()
    root = data_root(cfg)
    slug = pick_project(root, args.project)
    sd = core.steps_dir_of(root, slug)
    info = W.delete_step(sd, args.id, args.reason, by=args.by or "human", date=args.date or "")
    print(f"已删除 {slug}/{info['id']}「{info['title']}」（连同 {info['files_removed']} 个文件）")
    print(f"原因已记进 {core.project_dir(root, slug) / core.PROJECT_NOTE}")
    if info["orphaned"]:
        print("⚠ 变成孤儿（会被降级为根）：" + "、".join(info["orphaned"]))
    if info["dangling_refs"]:
        print("⚠ 这些步骤的正文里还写着 [[" + info["id"] + "]]：" + "、".join(info["dangling_refs"]))
    if info.get("dangling_inputs"):
        # 单独一行，措辞比上面那条重：正文引用是给人读的，`input:` 是数据依赖的声明，
        # 可溯源性沿着它上溯；再加上 id 会被重用，这些边会无声地改指到别的步骤上。
        print("⚠ 这些步骤声明了 input: " + info["id"] + "（数据依赖，可溯源链会断在这里）："
              + "、".join(info["dangling_inputs"]))
    if info.get("dangling_results"):
        # 三条里最重的一条。删掉的这一步被 project.md 声明成了**成果**，整条定稿
        # 流程就是从它长出来的——它一没，流程静默变空，而页面上只有一行小字。
        # 写入侧刻意**不**替人撤那一行（撤了就没人看得见流程曾经指向一步被删的
        # 记录），所以这里必须说出来；而 id 会被重用，下一个拿到该号的步骤会
        # 无声地变成论文的主结果。
        print("⚠ project.md 里这几行成果声明现在指空了（定稿流程从它们长出来）："
              + "、".join(info["dangling_results"]))
        print("   去 project.md 里改掉或删掉它们，或者用 result 子命令重新指一步。")
    print(f"⚠ id {info['id']} 可能被下一个新建的步骤重用。")
    return 0


KIND_LABEL = {
    "hpc": "超算", "github": "GitHub", "git": "Git", "dropbox": "Dropbox", "drive": "Drive",
    "object": "对象存储", "archive": "数据仓库", "mlhub": "实验平台", "url": "链接",
    "local": "本机", "path": "路径",
}


ROLE_LABEL = {"input": "输入", "script": "脚本", "output": "产物", "evidence": "证据"}


def _path_status(p: dict) -> str:
    """一条路径最后一次核对的结论，写成人话。

    「已确认不存在」按 P4 写成**结论**而不是错误：一个曾经装着 57 GB、现在空了的
    位置是一条发现，不是要顺手清掉的笔误。所以这里不用「错误」「失败」这类词，
    这一行也永远不会被 --check 删掉。
    """
    if p.get("state") == "missing":
        return f"✕ {p.get('missing')} 起已确认不存在"
    if p.get("state") == "present":
        return f"✓ {p.get('checked')} 确认还在"
    return "· 从未核对过"


def _check_one_project(root: Path, slug: str, rows, count: bool) -> tuple[int, int, int]:
    """对一批 (step, path) 逐条 stat 并写回。返回（还在, 已不存在, 够不着）。"""
    sd = core.steps_dir_of(root, slug)
    ok = gone = far = 0
    for s, p in rows:
        loc = p["location"]
        state, size = mcp.probe_path(loc)
        if state == mcp.PROBE_UNREACHABLE:
            # **一个字都不写。** 这台机器够不着，不代表那份数据没了。
            far += 1
            print(f"  ? {s['id']:<5} {loc}   够不着（远端位置，或这台机器上没挂那个盘）")
            continue
        n = None
        if count and state == mcp.PROBE_PRESENT and Path(loc).is_dir():
            n = mcp.count_entries(Path(loc))
        W.record_path_check(sd, s["id"], loc, exists=state == mcp.PROBE_PRESENT, size=size, n=n)
        if state == mcp.PROBE_PRESENT:
            ok += 1
        else:
            gone += 1
            print(f"  ✕ {s['id']:<5} {loc}   已确认不存在（记录保留，只是记上了日期）")
    return ok, gone, far


def cmd_paths(args) -> int:
    """把一个项目里所有外部产物的位置列出来 —— 溯源时最常问的"东西在哪"。

    `--check` 会逐条 stat 一遍并把结论写回记录。它**只在能看到那些路径的机器上
    才有意义**：`/blue/…` 多半挂在超算上，笔记本上跑一遍只会得到一屏「够不着」。
    而「够不着」和「不存在」绝不混为一谈——前者什么都不写。
    """
    cfg = load_config()
    root = data_root(cfg)
    slugs = [pick_project(root, args.project)] if args.project else [p.slug for p in core.scan_projects(root)]

    if args.check:
        for slug in slugs:
            f = core.compile_forest(core.steps_dir_of(root, slug), with_files=False)
            rows = [(s, p) for s in f["steps"] for p in s["paths"]
                    if not args.kind or p["kind"] == args.kind]
            if not rows:
                continue
            print(f"[{slug}] 核对 {len(rows)} 条…")
            ok, gone, far = _check_one_project(root, slug, rows, args.count)
            print(f"  → 还在 {ok} · 已不存在 {gone} · 够不着 {far}\n")

    total = missing = 0
    for slug in slugs:
        f = core.compile_forest(core.steps_dir_of(root, slug), with_files=False)
        rows = [(s, p) for s in f["steps"] for p in s["paths"]
                if (not args.kind or p["kind"] == args.kind)
                and (not args.missing or p.get("state") == "missing")]
        if not rows:
            continue
        print(f"[{slug}]")
        for s, p in rows:
            total += 1
            if p.get("state") == "missing":
                missing += 1
            # checksum 是 core 拼好的 "md5:7d4e1a9c" 一串（算法在冒号左边）。
            # 它以前一处出口都没有：`paths` 不打、`check` 不打，于是「拿到的还是不是
            # 当时那份」这个问题只能靠人去 grep note.md。核对的时候它比大小有用得多。
            bits = [b for b in (
                core.fmt_size(p["size"]) if p.get("size") is not None else "",
                f"{p['n']} 条" if p.get("n") is not None else "",
                str(p["checksum"]).replace(":", "=", 1) if p.get("checksum") else "",
                _path_status(p),
            ) if b]
            print(f"  {s['id']:<5} {KIND_LABEL.get(p['kind'], p['kind']):<6} "
                  f"{ROLE_LABEL.get(p.get('role', ''), '—'):<4} {p['location']}")
            print(f"{'':>20}{' · '.join(bits)}" + (f"   {p['note']}" if p["note"] else ""))
        print()
    if not total:
        if args.missing:
            print("没有任何一条路径被确认为已不存在。")
        else:
            print("还没有记录任何外部路径。用 trace_cli.py new --path \"…\" 或在网页里加。")
    elif missing and not args.missing:
        print(f"⚠ {missing} 处位置已确认不存在（记录保留——那是溯源结论，不是笔误）。"
              f"只看它们：paths --missing")
    return 0


def cmd_mv(args) -> int:
    """把一步连同它的整棵子树改挂到别的父节点下。

    这个子命令的意义不是「能移」——在数据仓那台机器上，改一行 `parent:` 本来就能
    达到同样的效果。它的意义是**逼你把原因留下来**：移动之后这棵树就和创建顺序对不上了，
    「为什么 016 挂在 013b 下面，而它的号比 013b 大」只有那句话答得了。
    """
    cfg = load_config()
    root = data_root(cfg)
    slug = pick_project(root, args.project)
    sd = core.steps_dir_of(root, slug)
    info = W.move_step(sd, args.id, args.parent, args.reason,
                       by=args.by or "human", date=args.date or "")
    print(f"已把 {slug}/{info['id']} 从 {info['old_parent'] or '（根）'} 移到 "
          f"{info['new_parent'] or '（根）'}")
    print("  " + info["moved"])
    if info["subtree"]:
        print(f"⚠ 跟着一起走的还有 {len(info['subtree'])} 个后代：" + "、".join(info["subtree"]))
    # 移动会改变**两个**岔路口的成员：走掉的那个和加入的那个。候选组是现算的，
    # 事后只能靠再跑一遍 forks 才看得见，而「011 那组现在只剩一个候选了」正是
    # 这次移动的直接后果 —— 不说的话，人下次看到的是一条来路不明的 lone_alternative。
    for label, g in (("原来那个岔路口", (info.get("alternatives") or {}).get("left")),
                     ("移过去之后那个岔路口", (info.get("alternatives") or {}).get("joined"))):
        if g:
            print(f"  {label} {g['at'] or '（根之间）'}: "
                  f"{' / '.join(g['options'])} —— {mcp.fork_label(g)}")
    # 换章是移动最常见的用意之一（「把这一支挪进消融」），而它**磁盘上一个字节都没变**：
    # 二十步集体转章，diff 里只有一行 `moved:`。和上面那两个岔路口是同一类事、
    # 只是更隐蔽——不说的话，事后只能靠重新拉一遍森林才看得见。
    # 两头都没有章节时 move_step 给的是 None，那时一个字都不打（现存项目全是这个状态）。
    ch = info.get("chapter")
    if ch and ch["changed"]:
        # 「还有」说的是**这一步之外**的那些：`steps` 里第一个多半就是它自己
        # （它也是靠继承换的章），照数会让「移一步」听起来像「带走了一支」。
        others = [x for x in ch["steps"] if x != info["id"]]
        print(f"  章节 {mcp.chapter_label(ch['from'])} → {mcp.chapter_label(ch['to'])}"
              + (f"；跟着换章的还有 {len(others)} 步：" + "、".join(others)
                 + "（它们自己没写 chapter:，归属是继承来的）" if others else ""))
    elif ch:
        print(f"  章节没变：还在「{mcp.chapter_label(ch['to'])}」里。")
    print("id 没变，inputs 也没动 —— 数据依赖是另一件事。")
    return 0


# ---------------------------------------------------------------- 翻译


def _read_translation_file(path: Path, only_keys: tuple[str, ...], source: str) -> tuple[str, str]:
    """读一份写好的译文，拆成（front-matter 里那一个键, 正文）。

    整份文件读进来（而不是只收正文），是因为人和 agent 手上真正存在的东西就是
    一份 `note.en.md`——逼他们先手工把 front-matter 剥掉，只会剥错。
    结构键在这里就报出来：走 core.parse_translation 是为了让「哪些键会被丢掉」
    的判断只有一份实现，和读侧（网页顶栏、check）说的是同一句话。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise W.WriteError(
            f"{path} 不是 UTF-8（第 {exc.start} 个字节解不开）。"
            "Windows 上 cmd.exe 的 `>` 是 GBK、PowerShell 5.1 的 `>` 是 UTF-16LE——"
            "先转成 UTF-8 再来，别让工具替你猜（猜错就是一串不可逆的 �）。") from None
    except OSError as exc:
        raise W.WriteError(f"读不了 {path}：{exc.strerror or exc}") from None

    data, warns = core.parse_translation(text, only_keys=only_keys,
                                         where=path.name, source=source)
    for w in warns:
        print(f"⚠ {w['message']}")
    return data.get(only_keys[0], ""), data["body"]


def cmd_tr(args) -> int:
    """列出缺翻译的步骤，或者从一份写好的文件补一版进去。

    **check 里没有「缺翻译」这一条**，这里也不把它算成缺陷：L0–L4 问的是
    「这个结果追不追得到」，不是「翻译全不全」，只写了中文的记录一样是可溯源的。
    这个子命令回答的是另一个问题——「我隔了几天回来，还欠哪些」。
    """
    cfg = load_config()
    root = data_root(cfg)
    slug = pick_project(root, args.project)
    lang = W.norm_lang(args.lang)
    sd = core.steps_dir_of(root, slug)

    if args.drop:
        if not args.step:
            raise W.WriteError("--drop 要配 --step：项目笔记的译文目前只能手工删文件")
        info = W.drop_translation(sd, args.step, lang)
        print(f"已删除 {slug}/{info['id']} 的 {lang} 译文（{info['path']}）。原文没动。")
        return 0

    if args.file:
        path = Path(args.file).expanduser()
        if args.step and args.project_note:
            raise W.WriteError("--step 和 --project-note 只能给一个")
        if args.project_note:
            name, body = _read_translation_file(path, core.PROJECT_TR_ONLY_KEYS, core.PROJECT_NOTE)
            info = W.write_project_translation(root, slug, lang,
                                               name=args.title or name, body=body)
            print(f"已写入 {slug}/{info['path']}（项目笔记的 {lang} 版）。原文没动。")
            return 0
        if not args.step:
            raise W.WriteError("--file 要配 --step <id>（或 --project-note 翻译项目笔记）")
        title, body = _read_translation_file(path, core.TR_ONLY_KEYS, core.NOTE_NAME)
        info = W.write_translation(sd, args.step, lang, title=args.title or title, body=body)
        print(f"已写入 {slug}/{info['id']}/{info['path']}。原文没动。")
        return 0

    if args.step or args.project_note or args.title:
        raise W.WriteError("要补一份翻译请同时给 --file <写好的译文>；"
                           "不给 --file 就是列出还缺哪些")

    f = core.compile_forest(sd, with_files=False)
    p = next((x.to_dict() for x in core.scan_projects(root) if x.slug == slug), None)
    r = mcp.untranslated_report(f, p, lang)
    print(f"[{slug}] {lang} 翻译：{r['total']} 步里已有 {r['translated']} 份译文"
          + (f"，另有 {r['native']} 步原文就是 {lang}" if r["native"] else "")
          + f"，还缺 {len(r['missing'])} 份")
    for m in r["missing"]:
        print(f"  {m['id']:<6} [{m['status']:<4}] {m['title']}")
    note = r["project_note"]
    print(f"  项目笔记 project.{lang}.md: "
          + ("还没有" if note["missing"] else ("原文就是这个语言" if note["native"] else "已有")))
    if r["missing"] or note["missing"]:
        print(f"\n补一份：trace_cli.py tr -P {slug} --lang {lang} --step <id> --file <译文.md>")
    return 0


# ---------------------------------------------------------------- 定稿流程
#
# 两条路径，两个出口：`check` / `forks` / 三个视图看的是**开发路径**（全部记录），
# 这一段看的是**定稿流程**（只有产出成果的那条链）。推导和渲染一个字都不在这里——
# 判据在 core.compute_pipeline，渲染在 trace_mcp（那三样导出**只有那一份实现**，
# 网页拿的也是它的产物，理由见那一段的注释）。CLI 只负责读盘、挑格式、写文件。


def _pipeline_of(root: Path, slug: str, chapter: str = "") -> dict:
    """一个项目的定稿流程 payload。和 REST / MCP 走的是同一个 pipeline_payload。

    `compute_pipeline({}, [])` 那个 fallback 不是形式：一个 `result:` 都没声明的
    项目，forest 里**整个 pipeline 键都不出现**（现存项目必须完全无感），
    空态那句「教你怎么办」只有主动问起来的这条路上拿得到。

    `chapter` 给了就只出那一章。**这里不筛任何东西**——按章节切开是
    `mcp.pipeline_payload` 那一份实现的事，CLI 自己筛一遍就等于第二份实现，
    而其中一份产物（`--methods` / `--svg`）会进论文。
    """
    f = core.compile_forest(core.steps_dir_of(root, slug))
    # 抬头用显示名而不是目录名：导出的图和草稿是要交出去的，抬头写着 slug
    # （`my-project-2`）的话，收到的人只会以为拿错了文件。
    name = next((p.name for p in core.scan_projects(root) if p.slug == slug), slug)
    try:
        return mcp.pipeline_payload(f, slug, core.compute_pipeline({}, []), name, chapter)
    except mcp.ToolError as exc:
        raise W.NotFound(str(exc)) from None


def _chapters_of(root: Path, slug: str) -> dict:
    """一个项目的章节清单。和 REST 的 /chapters、MCP 的 trace_read(chapters=true)
    走的是同一个 chapters_payload——三个门面各 join 一遍的话，「消融有没有成果」
    这一列迟早在某个门面上分家。"""
    f = core.compile_forest(core.steps_dir_of(root, slug))
    name = next((p.name for p in core.scan_projects(root) if p.slug == slug), slug)
    return mcp.chapters_payload(f, slug, name)


# 诊断按**后果**分栏，不按 level 字段分——和上面 HINT_CODES 那一段是同一条道理。
# 混成一栏打，人会以为「还没声明成果」和「你的主结果站在一条自己判死的路上」
# 一样严重，然后开始整体略过这一段。
#
#   这两条直接决定「别人能不能照着做出来」：一条压着整条流程的等级，
#   另一条说的是结果依赖着一条已经放弃的路。
PIPE_BLOCKING_CODES = ("pipeline_weak_step", "pipeline_dead_step")
#   这几条是**记录里两句话打架**，程序不替人裁决：排除了却仍被消费、
#   成果指向不存在的步骤、数据依赖成环。改记录才消得掉。
PIPE_CONFLICT_CODES = ("pipeline_excluded_consumed", "pipeline_excluded_result",
                       "dangling_result", "pipeline_cycle")


def _pipeline_diag_lines(payload: dict) -> tuple[list[str], list[str], list[str]]:
    """(影响能不能复现, 记录自相矛盾, 纯提示)。三栏都可能是空的。"""
    hard: list[str] = []
    conflict: list[str] = []
    hint: list[str] = []
    for d in payload["pipeline"].get("diagnostics") or []:
        row = f"[{d.get('where') or d.get('code')}] {d.get('message', '')}"
        if d.get("code") in PIPE_BLOCKING_CODES:
            hard.append(row)
        elif d.get("code") in PIPE_CONFLICT_CODES:
            conflict.append(row)
        else:
            hint.append(row)
    return hard, conflict, hint


def _print_pipeline_diags(payload: dict, indent: str = "  ") -> int:
    """打三栏诊断，返回「该让 --strict 失败的」条数。"""
    hard, conflict, hint = _pipeline_diag_lines(payload)
    if hard:
        print(f"{indent}⚠ 影响别人能不能照着做出来（**这几条直接决定整条流程的等级**）：")
        for r in hard:
            print(f"{indent}    {r}")
    if conflict:
        print(f"{indent}⚠ 记录里两句话打架，程序不替你裁决：")
        for r in conflict:
            print(f"{indent}    {r}")
    if hint:
        print(f"{indent}ⓘ 提示（**不影响等级，也不影响退出码**）：")
        for r in hint:
            print(f"{indent}    {r}")
    return len(hard) + len(conflict)


def cmd_result(args) -> int:
    """声明「这一步是成果」，或撤销一条声明。

    单开一个子命令而不是给 `new` / 一个通用的 project 命令加参数：这个动作决定
    **整条定稿流程长什么样**——论文 Methods 和附录里出现哪几步、导出的那张图上
    画着谁。和「改个标题」走同一个随手的口子，在语义上就把它降级成了一个字段。
    """
    cfg = load_config()
    root = data_root(cfg)
    slug = pick_project(root, args.project)
    if args.drop:
        info = W.drop_result(root, slug, args.id)
        left = info["results"]
        print(f"已撤销 {slug}/{args.id} 的成果声明。记录一个字都没动——"
              "它只是不再是定稿流程的终点，开发路径上还在原处。")
        print("现在声明的成果：" + ("、".join(r["step"] for r in left) if left
                                    else "（一个都没有，定稿流程也就推不出来了）"))
        return 0
    info = W.set_result(root, slug, args.id, args.note or "")
    print(("已声明" if info["created"] else "已改写") + f" {slug}/{info['step']} 为成果：{info['line']}")
    print("定稿流程会从它沿 `input:` 反向算出来（一步没写 input: 时退回 parent，剔掉 dead）。"
          "成员清单一个字都不存，所以你不用维护它。")
    # 声明完当场把算出来的流程摆出来。不摆的话，用户拿到的是一句干净的「已声明」，
    # 而这个动作真正改变的东西（哪几步进了 Methods、整条链的等级、有没有踩着 dead）
    # 一样都看不见——它们恰恰是这一次调用的全部后果。
    print()
    payload = _pipeline_of(root, slug)
    print(mcp.fmt_pipeline(payload, with_diagnostics=False))
    if payload["pipeline"].get("diagnostics"):
        print()
        _print_pipeline_diags(payload, indent="")
    return 0


def cmd_chapter(args) -> int:
    """列出这个项目的章节：主实验 / 消融实验 / 数据准备，各自多少步、能被追到哪一级、
    有没有自己的成果声明，以及跨章节的那几条边。

    单开一个子命令而不是塞进 `check`：`check` 回答的是「有没有毛病」，而这里回答的是
    「这个项目是怎么分块的、消融那部分做到哪了」——那不是一个毛病，是一张目录。
    混进 check 的后果是它只在有问题的时候才被人看见。

    渲染在 trace_mcp.fmt_chapters（和 MCP 的 trace_read(chapters=true) 是同一份），
    这里只负责读盘和挑项目。
    """
    cfg = load_config()
    root = data_root(cfg)
    slug = pick_project(root, args.project)
    print(mcp.fmt_chapters(_chapters_of(root, slug)), end="")
    return 0


def cmd_pipeline(args) -> int:
    """打印定稿流程，或把它导出成 Methods 草稿 / 一张图 / 一页独立的 HTML。

    三样导出**从同一份派生来**，而且是同一份代码生成的——CLI 和网页各写一遍的话，
    两份迟早不一致，**而其中一份会进论文**。所以这里一行渲染逻辑都没有，
    全部调 trace_mcp 里那三个纯函数（网页那侧走 REST 的
    `/pipeline/figure.svg` 与 `/pipeline/methods.md`，拿到的是同一批字节）。

    `--chapter` 同理：**按章节切开也只有那一份实现**（pipeline_payload），
    这里只是把名字递进去。CLI 自己筛一遍 order 就是第二份实现，而两份不一致的
    那天，你不会知道自己投出去的是哪一份。
    """
    cfg = load_config()
    root = data_root(cfg)
    slug = pick_project(root, args.project)
    payload = _pipeline_of(root, slug, args.chapter or "")

    wrote = []
    for target, render in ((args.svg, mcp.pipeline_svg), (args.page, mcp.pipeline_page)):
        if not target:
            continue
        p = Path(target)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render(payload), encoding="utf-8", newline="\n")
        wrote.append(p)

    # `--methods` 那条路的 **stdout 是文档本身**（`> methods.md` 收走、直接粘进稿子），
    # 所以流程概览、三栏诊断、「已写出」一样都不许混进去 —— 混进去就等于往 Methods
    # 的第一段里塞一句提示。同时给了 --svg 的话文件照写，只是那句回执改走 stderr：
    # 静默写文件同样不行（人不知道自己刚生成了什么），而 stderr 正是为这种话准备的。
    out = sys.stderr if args.methods else sys.stdout
    if args.methods:
        print(mcp.pipeline_methods(payload), end="")
    else:
        # 诊断在这里**按后果分三栏**打（fmt_pipeline 那份关掉），因为「还没声明成果」
        # 和「你的主结果站在一条自己判死的路上」不该长得一样。空态时一栏都不打：
        # 那一条和 fmt_pipeline 里那段话说的是同一件事，打两遍只会让人以为犯了两个错。
        print(mcp.fmt_pipeline(payload, with_diagnostics=False))
        if payload["declared"] and payload["pipeline"].get("diagnostics"):
            print()
            if payload.get("chapter"):
                # 诊断是**整张图**的（core 只算一份）。按章节看的时候必须说清这一点，
                # 否则「卡在 007」和上面那行「这一章 L3」摆在同一屏上是句自相矛盾的话。
                print("  （以下是**整个项目**这张流程图上的诊断，不是只针对本章；"
                      "本章自己的等级和最弱一环见上面。）")
            _print_pipeline_diags(payload, indent="")
    for p in wrote:
        print(f"\n已写出 {p}（**逐字节确定**：同样的记录重新生成一次一模一样，"
              "所以别把它存进仓库当第二份真相，要用就重新生成）", file=out)
    if not payload["declared"] and not args.methods:
        print(f"\n标一个成果：trace_cli.py result -P {slug} <步骤id> --note \"这是什么成果\"")
    # 分了章却在看总图时提一句：论文里主实验和消融本来就是两段 Methods，
    # 而「能按章节导」这件事不说就没人知道（磁盘上分章只是一行 `chapter:`）。
    groups = payload["pipeline"].get("chapters") or []
    if not args.chapter and not args.methods and len(groups) > 1:
        print("\n这个项目分了章节，上面是**合起来的一张图**（共用的准备步骤只出现一次）。"
              "论文里要分段写就一章一章导：")
        for g in groups:
            print(f"  pipeline -P {slug} --chapter \"{g['name'] or mcp.CHAPTER_NONE}\""
                  f"   [{mcp.chapter_label(g['name'])} · {len(g['order'])} 步 · {g['level']}]")
    return 0


def _histogram(levels: list[str]) -> str:
    c = {k: 0 for k in LEVELS}
    for x in levels:
        if x in c:
            c[x] += 1
    return "  ".join(f"{k} {c[k]}" for k in LEVELS if c[k])


def cmd_check(args) -> int:
    """校验不变量 + 按 FORMAT.md 执法。

    FORMAT.md 第 10 节写死了「`check` 和网页会给出等级」，而在这之前 check 只打印
    步数/轨道/树尺寸/警告数，一个等级都不显示 —— L0–L4 在整个系统里只有 MCP 单步
    详情一条出口。这里把三样补上，都是 compile_forest 早就算好的派生字段：

      · L0–L4 分布（自身 / 整条链各一份）—— 链级才是「这个结论追不追得到底」；
      · **最弱的一环**，按它卡住了多少条链排序 —— FORMAT.md 明写「补记录要从最弱
        的那一环补起，不是从最新那一步补起」，不点名就没法照做；
      · 每一条 missing 逐行列出，直接就是待办清单。
    """
    cfg = load_config()
    root = data_root(cfg)
    slugs = [pick_project(root, args.project)] if args.project else [p.slug for p in core.scan_projects(root)]
    errors = weak = warns = 0
    for slug in slugs:
        f = core.compile_forest(core.steps_dir_of(root, slug))
        ws = f["warnings"]
        errs = [w for w in ws if w["level"] == "error"]
        errors += len(errs)
        # 提示不进 warns：--strict 是给 CI 用的闸门，而「这张表没配一句说明」
        # 不是缺陷，用它拦住一次合并只会让人加 --no-verify。
        warns += len([w for w in ws if w["level"] != "error"
                      and w["code"] not in HINT_CODES and w["code"] not in TODO_CODES])
        steps = f["steps"]
        print(f"[{slug}] {len(steps)} 步 · {f['lane_count']} 轨道 · "
              f"树 {f['tree']['w']}×{f['tree']['h']}px · 警告 {len(ws)}（错误 {len(errs)}）")

        traces = [(s, s.get("trace") or {}) for s in steps]
        if traces:
            print("  可溯源性  自身 " + (_histogram([t.get("self", "") for _, t in traces]) or "—"))
            print("            整链 " + (_histogram([t.get("chain", "") for _, t in traces]) or "—"))

        # 谁卡住了最多的链。只点名链级还停在 L0/L1 的那些：L2 以上说明「东西在哪、
        # 代码是哪版」都追得到，再催就是噪音，而噪音会让人开始整体忽略这一段。
        blame: dict[str, int] = {}
        for s, t in traces:
            w = t.get("weakest")
            if w and t.get("chain") in ("L0", "L1"):
                blame[w] = blame.get(w, 0) + 1
        by_id = {s["id"]: (s, t) for s, t in traces}
        for wid, n in sorted(blame.items(), key=lambda kv: (-kv[1], kv[0])):
            s, t = by_id.get(wid, (None, {}))
            if s is None:
                continue
            weak += 1
            print(f"  ↓ 最弱一环 {wid} [{t.get('self')} {LEVEL_LABEL.get(t.get('self'), '')}] "
                  f"{s['title']} —— 卡住 {n} 条链")
            for m in t.get("missing", []):
                print(f"      · {m}")

        # 四档，按**后果**分，不按 level 字段分：写法诊断和分叉诊断都是 warn 级，
        # 但它们一个都不影响 L0–L4。混在一起打，人会以为「表格没写说明」和
        # 「dead 没写结论」一样严重，然后开始整体忽略这一段——而那正是警告失效的方式。
        #   ✕ 错误      结构性问题，进退出码
        #   ⚠ 警告      内容层缺陷，--strict 才拦
        #   ⓘ 写法提示  不影响等级，也不影响退出码
        #   ⑂ 待办      未决的岔路口，连「有问题」都不是
        for w in ws:
            if w["code"] in HINT_CODES or w["code"] in TODO_CODES:
                continue
            print(("  ✕ " if w["level"] == "error" else "  ⚠ ") + f"[{w['where'] or w['code']}] {w['message']}")
        hints = [w for w in ws if w["code"] in HINT_CODES]
        if hints:
            print(f"  ⓘ {len(hints)} 条写法提示（**不影响 L0–L4，也不影响退出码**）：")
            for w in hints:
                print(f"      [{w['where'] or w['code']}] {w['message']}")

        # 未决的岔路口。**既不是错误也不是提示，是待办**：同时开几条线是研究的常态。
        # 单独一栏而不是并进上面那两栏，是因为「有几个决定还没做」是人主动想知道的
        # 东西，而不是被指出来的毛病；措辞里也不能带责备——一带责备，人就会为了让
        # 输出干净随手把一条支标成 dead，那是拿假结论换绿色。
        todo = mcp.open_forks(f)
        if todo:
            print(f"  ⑂ 还有 {len(todo)} 个岔路口没做决定"
                  f"（**待办，不是缺陷，不计入退出码**；同时开几条线是常态）：")
            for g in todo:
                at = g["at"] or "（森林的根之间）"
                print(f"      {at}  {len(g['live'])} 选 1（{' / '.join(g['live'])}）"
                      + (f"  —— {g['decision']}" if g.get("decision") else "  —— 还没写在决定什么"))
            print("      逐个看：forks ／ 结掉一个：把没走通的候选标 dead 并写清为什么放弃")

        # 定稿流程。**只在这个项目真的声明了成果时才出现一个字**——没声明是常态
        # 不是缺陷，每次 check 都念一遍「你还没声明成果」只会让人为了让输出干净
        # 随手指一步当成果，那和拿假结论换绿色是同一件事。
        #
        # 它回答的是 check 原来答不了的那个问题：上面那几栏说的是**开发路径**
        # （全部记录，含走不通的），而投稿前真正要问的是「**产出成果的那条链**
        # 别人能不能照着做出来」。两者的等级可以差很远：一堆没记全的探索性步骤
        # 把整体分布压得很难看，而定稿流程上的那七步其实条条 L3。
        pipe = mcp.pipeline_payload(
            f, slug, core.compute_pipeline({}, []),
            next((x.name for x in core.scan_projects(root) if x.slug == slug), slug))
        if pipe["declared"]:
            p = pipe["pipeline"]
            print(f"  ⇉ 定稿流程 {len(p['order'])} 步 · 整链 {p['level']} "
                  f"{LEVEL_LABEL.get(p['level'], '')}"
                  + (f" · 最弱一环 {p['weakest']}" if p.get("weakest") else "")
                  + f" · 成果 {'、'.join(r['step'] for r in p['results'])}")
            print("      " + " → ".join(p["order"]))
            # 这几条**算进 --strict**：它们说的是「别人照着这条链做不出来」，
            # 而那正是 --strict 存在的理由。纯提示（info 级）不算，理由同上。
            warns += _print_pipeline_diags(pipe, indent="      ")
            # 分了章的项目：每一章各有一条自己的流程和自己的等级。整份流程的等级是
            # **全项目最弱的一步**，拿它当消融那一章的等级，就是让消融替别的章背锅——
            # 而「消融这部分别人能不能重做」是个要单独回答的问题。
            for g in p.get("chapters") or []:
                print(f"      · {mcp.chapter_label(g['name']):<12} {len(g['order']):>3} 步 · "
                      f"{g['level']} {LEVEL_LABEL.get(g['level'], '')}"
                      + (f" · 最弱一环 {g['weakest']}" if g.get("weakest") else "")
                      + (f" · 借了别的章 {len(g['external'])} 步" if g.get("external") else "")
                      + f" · 成果 {'、'.join(g['results'])}")
            print(f"      逐步看 / 导出：pipeline -P {slug} [--methods | --svg 图.svg]"
                  + ("  ／ 分章导：pipeline -P %s --chapter <章节名>" % slug
                     if len(p.get("chapters") or []) > 1 else ""))

        # 章节的诊断。**单独一栏，而且明写「不影响等级」**：这三条一条都不改变
        # L0–L4，也不进退出码。混进上面那两栏，人会以为「两个人各写了一句章节说明」
        # 和「dead 没写结论」一样严重，然后开始整体忽略——那正是警告失效的方式。
        #
        # 一个 `chapter:` 都没写的项目这里一个字都不打（forest 里那个键整个不存在）。
        cdiags = (f.get("chapters") or {}).get("diagnostics") or []
        if cdiags:
            print(f"  § {len(cdiags)} 条章节提示（**不影响 L0–L4，也不影响退出码**）：")
            for d in cdiags:
                print(f"      [{d.get('where') or d.get('code')}] {d.get('message', '')}")
            print(f"      看章节：chapter -P {slug}")

        # 已确认不存在的外部位置。**不是警告，也不进退出码**：路径没了是溯源
        # 结论（P4），不是这份记录写错了。但 check 是三个出口里唯一一个人会
        # 天天跑的，而它以前对此一个字都不说——网页顶上有横幅、`paths` 有汇总、
        # 只有这里静默，于是「57 GB 没了」这件事在 CI 里永远看不见。
        gone = [(s["id"], p) for s in steps for p in (s.get("paths") or [])
                if p.get("state") == "missing"]
        if gone:
            print(f"  ⊘ {len(gone)} 处记下来的位置已确认不存在"
                  f"（记录保留——那是溯源结论，不是笔误；不计入退出码）：")
            for sid, p in gone:
                size = f"{core.fmt_size(p['size'])} · " if p.get("size") else ""
                print(f"      {sid}  {p['location']}  （{size}{p['missing']} 起）")
            print("      逐条看：paths --missing ／ 重新核对：paths --check"
                  "（只在看得见那些路径的机器上跑）")

    if errors:
        return 1
    # --strict 给 CI 用：把「内容层缺陷」也算成失败。默认不算，是因为 wip 阶段的
    # 记录本来就该是残缺的（内核只对 done/dead 报这些警告，但仍有正当的过渡期），
    # 天天红一片只会训练大家忽略它。要拦就得是显式选择。
    if getattr(args, "strict", False) and (weak or warns):
        print(f"\n--strict：{warns} 条内容层警告、{weak} 个最弱一环 —— 判为失败。")
        return 1
    return 0


# ---------------------------------------------------------------- build


def cmd_build(args) -> int:
    cfg = load_config()
    root = data_root(cfg)
    out = (ROOT / args.out).resolve()
    title = cfg.get("title", "科研溯源")
    tpl = (WEB / "index.html").read_text(encoding="utf-8")
    projects = core.scan_projects(root)

    # 清空内容而不是删掉目录本身：Windows 上只要有进程把 dist 当作工作目录
    # （比如 python -m http.server），rmtree 就会 WinError 32，构建整个失败。
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()

    def render(asset_prefix: str, project: str, forest, plist, pipe_svg: str = "") -> str:
        return (
            tpl.replace("__ASSET__", asset_prefix)
            .replace("__BASE__", "")
            .replace("__TITLE__", title)
            .replace("__MODE__", "static")
            .replace("__PROJECT__", project)
            .replace("__DATA__", inline(forest) if forest is not None else "")
            .replace("__PROJECTS__", inline(plist))
            # 静态导出里那张图**灌进页面**而不是让页面去 fetch：file:// 下取一个
            # 相对路径会被当成跨源，断网双击打开时页面上会是一块空白。灌进来的
            # 字节和同目录 pipeline.svg、和服务端 /pipeline/figure.svg 完全一样
            # ——它们是同一个纯函数的同一份输出。
            .replace("__PIPESVG__", inline(pipe_svg) if pipe_svg else "")
        )

    # 静态产物必须是确定性的：不写入任何时间戳或版本号，否则
    # "删掉产物再重建，结果逐字节一致"这条验收就假了。
    plist, total = [], 0
    forests = {}
    for p in projects:
        f = core.compile_forest(core.steps_dir_of(root, p.slug))
        forests[p.slug] = f
        counts = {"wip": 0, "done": 0, "dead": 0}
        for s in f["steps"]:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        d = p.to_dict()
        d.update(steps=len(f["steps"]), counts=counts, warnings=len(f["warnings"]),
                 latest=max((s["date"] for s in f["steps"] if s["date"]), default=""))
        # 索引页卡片上那一行章节。形状和 /api/projects 那一份逐字一样（网页只有
        # 一份渲染器），**没有章节就整个键不出现**——静态导出里那些项目的卡片
        # 必须和分章之前逐字节相同。
        chs = (f.get("chapters") or {}).get("chapters") or []
        if chs:
            d["chapters"] = [{"name": c["name"], "n": c["n"]} for c in chs]
        plist.append(d)
        total += len(f["steps"])

    (out / "index.html").write_text(render("", "", None, plist), encoding="utf-8", newline="\n")
    for name in STATIC_ASSETS:
        shutil.copyfile(WEB / name, out / name)

    for p in projects:
        pd = out / "p" / p.slug
        pd.mkdir(parents=True)
        # 定稿流程那**三样导出**：**只在这个项目声明了成果时才生成**（没声明就没有
        # 流程，三个空文件比没有这三个文件更让人困惑）。它们和 index.html 是两条
        # 路径的两个出口——index 是开发路径（全部记录，给自己查问题），这三样只有
        # 产出成果的那条链，是**能直接发给合作者**的那一份。
        # 无脚本、无外部资源，双击就能开，断网也行；逐字节确定（P3）。
        #
        # 三样都出自 trace_mcp 里那**一份**生成器，也就是服务端 /pipeline/*
        # 和 `trace_cli.py pipeline --svg/--methods/--page` 用的同一个纯函数。
        # 网页上那三个按钮指到这三个文件（静态）或那三条路由（服务），所以
        # 「屏幕上看到的」和「发出去的」永远是同一批字节。
        pipe = mcp.pipeline_payload(forests[p.slug], p.slug,
                                    core.compute_pipeline({}, []), p.name)
        pipe_svg = ""
        if pipe["declared"]:
            pipe_svg = mcp.pipeline_svg(pipe)
            (pd / "pipeline.svg").write_text(pipe_svg, encoding="utf-8", newline="\n")
            (pd / "pipeline.md").write_text(mcp.pipeline_methods(pipe),
                                            encoding="utf-8", newline="\n")
            (pd / "pipeline.html").write_text(
                mcp.pipeline_page(pipe, title=f"{p.name} · 定稿流程"),
                encoding="utf-8", newline="\n")
            # 分了章的项目**每一章再各出一份**。论文里主实验一段 Methods、消融一段，
            # 本来就是两份文件；只出合起来的那一份，人拿到之后要做的第一件事就是
            # 用手把它剪成两半——而剪的时候「哪几步是借来的」这条信息正好会丢。
            #
            # 只在不止一组时才出：一个章节的项目里，分章那一份和总的那一份逐字节
            # 相同，多写三个文件只会让人怀疑它们哪里不一样。
            groups = pipe["pipeline"].get("chapters") or []
            if len(groups) > 1:
                # 文件名不许拿章节名直接拼（它合法地可以是 `主实验/数据准备`、`CON`、`..`）。
                # 派生规则只有 mcp.chapter_export_name 一份，去重也在那里。
                #
                # 喂进去的顺序是**章节清单那一份**（core 的 compute_chapters，按章节
                # 被开启的先后），不是这里这份按 `result:` 声明顺序排的分组：消歧的
                # 序号跟着顺序走，而网页那侧的下载名用的正是章节清单的顺序。两边喂
                # 不同的顺序，撞名的那两章在磁盘上和在浏览器里就会得到不同的后缀。
                ch_all = [c["name"] for c in
                          ((forests[p.slug].get("chapters") or {}).get("chapters") or [])]
                if any(g["name"] == "" for g in groups):
                    ch_all.append("")          # 未分章那一组不是章节，不在清单里
                names = mcp.chapter_export_name(ch_all)
                for g in groups:
                    one = mcp.pipeline_payload(
                        forests[p.slug], p.slug, core.compute_pipeline({}, []), p.name,
                        g["name"] or mcp.CHAPTER_NONE)
                    # 拿回来的**真的是**要的那一章吗。未分章那一组只能用记号点名，
                    # 而一个**真叫 `-` 的章节**会赢过记号（真名优先，写在
                    # mcp.CHAPTER_NONE 那段里）。撞上了就跳过，别写那份文件：
                    # 一份文件名写着「未分章」、内容却是别人那一章的 Methods，
                    # 比少一份糟得多——而它可能就是投出去的那一份。
                    if (one.get("chapter") or {}).get("name") != g["name"]:
                        print(f"⚠ {p.name}：未分章那一组导不出来——这个项目里有一个"
                              f"真叫「{mcp.CHAPTER_NONE}」的章节，它按真名优先赢走了那个记号。"
                              "那一组的步骤仍在合起来的 pipeline.md 里")
                        continue
                    stem = names[g["name"]]
                    (pd / f"{stem}.svg").write_text(mcp.pipeline_svg(one),
                                                    encoding="utf-8", newline="\n")
                    (pd / f"{stem}.md").write_text(mcp.pipeline_methods(one),
                                                   encoding="utf-8", newline="\n")
                    (pd / f"{stem}.html").write_text(
                        mcp.pipeline_page(
                            one, title=f"{p.name} · {mcp.chapter_label(g['name'])} · 定稿流程"),
                        encoding="utf-8", newline="\n")
        (pd / "index.html").write_text(
            render("../../", p.slug, forests[p.slug], plist, pipe_svg),
            encoding="utf-8", newline="\n")
        src = core.steps_dir_of(root, p.slug)
        if src.is_dir():
            shutil.copytree(src, pd / "steps", ignore=shutil.ignore_patterns(".*"))

    print(f"已导出 {len(projects)} 个项目 · {total} 步 → {out}")
    print(f"直接打开: {(out / 'index.html').as_uri()}")
    return 0


# ---------------------------------------------------------------- serve


def cmd_serve(args) -> int:
    import uvicorn
    from trace_server import create_app

    cfg = load_config()
    app = create_app(cfg)
    base = f"/t/{cfg['space']}/" if cfg.get("space") else "/"
    print(f"→ http://{args.host}:{args.port}{base}")
    if cfg.get("token"):
        print(f"  写入令牌: {cfg['token']}")
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    """整棵 argparse。**单独一个函数，是为了让「有哪些子命令」可以被问出来。**

    上一版这张表只存在于 main() 内部的局部变量里，于是「CLI 真有的子命令」这件事
    在程序外面读不到，测试只好手抄一份名单——而手抄的那份漏掉了后来加的 result /
    pipeline，那道闸门对新命令恰好是不设防的。派生的东西要能被派生出来（P1）。
    """
    ap = argparse.ArgumentParser(prog="trace", description="科研记录与溯源")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="初始化")
    p.add_argument("--title", default="科研溯源")
    p.add_argument("--project", default="default", help="首个项目的名字")
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR, metavar="路径",
                   help=f"数据仓在哪（projects/ 的父目录），默认 {DEFAULT_DATA_DIR}。"
                        "它应当是一个**私有**仓库，好让代码仓可以公开。"
                        "填 . 就是和代码放一起——那样自动 git 同步会被禁掉。")
    p.add_argument("--force", action="store_true")
    p.add_argument("--git", action="store_true",
                   help="开自动 git 同步（默认关）。数据仓和代码仓是同一个 git 仓库时会被拒绝——"
                        "那等于把未发表的科研笔记推进公开的代码仓")
    p.add_argument("--no-git", action="store_true",
                   help="明确不开自动 git 同步（现在是默认行为；保留它是为了让老脚本继续能跑）")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("projects", help="列出项目")
    p.set_defaults(fn=cmd_projects)

    p = sub.add_parser("new-project", help="新建项目")
    p.add_argument("--name", required=True)
    p.set_defaults(fn=cmd_new_project)

    p = sub.add_parser("new", help="新建一步")
    p.add_argument("--title", required=True)
    p.add_argument("-P", "--project", default=None)
    p.add_argument("-p", "--parent", default=None)
    p.add_argument("-s", "--status", default=core.DEFAULT_STATUS, choices=list(core.STATUSES))
    p.add_argument("--date", default="")
    p.add_argument("--commit", default="")
    p.add_argument("--author", default="human")
    p.add_argument("--tags", default="")
    p.add_argument("--path", action="append", metavar="位置|角色|说明|k=v",
                   help='外部产物的位置，可重复。如 --path "/blue/组/用户/data | input | 训练集 | size=12884901888"。'
                        '角色是 input/script/output/evidence 之一，属性是 size/n/md5/sha256/checked/missing；'
                        '老写法 "位置 | 说明" 照样有效')
    p.add_argument("--input", action="append", metavar="步骤id|消费的产物",
                   help='**数据依赖**，可重复。如 --input "013 | pocket_composition.csv"。'
                        '它和 --parent 是两件事：parent 是「我当时接着哪一步想」（树，单父），'
                        'input 是「这些字节从哪来」（DAG，可以有好几个）')
    p.add_argument("--code", action="append", metavar="kind|位置|k=v",
                   help='代码在哪，可重复。kind ∈ git/snapshot/container。'
                        '如 --code "snapshot | /orange/lab/snap/20260809 | manifest=MANIFEST.md5 n=43"。'
                        '代码不在 git 里时用 snapshot，它一样能上 L2 —— 别再把快照路径塞进 --commit')
    p.add_argument("--branch", default="", metavar="extends|alternative[|说明]",
                   help='这一步和它 parent 之间那条边是什么性质。默认 extends（普通延伸，不用写）；'
                        'alternative 是**互斥候选**——「我和我的兄弟们是同一个问题的几个答案，'
                        '只能选一条走下去」。可带说明：--branch "alternative | 先试最便宜的"。'
                        '注意「又分出一条支线去试别的」不是互斥候选，那就是普通延伸')
    p.add_argument("--decision", default="", metavar="在决定什么",
                   help='写在**分叉点自己**身上的一句话：它底下那几个互斥候选在决定什么。'
                        '候选有谁、选了谁都算得出来，唯独这句话只能人写')
    p.add_argument("--chapter", default="", metavar="章节名[|这个章节是什么]",
                   help='这一步开启的**章节**（同一个项目里并列的几块：主实验 / 消融 / 数据准备）。'
                        '如 --chapter "消融实验 | 逐个拿掉模块，对着主实验的 023 比"。'
                        '**只写在开启那条线的第一步上**，底下整棵子树沿 parent 自动继承——'
                        '每一步各写一遍的代价是改一次章节名要改二十个文件。'
                        '章节之间**互不排斥**（都要留着，论文里本来就是两段 Methods）；'
                        '「同一个问题的几个答案、只能选一条」是 --branch alternative，不是章节')
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("fork", help="把几个兄弟成组标成互斥候选（同一个问题的几个答案，只能选一条）")
    p.add_argument("ids", nargs="+", metavar="步骤id", help="至少两个，必须是同一个父节点下的兄弟")
    p.add_argument("-P", "--project", default=None)
    p.add_argument("--decision", default="", metavar="在决定什么",
                   help="顺手写在它们父节点上的那句话，如「类别不平衡怎么处理？只能选一条走下去」")
    p.add_argument("--note", action="append", metavar="步骤id=说明",
                   help="某个候选自己的角度，可重复，如 --note \"012=先试最便宜的：只调采样权重\"")
    p.set_defaults(fn=cmd_fork)

    p = sub.add_parser("forks", help="列出岔路口（默认只列还没做决定的那些）")
    p.add_argument("-P", "--project", default=None)
    p.add_argument("--all", action="store_true", help="连已经做完决定的岔路口一起列")
    p.set_defaults(fn=cmd_forks)

    p = sub.add_parser("rm", help="真删一步（只用于误建/测试数据；失败的实验请标 dead）")
    p.add_argument("id")
    p.add_argument("--reason", required=True, help="为什么删。必填 —— 目录一删，这句话就是唯一留下来的")
    p.add_argument("-P", "--project", default=None)
    p.add_argument("--by", default="human")
    p.add_argument("--date", default="")
    p.set_defaults(fn=cmd_rm)

    p = sub.add_parser("mv", help="把一步（连同整棵子树）改挂到别的父节点下。原因必填")
    p.add_argument("id")
    p.add_argument("--parent", default=None, metavar="新父id",
                   help="新的父节点 id；**不给这个参数**就是提为根，自己开一棵树")
    p.add_argument("--reason", required=True,
                   help="为什么移。必填 —— 移完这棵树就和创建顺序对不上了，"
                        "「为什么 016 挂在 013b 下面」只有这句话答得了。"
                        "写清是哪条数据依赖决定了新的父子关系，别写「修正结构」")
    p.add_argument("-P", "--project", default=None)
    p.add_argument("--by", default="human")
    p.add_argument("--date", default="")
    p.set_defaults(fn=cmd_mv)

    p = sub.add_parser("paths", help="列出所有外部产物的位置，或逐条核对它们还在不在")
    p.add_argument("-P", "--project", default=None)
    p.add_argument("--kind", default=None, help="只看某一类：hpc / github / dropbox / object / url …")
    p.add_argument("--missing", action="store_true",
                   help="只列已确认不存在的那些（记录不会被删——那是溯源结论，不是笔误）")
    p.add_argument("--check", action="store_true",
                   help="逐条 stat 一遍，把结论写回 checked= / missing=。"
                        "**只在看得到那些路径的机器上跑才有意义**：/blue/… 多半挂在超算上，"
                        "在笔记本上跑只会得到一屏「够不着」。够不着≠不存在，那种情况一个字都不写；"
                        "s3:// / https:// 一律不探测（那等于让工具替你去访问网络）")
    p.add_argument("--count", action="store_true",
                   help="配合 --check：目录还要数一层条目数写进 n=。默认不数，"
                        "因为数一个几十万文件的目录要遍历整棵树，在网络盘上能卡很久")
    p.set_defaults(fn=cmd_paths)

    p = sub.add_parser("tr", help="列出还缺哪些语言版本，或从一份写好的译文补一版进去")
    p.add_argument("-P", "--project", default=None)
    p.add_argument("--lang", default="en", metavar="语言码", help="短语言码，默认 en（ja / zh-Hant 同理）")
    p.add_argument("--step", default=None, help="哪一步。补翻译时给它；不给且带 --file 就要显式 --project-note")
    p.add_argument("--project-note", action="store_true", help="翻译项目笔记 project.<lang>.md")
    p.add_argument("--file", default=None, metavar="路径",
                   help="写好的译文（可以整份带 front-matter，只有 title/name 会被采用）")
    p.add_argument("--title", default=None, help="覆盖译文里的标题（项目笔记则是显示名）")
    p.add_argument("--drop", action="store_true", help="删掉这一步的该语言版本。原文不受影响")
    p.set_defaults(fn=cmd_tr)

    p = sub.add_parser("result",
                       help="声明「这一步是成果」——**定稿流程唯一写下来的那件事**，其余全是算出来的")
    p.add_argument("id", metavar="步骤id")
    p.add_argument("-P", "--project", default=None)
    p.add_argument("--note", default="", metavar="这是什么成果",
                   help='一句话，如 --note "主结果：亲和力预测 AUC 0.91"。'
                        "会显示在流程和 Methods 草稿的开头")
    p.add_argument("--drop", action="store_true",
                   help="撤销这一条声明。不删任何记录，也不要求写原因——"
                        "`result:` 不是一段历史，是一个当前指针（论文现在报的是哪一步）")
    p.set_defaults(fn=cmd_result)

    p = sub.add_parser("chapter",
                       help="列出章节：同一个项目里并列的几块（主实验 / 消融 / 数据准备），"
                            "各自多少步、能被追到哪一级、有没有自己的成果")
    p.add_argument("-P", "--project", default=None)
    p.set_defaults(fn=cmd_chapter)

    p = sub.add_parser("pipeline",
                       help="定稿流程：真正产出成果的那一条链（给别人照着做、给论文用）。"
                            "开发路径——全部记录，含走不通的——看 check / 网页")
    p.add_argument("-P", "--project", default=None)
    p.add_argument("--chapter", default="", metavar="章节名",
                   help="只出这一章的流程（--methods / --svg / --page 跟着只出这一章）。"
                        "章节名照抄记录里那个字符串，**不做大小写折叠或近似匹配**——"
                        "替你猜一次，导出的是哪一章就取决于猜法，而其中一份会进论文。"
                        f"未分章那一组写 \"{mcp.CHAPTER_NONE}\"（多数项目的主线没起过名字，"
                        "它常常就是主实验）。有哪几章：chapter -P <项目>")
    p.add_argument("--methods", action="store_true",
                   help="输出 **Methods 草稿**（markdown）到 stdout：按流程顺序，每一步的"
                        "「做了什么」原文、完整命令、代码位置、产物路径与校验和。"
                        "**写论文时最顺手的入口**（`> methods.md` 收走即可）。"
                        "它是初稿不是成品——里面只有记录里已有的事实，没有替你编的论文腔句子")
    p.add_argument("--svg", default=None, metavar="文件",
                   help="导出那张图：自包含 SVG，不引任何外部资源、没有脚本，黑白打印可读")
    p.add_argument("--page", default=None, metavar="文件",
                   help="导出一页能发给合作者的独立 HTML（图 + Methods 草稿，只含定稿流程）。"
                        "`build` 也会给每个声明了成果的项目生成一份 p/<项目>/pipeline.html")
    p.set_defaults(fn=cmd_pipeline)

    p = sub.add_parser("check", help="校验不变量，并按 FORMAT.md 给出 L0–L4 可溯源性等级")
    p.add_argument("-P", "--project", default=None)
    p.add_argument("--strict", action="store_true",
                   help="把内容层缺陷（dead 没写结论、图没图注、链卡在 L0/L1）也算成失败，给 CI 用")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("build", help="静态导出")
    p.add_argument("--out", default="dist")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("serve", help="起服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8100)
    p.add_argument("--log-level", default="info")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("url", help="打印访问地址与令牌")
    p.set_defaults(fn=cmd_url)
    return ap


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 cp936，中文提示会乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except W.WriteError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
