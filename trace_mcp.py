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

# 译文的 front-matter 里**只准**出现 title（项目笔记是 name）。下面这些是结构键，
# 它们在 note.md / project.md 里已经有了；写进译文就是双真相源，读侧一律忽略并报警告。
# 这里留一份字面量而不是 import trace_core，是因为远端后端那条路上这个文件可能是
# 单独拷过去的（只有 TRACE_URL，没有 trace_core）。tests/test_mcp.py 拿 core 的
# TR_STRUCT_KEYS 逐字核对这一份，漂移会当场被测出来。
TR_STRUCT_KEYS = ("id", "parent", "status", "date", "commit", "author",
                  "tags", "path", "repro", "key")

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
        return out

    def _guard(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except self.W.WriteError as e:
            raise ToolError(str(e)) from None

    def create_project(self, name):
        return self._guard(self.W.create_project, self.root, name).to_dict()

    def update_project(self, project, payload):
        add = None
        if payload.get("add_insight"):
            a = payload["add_insight"]
            add = (a.get("kind"), a.get("text", ""))
        return self._guard(self.W.update_project, self.root, project,
                           name=payload.get("name"), insights=payload.get("insights"),
                           add=add).to_dict()

    def create(self, project, payload):
        step, created = self._guard(
            self.W.create_step, self._sd(project),
            parent=payload.get("parent"), title=payload.get("title", ""),
            status=payload.get("status", "wip"), body=payload.get("body"),
            date=payload.get("date", ""), commit=payload.get("commit", ""),
            author=payload.get("author", ""), key=payload.get("key", ""),
            tags=payload.get("tags"), paths=payload.get("paths"),
            lang=payload.get("lang", ""),
        )
        d = step.to_dict()
        d["created"] = created
        return d

    def update(self, project, sid, patch):
        return self._guard(self.W.update_step, self._sd(project), sid, patch).to_dict()

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
        t = s.get("trace") or {}
        if t.get("self"):
            extra.append(t["self"] if t.get("chain") == t["self"] else f"{t['self']}→链{t['chain']}")
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
            "带上 step 让它指回证据来源，正文里会渲染成可跳转的链接。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "kind": {
                    "type": "string", "enum": ["idea", "works", "fails", "pitfall"],
                    "description": ("idea＝核心想法（还没验证的方向）；works＝有效（确认管用的）；"
                                    "fails＝无效（确认不管用的，和 works 一样重要）；"
                                    "pitfall＝坑（会反复咬人的问题，比如数据里的陷阱、环境的雷）"),
                },
                "text": {"type": "string", "description": "一句话说清楚。有数字就带上数字"},
                "step": {"type": "string", "description": "证据来自哪一步，如 002c。会渲染成可跳转链接"},
            },
            "required": ["project", "kind", "text"],
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
        "description": ("在标题、正文、标签、以及各语言的译文里搜关键词。"
                        "用来回答「之前是不是试过 X」「为什么放弃了 Y」。"
                        "英文词搜得到英文译文，命中落在译文里时结果行上会标出是哪个语言。"),
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
                "lang": {"type": "string", "description": "这份记录用什么语言写的（en / zh / ja …）。**声明**出来，别让读的一侧去猜——没有它，界面对没翻译的记录只能说「这是原文」，说不出是哪种语言的原文。"},
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
                "lang": {"type": "string", "description": "这份记录用什么语言写的（en / zh / ja …）。补一句声明，界面就不必对读者说「这是原文」而说不出是哪种语言；空串是撤回声明。"},
                "add_paths": {"type": "array", "items": {"type": "string"},
                              "description": "追加（按位置去重），比整组替换安全。" + PATHS_DESC},
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
    f = be.forest(project)
    out = _fmt_tree(f, f"项目 {project} · {len(f['steps'])} 步"
                       f"（● done / ○ wip / ▣ dead，缩进表示派生关系）")
    # 项目级的洞察放最前面：它是这个项目里已经沉淀下来的判断，
    # 比逐步去读更快让人（和你）进入状态。
    info = next((p for p in be.projects() if p["slug"] == project), None)
    if info and (info.get("body") or "").strip():
        out = "【本项目已沉淀的洞察】\n" + info["body"].strip() + "\n\n" + out
    return out


def t_search(be, args) -> str:
    """id / 标题 / 正文 / 标签 / **各语言的译文**里搜。

    译文也搜，理由和 REST 那侧的 search_hits 一字不差：这套系统的底线是
    「删掉全部程序，grep -r 还能回答『为什么放弃了 X』」，双语之后英文的 grep
    也要能回答同一个问题。`grep -r abandoned` 命中 note.en.md 而 trace_search
    命中不了的话，agent 会得到「没搜到」，而它会把这四个字读成「没试过」，
    然后重跑一条已经走死的路。命中落在译文里时结果行上会标出是哪个语言。
    """
    q = args["query"].strip().lower()
    if not q:
        raise ToolError("query 不能为空")
    slugs = [args["project"]] if args.get("project") else [p["slug"] for p in be.projects()]
    hits = []
    for slug in slugs:
        for s in be.forest(slug)["steps"]:
            tr = s.get("tr") or {}
            hay = " ".join([s["id"], s["title"], s["body"], " ".join(s["tags"])]).lower()
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


def t_insight(be, args) -> str:
    text = args["text"].strip()
    if args.get("step"):
        text = f"{text} —— [[{args['step'].strip()}]]"
    p = be.update_project(args["project"], {"add_insight": {"kind": args["kind"], "text": text}})
    label = {"idea": "核心想法", "works": "有效", "fails": "无效", "pitfall": "坑"}[args["kind"]]
    return f"已记入 {p['slug']} 的「{label}」：{text}"


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

    payload = {k: args[k] for k in ("parent", "title", "status", "body", "date", "commit", "key", "tags", "paths", "lang")
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
    patch = {k: args[k] for k in ("status", "title", "date", "commit", "tags", "paths", "add_paths", "lang")
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


HANDLERS = {
    "trace_projects": t_projects,
    "trace_new_project": t_new_project,
    "trace_insight": t_insight,
    "trace_delete_step": t_delete_step,
    "trace_read": t_read,
    "trace_search": t_search,
    "trace_new_step": t_new_step,
    "trace_update_step": t_update_step,
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
SERVER_VERSION = "1.3.0"

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
    "⑤ 外部产物用 paths 记成 `位置 | 说明`，说明里带上校验和与大小；"
    "GB 级的东西不要传进来，只记它在哪 —— 这是溯源的一半。"
    "⑥ 可溯源性等级：L0 不可溯源（缺「为什么」/「做了什么」，或有图没图注，或 done/dead 没结论）；"
    "L1 可读；L2 可定位（L1 + 记了 commit + 记了产物位置）；L3 可重跑（repro: runnable）；"
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
    "id / parent / status / date / commit / author / tags / path / repro / key 写进去会被"
    "忽略并报一条警告，因为那些在原文里已经有了，写两份就是双真相源。"
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
    "⑥ 产物落在哪（超算路径、GitHub、对象存储）用 paths 记下来；"
    "⑦ id 和 parent 写下就不可改；"
    "⑧ 要双语就用 trace_translate 单独补一份译文（`note.en.md`），它碰不到原文——"
    "建完步骤马上调就是立刻翻译，过几天再调就是延迟翻译，同一条路径；"
    "隔了几天先用 trace_untranslated 看还欠哪些。\n\n"
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
