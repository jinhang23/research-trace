"""trace_cli — 命令行入口。

    python trace_cli.py init                       初始化（生成 config.json / steps / git）
    python trace_cli.py new  --title "..." [-p ID] 新建一步（G3：新建一步 ≤ 1 条命令）
    python trace_cli.py check                      校验不变量，打印警告
    python trace_cli.py build [--out dist]         静态导出，file:// 可直接打开
    python trace_cli.py serve [--port 8100]        起服务
    python trace_cli.py url                        打印带 space 的访问地址和写入令牌
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


def steps_dir(cfg: dict) -> Path:
    return (ROOT / cfg["steps_dir"]).resolve()


# ---------------------------------------------------------------- init


def cmd_init(args) -> int:
    if CONFIG_PATH.is_file() and not args.force:
        print(f"config.json 已存在（--force 覆盖）: {CONFIG_PATH}")
        return 1
    cfg = make_config(args.title)
    cfg["git"]["enabled"] = not args.no_git
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    steps_dir(cfg).mkdir(parents=True, exist_ok=True)

    if not args.no_git and not (ROOT / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(ROOT), check=False)

    print(f"已写入 {CONFIG_PATH}")
    print(f"  访问路径  /t/{cfg['space']}/")
    print(f"  写入令牌  {cfg['token']}")
    print("\nconfig.json 含密钥，已在 .gitignore 中。请另行备份。")
    return 0


def cmd_url(args) -> int:
    cfg = load_config()
    base = f"/t/{cfg['space']}/" if cfg.get("space") else "/"
    print(f"路径: {base}")
    print(f"令牌: {cfg.get('token') or '(未设置 — 写入不设防)'}")
    return 0


# ---------------------------------------------------------------- new / check


def cmd_new(args) -> int:
    cfg = load_config()
    sd = steps_dir(cfg)
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
    d = sd / step.dirname
    print(("已创建 " if created else "已存在 ") + step.id)
    print(d / core.NOTE_NAME)
    return 0


def cmd_check(args) -> int:
    cfg = load_config()
    forest = core.compile_forest(steps_dir(cfg))
    ws = forest["warnings"]
    errors = [w for w in ws if w["level"] == "error"]
    for w in ws:
        print(("✕ " if w["level"] == "error" else "⚠ ") + f"[{w['where'] or w['code']}] {w['message']}")
    print(f"\n{len(forest['steps'])} 步 · {forest['lane_count']} 条轨道 · {len(ws)} 条警告（其中 {len(errors)} 条错误）")
    return 1 if errors else 0


# ---------------------------------------------------------------- build


def cmd_build(args) -> int:
    cfg = load_config()
    sd = steps_dir(cfg)
    out = (ROOT / args.out).resolve()

    forest = core.compile_forest(sd)
    # 静态产物必须是确定性的：不写入任何时间戳或版本号，
    # 否则"删掉产物再重建，结果逐字节一致"这条验收就假了。
    payload = json.dumps(forest, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")

    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = (
        html.replace("__ASSET__", "")
        .replace("__BASE__", "")
        .replace("__TITLE__", cfg.get("title", "科研溯源"))
        .replace("__MODE__", "static")
        .replace("__DATA__", payload)
    )

    # 清空内容而不是删掉目录本身：Windows 上只要有进程把 dist 当作工作目录
    # （比如 python -m http.server），rmtree 就会 WinError 32，构建整个失败。
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()

    (out / "index.html").write_text(html, encoding="utf-8", newline="\n")
    for name in STATIC_ASSETS:
        shutil.copyfile(WEB / name, out / name)

    if sd.is_dir():
        shutil.copytree(sd, out / "steps", ignore=shutil.ignore_patterns(".*"))

    print(f"已导出 {len(forest['steps'])} 步 → {out}")
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
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-git", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("new", help="新建一步")
    p.add_argument("--title", required=True)
    p.add_argument("-p", "--parent", default=None)
    p.add_argument("-s", "--status", default=core.DEFAULT_STATUS, choices=list(core.STATUSES))
    p.add_argument("--date", default="")
    p.add_argument("--commit", default="")
    p.add_argument("--author", default="human")
    p.add_argument("--tags", default="")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("check", help="校验不变量")
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
