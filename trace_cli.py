"""trace_cli — 命令行入口。

    python trace_cli.py init                             初始化
    python trace_cli.py projects                         列出项目
    python trace_cli.py new-project --name "SMARTAffinity"
    python trace_cli.py new -P <项目> --title "..."       新建一步
    python trace_cli.py check [-P <项目>]                 校验不变量
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
import trace_write as W
from trace_server import CONFIG_PATH, ROOT, WEB, load_config, make_config

STATIC_ASSETS = ("style.css", "app.js", "md.js")


def data_root(cfg: dict) -> Path:
    root = (ROOT / cfg.get("data_dir", ".")).resolve()
    core.ensure_layout(root)
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


def cmd_init(args) -> int:
    if CONFIG_PATH.is_file() and not args.force:
        print(f"config.json 已存在（--force 覆盖）: {CONFIG_PATH}")
        return 1
    cfg = make_config(args.title)
    cfg["git"]["enabled"] = not args.no_git
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    root = data_root(cfg)
    if not core.scan_projects(root):
        W.create_project(root, args.project)

    if not args.no_git and not (ROOT / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(ROOT), check=False)

    print(f"已写入 {CONFIG_PATH}")
    print(f"  访问路径  /t/{cfg['space']}/")
    print(f"  写入令牌  {cfg['token']}")
    print("\nconfig.json 含密钥，已在 .gitignore 中。请另行备份。")
    if cfg["git"]["enabled"]:
        print("\n⚠ 自动 git 同步已开启。确认 remote 指向**私有**仓库——")
        print("  否则你的科研笔记会被自动推到公开仓库上。")
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
    )
    print(("已创建 " if created else "已存在 ") + f"{slug}/{step.id}")
    print(sd / step.dirname / core.NOTE_NAME)
    return 0


def cmd_check(args) -> int:
    cfg = load_config()
    root = data_root(cfg)
    slugs = [pick_project(root, args.project)] if args.project else [p.slug for p in core.scan_projects(root)]
    errors = 0
    for slug in slugs:
        f = core.compile_forest(core.steps_dir_of(root, slug))
        ws = f["warnings"]
        errs = [w for w in ws if w["level"] == "error"]
        errors += len(errs)
        print(f"[{slug}] {len(f['steps'])} 步 · {f['lane_count']} 轨道 · "
              f"树 {f['tree']['w']}×{f['tree']['h']}px · 警告 {len(ws)}（错误 {len(errs)}）")
        for w in ws:
            print(("  ✕ " if w["level"] == "error" else "  ⚠ ") + f"[{w['where'] or w['code']}] {w['message']}")
    return 1 if errors else 0


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
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-git", action="store_true")
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
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("check", help="校验不变量")
    p.add_argument("-P", "--project", default=None)
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
