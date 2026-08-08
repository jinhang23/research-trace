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
KIND_LABEL = {
    "hpc": "超算", "github": "GitHub", "git": "Git", "dropbox": "Dropbox", "drive": "Drive",
    "object": "对象存储", "archive": "数据仓库", "mlhub": "实验平台", "url": "链接",
    "local": "本机", "path": "路径",
}
PATHS_DESC = (
    "外部产物的位置，每条写成 \"位置 | 说明\"。GB 级的东西（数据集、checkpoint）不要传进来，"
    "只记它在哪 —— 这是溯源的一半。例："
    "\"/blue/<组>/<用户>/exp/agnews-clean | 去重后的训练集，12 GB\"、"
    "\"https://github.com/你/仓库/tree/9b7d112 | 跑这一步的代码\"、"
    "\"s3://bucket/ckpt/run042.pt | sha256:ab12cd34, 4.2 GB\"。"
)
DEFAULT_AUTHOR = os.environ.get("TRACE_AUTHOR", "agent")

BODY_TEMPLATE = (
    "## 为什么\n（承接上一步的什么发现，想验证什么假设）\n\n"
    "## 做了什么\n\n## 结果\n\n## 结论\n\n## 下一步\n"
)


class ToolError(Exception):
    pass


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

    def create(self, project, payload):
        return self._call("POST", f"/api/p/{urllib.parse.quote(project)}/steps", payload)

    def update(self, project, sid, patch):
        return self._call("PATCH", f"/api/p/{urllib.parse.quote(project)}/steps/{urllib.parse.quote(sid)}", patch)

    def attach(self, project, sid, data, name, mime):
        h = {"Content-Type": mime}
        if name:
            h["X-Filename"] = urllib.parse.quote(name)
        return self._call("POST", f"/api/p/{urllib.parse.quote(project)}/steps/{urllib.parse.quote(sid)}/files",
                          raw=data, headers=h)


