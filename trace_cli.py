"""trace_cli — 命令行入口。

    python trace_cli.py init                             初始化
    python trace_cli.py projects                         列出项目
    python trace_cli.py new-project --name "SMARTAffinity"
    python trace_cli.py new -P <项目> --title "..."       新建一步
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
    )
    print(("已创建 " if created else "已存在 ") + f"{slug}/{step.id}")
    print(sd / step.dirname / core.NOTE_NAME)
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
    print(f"⚠ id {info['id']} 可能被下一个新建的步骤重用。")
    return 0


KIND_LABEL = {
    "hpc": "超算", "github": "GitHub", "git": "Git", "dropbox": "Dropbox", "drive": "Drive",
    "object": "对象存储", "archive": "数据仓库", "mlhub": "实验平台", "url": "链接",
    "local": "本机", "path": "路径",
}


def cmd_paths(args) -> int:
    """把一个项目里所有外部产物的位置列出来 —— 溯源时最常问的"东西在哪"。"""
    cfg = load_config()
    root = data_root(cfg)
    slugs = [pick_project(root, args.project)] if args.project else [p.slug for p in core.scan_projects(root)]
    total = 0
    for slug in slugs:
        f = core.compile_forest(core.steps_dir_of(root, slug), with_files=False)
        rows = [(s, p) for s in f["steps"] for p in s["paths"]
                if not args.kind or p["kind"] == args.kind]
        if not rows:
            continue
        print(f"[{slug}]")
        for s, p in rows:
            total += 1
            print(f"  {s['id']:<5} {KIND_LABEL.get(p['kind'], p['kind']):<6} {p['location']}"
                  + (f"\n{'':>14}{p['note']}" if p["note"] else ""))
        print()
    if not total:
        print("还没有记录任何外部路径。用 trace_cli.py new --path \"…\" 或在网页里加。")
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
        warns += len(ws) - len(errs)
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

        for w in ws:
            print(("  ✕ " if w["level"] == "error" else "  ⚠ ") + f"[{w['where'] or w['code']}] {w['message']}")

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

    def render(asset_prefix: str, project: str, forest, plist) -> str:
        return (
            tpl.replace("__ASSET__", asset_prefix)
            .replace("__BASE__", "")
            .replace("__TITLE__", title)
            .replace("__MODE__", "static")
            .replace("__PROJECT__", project)
            .replace("__DATA__", inline(forest) if forest is not None else "")
            .replace("__PROJECTS__", inline(plist))
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
        plist.append(d)
        total += len(f["steps"])

    (out / "index.html").write_text(render("", "", None, plist), encoding="utf-8", newline="\n")
    for name in STATIC_ASSETS:
        shutil.copyfile(WEB / name, out / name)

    for p in projects:
        pd = out / "p" / p.slug
        pd.mkdir(parents=True)
        (pd / "index.html").write_text(render("../../", p.slug, forests[p.slug], plist),
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


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 cp936，中文提示会乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

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
    p.add_argument("--path", action="append", metavar="位置|说明",
                   help='外部产物的位置，可重复。如 --path "/blue/组/用户/data | 训练集，12 GB"')
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("rm", help="真删一步（只用于误建/测试数据；失败的实验请标 dead）")
    p.add_argument("id")
    p.add_argument("--reason", required=True, help="为什么删。必填 —— 目录一删，这句话就是唯一留下来的")
    p.add_argument("-P", "--project", default=None)
    p.add_argument("--by", default="human")
    p.add_argument("--date", default="")
    p.set_defaults(fn=cmd_rm)

    p = sub.add_parser("paths", help="列出所有外部产物的位置")
    p.add_argument("-P", "--project", default=None)
    p.add_argument("--kind", default=None, help="只看某一类：hpc / github / dropbox / object / url …")
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

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except W.WriteError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