class LocalBackend:
    """直接读写文件。agent 和数据在同一台机器上时用这个，不需要起服务。"""

    def __init__(self, root: Path) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import trace_core as core
        import trace_write as W

        self.core, self.W = core, W
        self.root = root.resolve()
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
        return out

    def _guard(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except self.W.WriteError as e:
            raise ToolError(str(e)) from None

    def create(self, project, payload):
        step, created = self._guard(
            self.W.create_step, self._sd(project),
            parent=payload.get("parent"), title=payload.get("title", ""),
            status=payload.get("status", "wip"), body=payload.get("body"),
            date=payload.get("date", ""), commit=payload.get("commit", ""),
            author=payload.get("author", ""), key=payload.get("key", ""),
            tags=payload.get("tags"), paths=payload.get("paths"),
        )
        d = step.to_dict()
        d["created"] = created
        return d

    def update(self, project, sid, patch):
        return self._guard(self.W.update_step, self._sd(project), sid, patch).to_dict()

    def attach(self, project, sid, data, name, mime):
        return self._guard(self.W.attach_auto, self._sd(project), sid, data, filename=name or "", mime=mime)


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


def make_backend() -> HttpBackend | LocalBackend:
    cfg, _src = read_config()

    url = (os.environ.get("TRACE_URL") or cfg.get("url") or "").strip()
    if url:
        token = (os.environ.get("TRACE_TOKEN") or cfg.get("token") or "").strip()
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


# ---------------------------------------------------------------- 渲染

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
        if s.get("paths"):
            extra.append(f"{len(s['paths'])} 路径")
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
    warn = [w for w in forest["warnings"]]
    if warn:
        lines += ["", f"⚠ {len(warn)} 条警告："] + [f"  [{w['where'] or w['code']}] {w['message']}" for w in warn]
    return "\n".join(lines)


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
    if s.get("children"):
        head.append("  子步骤: " + ", ".join(s["children"]))
    if s.get("backlinks"):
        head.append("  被引用: " + ", ".join(s["backlinks"]))
    if s.get("paths"):
        head.append("  外部产物（不在仓库里，只记了位置）:")
        for p in s["paths"]:
            head.append(f"    [{KIND_LABEL.get(p['kind'], p['kind'])}] {p['location']}"
                        + (f"  — {p['note']}" if p.get("note") else ""))
    if s.get("files"):
        head.append("  文件: " + ", ".join(f"{f['path']} ({f['size']}B)" for f in s["files"]))
        if any(Path(f["path"]).suffix.lower() in IMG_EXT for f in s["files"]):
            head.append("  ⓘ 图片内容你看不到，只能读正文里的图注。要看原图就取 "
                        "{base}/p/{项目}/files/{id}/{文件名}")
    return "\n".join(head) + "\n\n" + (s.get("body") or "（正文为空）")


# ---------------------------------------------------------------- 工具

TOOLS: list[dict[str, Any]] = [
    {
        "name": "trace_projects",
        "description": "列出所有科研项目及其步骤数与 done/wip/dead 分布。不确定该记到哪个项目时先调这个。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "trace_read",
        "description": (
            "读一个项目的步骤树；给了 step 就读那一步的全文（含到根的溯源链、子步骤、"
            "被引用、附件清单）。**开始任何新实验之前先调这个**，看看这条线走到哪了、"
            "有没有人试过。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "项目 slug，见 trace_projects"},
                "step": {"type": "string", "description": "步骤 id，如 004 或 004b。不给就返回整棵树"},
            },
            "required": ["project"],
        },
    },
    {
        "name": "trace_search",
        "description": "在标题、正文、标签里搜关键词。用来回答「之前是不是试过 X」「为什么放弃了 Y」。",
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
            },
            "required": ["project", "title"],
        },
    },
    {
        "name": "trace_update_step",
        "description": (
            "改一个已有步骤。只能改 status / title / body / date / commit / tags——"
            "id 和 parent 是只追加系统的地基，改不了（会返回 409）。\n"
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
                "add_paths": {"type": "array", "items": {"type": "string"},
                              "description": "追加（按位置去重），比整组替换安全。" + PATHS_DESC},
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
    f = be.forest(project)
    return _fmt_tree(f, f"项目 {project} · {len(f['steps'])} 步"
                        f"（● done / ○ wip / ▣ dead，缩进表示派生关系）")


def t_search(be, args) -> str:
    q = args["query"].strip().lower()
    if not q:
        raise ToolError("query 不能为空")
    slugs = [args["project"]] if args.get("project") else [p["slug"] for p in be.projects()]
    hits = []
    for slug in slugs:
        for s in be.forest(slug)["steps"]:
            hay = " ".join([s["id"], s["title"], s["body"], " ".join(s["tags"])]).lower()
            if q not in hay:
                continue
            where = s["body"].lower().find(q)
            snippet = ""
            if where >= 0:
                a = max(0, where - 60)
                snippet = ("…" if a else "") + s["body"][a:where + 140].replace("\n", " ") + "…"
            hits.append(f"{slug}/{s['id']}  [{s['status']}]  {s['title']}"
                        + (f"\n    {snippet}" if snippet else ""))
    if not hits:
        return f"没有搜到「{args['query']}」。"
    return f"搜到 {len(hits)} 条：\n\n" + "\n".join(hits)


_WHY = None


def _why_is_blank(body: str) -> bool:
    """「为什么」这一节是不是还空着。按小节内容判断，不靠正文长度这种粗糙启发式。"""
    global _WHY
    if _WHY is None:
        _WHY = re.compile(r"##\s*为什么\s*\n(.*?)(?=\n##\s|\Z)", re.S)
    m = _WHY.search(body)
    if not m:
        return True                     # 压根没有这一节
    text = m.group(1).strip()
    return not text or text.startswith(("（", "("))   # 空的，或者还是模板里的占位括号


def t_new_step(be, args) -> str:
    payload = {k: args[k] for k in ("parent", "title", "status", "body", "date", "commit", "key", "tags", "paths")
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
    return f"已创建 {args['project']}/{s['id']}  [{s['status']}]  {s['title']}" + tip


def t_update_step(be, args) -> str:
    project, sid = args["project"], args["step"]
    # 静默忽略比报错更糟：agent 会以为改成功了。这里和服务端的 409 保持一致。
    for locked in ("parent", "id"):
        if locked in args:
            raise ToolError(
                f"{locked} 不可修改。只追加是这套系统的地基——笔记里写的「见 003b」、"
                f"论文脚注里的引用能一直有效，靠的就是 id 和 parent 不变。"
            )
    patch = {k: args[k] for k in ("status", "title", "date", "commit", "tags", "paths", "add_paths")
             if k in args and args[k] is not None}
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
    return f"已更新 {project}/{s['id']}  [{s['status']}]  {s['title']}"


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


HANDLERS = {
    "trace_projects": t_projects,
    "trace_read": t_read,
    "trace_search": t_search,
    "trace_new_step": t_new_step,
    "trace_update_step": t_update_step,
    "trace_attach": t_attach,
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
SERVER_VERSION = "0.5.0"

# 收到客户端要的版本就原样回它（前提是我们认识），否则回我们最新的。
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "这是一棵只追加的科研步骤树。规矩："
    "① 动手之前先 trace_read 看这条线走到哪了；"
    "② 开跑之前就建 wip 步骤，跑完再改成 done/dead——等跑完才记的话，跑挂的那一步就不存在了；"
    "③ 正文必须写「为什么」，这是唯一无法自动生成的字段；"
    "④ 失败也要记，标 dead 并写清放弃理由，死胡同是这里最有价值的部分；"
    "⑤ 图必须给 caption，你看不到图，图注是它唯一的信息来源；"
    "⑥ 产物落在哪（超算路径、GitHub、对象存储）用 paths 记下来；"
    "⑦ id 和 parent 写下就不可改。"
)

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


def main() -> int:
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
