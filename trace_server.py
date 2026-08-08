"""trace_server — 文件的遥控器，不是数据库。

进程内不持有任何独占状态：所有写入立刻落成 note.md，所有读取都是对目录的
一次纯函数编译。因此重启服务器、手工 vim 一个 note.md、git pull 一批新步骤，
效果完全等价——这是把"实时交互"叠加到"纯文件即数据库"上而不破坏后者的关键。

SSE 推的是"版本变了，重新编译"信号，不是增量 patch（保持 P3：编译而非同步）。

路径布局：
    projects/<slug>/project.md
    projects/<slug>/steps/<id>_<slug>/note.md
URL：
    /t/<space>/                 项目索引
    /t/<space>/p/<slug>/        某个项目
"""

from __future__ import annotations

import asyncio
import errno
import hmac
import json
import mimetypes
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse

import trace_core as core
import trace_write as W
from trace_git import GitSync

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
CONFIG_PATH = ROOT / "config.json"
POLL_SECONDS = 2.0
SEARCH_LIMIT = 50          # 默认返回多少条命中
SEARCH_LIMIT_MAX = 500     # 上限。跨项目搜索会把所有 forest 拉进内存，别让一次请求无限大


class Unauthorized(Exception):
    """令牌不对。

    刻意**不用** PermissionError：它是 OSError 的子类，而文件系统层随时会抛
    PermissionError（Windows 上文件被别的程序占用、目录只读、NAS 掉线）。
    以前这两件事共用一个 exception_handler，于是「磁盘写不进去」会被报成
    「需要写入令牌」——agent 拿到 401 会去重新找令牌，而真正的问题在磁盘上。
    """


DEFAULT_CONFIG: dict[str, Any] = {
    "title": "科研溯源",
    "space": "",
    "token": "",
    "data_dir": ".",
    "git": {"enabled": False, "remote": "origin", "branch": "main", "debounce": 45},
}


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.is_file():
        user = json.loads(path.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    cfg.pop("steps_dir", None)  # 旧字段，已被 projects/ 布局取代
    return cfg


def make_config(title: str = "科研溯源") -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["title"] = title
    cfg["space"] = secrets.token_urlsafe(16)[:22]
    cfg["token"] = secrets.token_urlsafe(32)
    return cfg


# ---------------------------------------------------------------- 磁盘错误


def describe_oserror(exc: OSError) -> tuple[int, str, str]:
    """把文件系统层的 OSError 翻成（HTTP 状态码, 分类, 中文说明）。

    为什么要单独翻一遍：这些异常的 str() 是英文 errno 加**服务器上的绝对路径**。
    直接漏成 500 有两个后果：agent 没法判断该改名重试还是该放弃（500 读作
    "服务器坏了"，正确的读法往往是"你给的文件名太长"），而返回体里的绝对路径
    等于把磁盘布局送给了任何拿得到读接口的人。

    每条说明都必须回答 agent 唯一真正关心的那个问题——**这次写入到底发生了没有**。
    答案统一是"没有"：记录本体（note.md / project.md）和附件都走 write_atomic
    （同目录临时文件 → fsync → os.replace），异常能漏到这一层，就说明 os.replace
    还没执行，目标文件仍是原来那一份。唯一的例外是新建步骤时可能留下一个空目录，
    它没有 note.md，扫描时会被静默跳过，不影响任何记录。
    """
    err = exc.errno
    win = getattr(exc, "winerror", None)
    raw = str(getattr(exc, "filename", "") or "")
    # 只回**文件名**，不回目录：文件名是客户端自己给的，回给它有用；路径是服务器的事。
    name = Path(raw).name if raw else ""
    tail = f"（{name}）" if name else ""

    if err == errno.ENAMETOOLONG or win == 206 or (err == errno.ENOENT and len(raw) >= 240):
        return 400, "name_too_long", (
            f"文件名或路径太长{tail}，操作系统直接拒绝了。Windows 的整条路径上限是 260 个字符，"
            "Linux 的单个文件名上限是 255 **字节**（一个中文占 3 字节，86 个中文就到顶）。"
            "换个短名字重试即可——这次写入没有发生，磁盘上原来的内容一个字节都没变。")
    if err in (errno.ENOSPC, errno.EDQUOT):
        return 507, "disk_full", (
            "服务器磁盘满了（或超出了配额），写不进去。这次写入没有发生，原来的内容还在。"
            "先去清出空间，再原样重发这个请求。")
    if win in (32, 33) or err == errno.EBUSY:
        return 409, "locked", (
            f"这个文件正被服务器上的另一个程序占用{tail}，写不进去。"
            "Windows 上常见的元凶是杀毒软件、同步盘（OneDrive/坚果云）、或者一个开着这份文件的编辑器。"
            "这次写入没有发生，原来的内容还在；等几秒重试通常就好了。")
    if err in (errno.EACCES, errno.EPERM, errno.EROFS):
        return 403, "permission", (
            f"服务器对这个文件没有写权限，或者所在的文件系统是只读的{tail}。"
            "这次写入没有发生，原来的内容还在。这属于部署问题（数据仓的属主/权限），"
            "重试解决不了，要去服务器上看目录权限。")
    if err == errno.ENOENT:
        return 404, "missing", (
            f"服务器上找不到这个文件或它所在的目录{tail}——多半是刚被别的进程删掉或移走了。"
            "这次写入没有发生。重新读一遍再决定要不要重试。")
    if err in (errno.EIO, errno.ENXIO, errno.ENODEV, errno.ESTALE):
        return 503, "unavailable", (
            "服务器的存储暂时不可用（磁盘 I/O 错误，或者挂载的网络盘掉线了）。"
            "这次写入没有发生。这是服务器侧的故障，稍后重试。")
    return 500, "io_error", (
        f"文件系统报了一个没预料到的错误{tail}（errno={err}）。这次写入没有落盘，"
        "原来的内容还在。详细信息只写进了服务端日志——如果反复出现，去看服务器日志。")


# ---------------------------------------------------------------- 状态


class State:
    """唯一可变状态：版本号 + 每个项目的编译缓存。两者都可以随时丢弃重算。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.version = 0
        self.sigs: dict[str, str] = {}
        self._cache: dict[str, tuple[str, dict[str, Any]]] = {}
        # 每个项目一把编译锁：同一个项目并发请求时只编译一次，后来者等着复用结果。
        # 没有它的话，n 个并发请求会开 n 个线程各算一遍同一棵树（编译是纯函数，
        # 结果一样，但白烧 n 倍 CPU，而且最后一个写进缓存的可能是最旧的那次）。
        self._locks: dict[str, asyncio.Lock] = {}
        self.refresh()

    def _sig(self, slug: str) -> str:
        # 传的是 steps 目录；同级的 project.md 由 core.signature 自己带上，
        # 所以改洞察 / 改项目显示名同样会让指纹变、version 涨、SSE 推出去。
        return core.signature(core.steps_dir_of(self.root, slug))

    def refresh_changed(self) -> list[str]:
        """重扫指纹，返回**变了的项目 slug**（新增和消失的也算）。

        同步函数，磁盘 I/O 不小，调用方负责放进线程。
        返回变更列表而不只是 bool，是为了让 git 自动同步能由**文件变化**驱动：
        vim 直接改一个 note.md、本机 MCP 以 role=server 写一步，都不经过 HTTP，
        只有这里看得见。
        """
        sigs = {p.slug: self._sig(p.slug) for p in core.scan_projects(self.root)}
        if sigs == self.sigs:
            return []
        changed = sorted(
            set(sigs) ^ set(self.sigs)
            | {s for s in sigs if s in self.sigs and sigs[s] != self.sigs[s]}
        )
        self.sigs = sigs
        self.version += 1
        return changed

    def refresh(self) -> bool:
        return bool(self.refresh_changed())

    def _compile(self, slug: str) -> tuple[str, dict[str, Any]]:
        """在线程里跑的那一半。先取指纹再编译，顺序是有讲究的：

        指纹在前，万一编译期间有人写盘，缓存里存的就是（旧指纹, 新产物）——
        下一次 refresh 会发现指纹变了、重编一次，最多多算一遍。
        反过来（指纹在后）会存成（新指纹, 旧产物），那份旧产物会一直被当成最新的。
        """
        sig = self.sigs.get(slug) or self._sig(slug)
        return sig, core.compile_forest(core.steps_dir_of(self.root, slug))

    def _stamp(self, forest: dict[str, Any], slug: str) -> dict[str, Any]:
        out = dict(forest)
        out["version"] = self.version
        out["project"] = slug
        return out

    async def forest(self, slug: str) -> dict[str, Any]:
        """编译一个项目的森林。**编译本身跑在线程里**。

        compile_forest 对深链项目是几百毫秒到几秒的纯 CPU + 磁盘 I/O，
        直接在 async 函数里同步调用会把整个事件循环焊死：实测 1000 步的项目
        一次 forest 请求会让同时发出的 /healthz 等十几秒，SSE 心跳、别人的读写
        全部停摆，反向代理的探活也跟着超时。
        """
        hit = self._cache.get(slug)
        sig = self.sigs.get(slug, "")
        if sig and hit is not None and hit[0] == sig:
            return self._stamp(hit[1], slug)          # 缓存命中：连线程都不用起
        lock = self._locks.get(slug)
        if lock is None:
            lock = self._locks[slug] = asyncio.Lock()
        async with lock:
            hit = self._cache.get(slug)
            sig = self.sigs.get(slug) or await asyncio.to_thread(self._sig, slug)
            if hit is None or hit[0] != sig:          # 等锁期间别人可能已经编完了
                hit = await asyncio.to_thread(self._compile, slug)
                self._cache[slug] = hit
            return self._stamp(hit[1], slug)

    async def projects(self) -> list[dict[str, Any]]:
        out = []
        for p in await asyncio.to_thread(core.scan_projects, self.root):
            f = await self.forest(p.slug)
            counts = {"wip": 0, "done": 0, "dead": 0}
            for s in f["steps"]:
                counts[s["status"]] = counts.get(s["status"], 0) + 1
            d = p.to_dict()
            d["steps"] = len(f["steps"])
            d["counts"] = counts
            d["warnings"] = len(f["warnings"])
            d["latest"] = max((s["date"] for s in f["steps"] if s["date"]), default="")
            out.append(d)
        return out


# ---------------------------------------------------------------- 搜索


SNIPPET_BEFORE = 60
SNIPPET_AFTER = 140


def _snippet(body: str, at: int) -> str:
    a = max(0, at - SNIPPET_BEFORE)
    b = min(len(body), at + SNIPPET_AFTER)
    return (("…" if a else "") + body[a:b].replace("\n", " ") + ("…" if b < len(body) else "")).strip()


def search_hits(forests: list[tuple[str, str, dict[str, Any]]], query: str,
                limit: int) -> tuple[list[dict[str, Any]], int]:
    """在若干个已经编译好的森林里做子串搜索。纯函数，方便整个丢进线程跑。

    判定规则和 MCP 的 trace_search 一致（id / 标题 / 正文 / 标签，大小写不敏感），
    人和 agent 搜同一个词必须搜到同一批东西——FORMAT.md 第 0 节的「信息对等」。
    返回（这一页的命中, 命中总数）：总数照实报，这样前端能说"还有 200 条没显示"，
    而不是让人以为搜完了。
    """
    q = query.strip().lower()
    hits: list[dict[str, Any]] = []
    total = 0
    if not q:
        return hits, total
    for slug, name, forest in forests:
        for s in forest["steps"]:
            body = s.get("body") or ""
            tags = s.get("tags") or []
            where = []
            if q in (s.get("title") or "").lower():
                where.append("title")
            at = body.lower().find(q)
            if at >= 0:
                where.append("body")
            if any(q in t.lower() for t in tags):
                where.append("tags")
            if q in s["id"].lower():
                where.append("id")
            if not where:
                continue
            total += 1
            if len(hits) >= limit:
                continue
            hits.append({
                "project": slug,
                "project_name": name,
                "id": s["id"],
                "title": s.get("title", ""),
                "status": s.get("status", ""),
                "date": s.get("date", ""),
                "tags": list(tags),
                "where": where,
                "snippet": _snippet(body, at) if at >= 0 else "",
            })
    return hits, total


# ---------------------------------------------------------------- 应用


def create_app(config: dict[str, Any] | None = None) -> FastAPI:
    cfg = config or load_config()
    data_root = (ROOT / cfg.get("data_dir", ".")).resolve()
    migrated = core.ensure_layout(data_root)

    space = (cfg.get("space") or "").strip("/")
    base = f"/t/{space}" if space else ""
    token = cfg.get("token") or ""

    state = State(data_root)
    # 同步的是**数据目录**而不是代码目录。data_dir 指向别处时（推荐做法：
    # 代码仓公开、数据仓私有），要 commit 的是那个数据仓。
    git = GitSync(
        data_root,
        enabled=bool(cfg["git"].get("enabled")),
        remote=cfg["git"].get("remote", "origin"),
        branch=cfg["git"].get("branch", "main"),
        debounce=float(cfg["git"].get("debounce", 45)),
    )

    async def poller() -> None:
        while True:
            try:
                await asyncio.sleep(POLL_SECONDS)
                changed = await asyncio.to_thread(state.refresh_changed)
                if changed:
                    # 同步由**文件变化**驱动，不由 API 调用驱动——这才是 P1
                    # 「文件才是真相」的立场。以前只有 HTTP 写路由会 touch()，
                    # 于是同一台机器上 vim 改的 note.md、role=server 的本地 MCP、
                    # trace_cli.py new/rm 写进去的步骤，一辈子进不了 git，
                    # 而 deploy/README 把 git push 当成换机器和灾难恢复的唯一依据。
                    git.touch([f"{slug}（磁盘变化）" for slug in changed])
            except asyncio.CancelledError:
                raise
            except Exception:  # 轮询失败不该拖垮服务
                pass

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if migrated:
            print(f"[trace] 已把旧的 steps/ 迁移到 projects/{migrated}/steps/")
        # 起服务时就把「数据仓没配 git 身份 / 没有那个 remote」查出来，
        # 而不是等 45 秒 debounce 之后在没人看着的时刻第一次 push 失败。
        await asyncio.to_thread(git.preflight)
        task = asyncio.create_task(poller())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="trace", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.cfg = cfg
    app.state.core = state
    app.state.git = git
    app.state.base = base
    app.state.data_root = data_root

    # ---- 通用 --------------------------------------------------------

    @app.middleware("http")
    async def no_index(request: Request, call_next):
        resp = await call_next(request)
        # "读公开但 URL 不可猜"只有在爬虫永远看不到这个路径时才成立
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return resp

    @app.exception_handler(W.WriteError)
    async def _write_error(_: Request, exc: W.WriteError):
        code = 409 if isinstance(exc, W.Conflict) else 404 if isinstance(exc, W.NotFound) else 400
        return JSONResponse({"error": str(exc)}, status_code=code)

    @app.exception_handler(OSError)
    async def _disk_error(_: Request, exc: OSError):
        """磁盘满、只读目录、文件被别的程序占用、路径过长——统统别漏成 500。

        注意这个处理器**必须**和令牌检查用两个不同的异常类型：PermissionError 是
        OSError 的子类，以前 require_token 抛的就是它，于是 Starlette 按 MRO 找
        处理器时，任何一处文件系统的 PermissionError 都会被报成 401「需要写入令牌」。
        """
        code, kind, message = describe_oserror(exc)
        print(f"[trace] 文件系统错误 {kind}: {exc!r}")   # 原文（含绝对路径）只留在服务端
        return JSONResponse({"error": message, "kind": kind, "written": False}, status_code=code)

    def has_token(request: Request) -> bool:
        if not token:
            return True  # 未配置 token（本地开发）时不拦
        auth = request.headers.get("authorization", "")
        supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        supplied = supplied or request.headers.get("x-trace-token", "").strip()
        return bool(supplied and hmac.compare_digest(supplied, token))

    def require_token(request: Request) -> None:
        if not has_token(request):
            raise Unauthorized

    @app.exception_handler(Unauthorized)
    async def _forbidden(_: Request, __: Unauthorized):
        return JSONResponse({"error": "需要写入令牌：Authorization: Bearer <token>"}, status_code=401)

    async def body_json(request: Request) -> dict[str, Any]:
        """畸形请求体要给 agent 一条能读懂的 400，而不是一段 500 traceback。"""
        raw = await request.body()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            raise W.WriteError("请求体不是合法的 UTF-8（注意 Windows 终端默认可能是 GBK）")
        except json.JSONDecodeError as exc:
            raise W.WriteError(f"请求体不是合法的 JSON：{exc}")
        if not isinstance(payload, dict):
            raise W.WriteError("请求体必须是 JSON 对象")
        return payload

    def sd(project: str) -> Path:
        return W.resolve_project(data_root, project)

    async def touched(ids: list[str]) -> None:
        await asyncio.to_thread(state.refresh)
        git.touch(ids)

    def with_digest(steps: Path, step: core.Step) -> dict[str, Any]:
        """把刚写完的步骤连**新的**摘要指纹一起回给调用方。

        step.digest 是 load() 时算的，也就是**改之前**那一份 note.md 的指纹。
        直接回给客户端会很坑：客户端拿它当下一次 expect 传回来，必然对不上，
        于是自己刚写完的东西被判成冲突。所以这里重新读一次磁盘。
        """
        out = step.to_dict()
        out["digest"] = W.digest_of(steps / step.dirname / core.NOTE_NAME)
        return out

    def read_expect(request: Request, payload: dict[str, Any]) -> str:
        """取乐观并发控制的基线指纹：请求体的 expect 优先，其次 If-Match 头。

        两条都收是因为两类客户端不一样：网页发 JSON 顺手带一个字段最省事，
        而 curl / agent 更习惯 HTTP 自己的 If-Match。不传就是不检查——
        agent 那种"读一段、追加一段"的写入不该被挡（它本来就不覆盖别人的内容）。
        """
        got = str(payload.pop("expect", "") or "").strip()
        if got:
            return got
        raw = request.headers.get("if-match", "").strip()
        if raw.lower().startswith("w/"):
            raw = raw[2:].strip()
        return raw.strip('"')

    def page(project: str) -> Response:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        html = (
            html.replace("__ASSET__", f"{base}/static/")
            .replace("__BASE__", base)
            .replace("__TITLE__", cfg.get("title", "科研溯源"))
            .replace("__MODE__", "server")
            .replace("__PROJECT__", project)
            .replace("__DATA__", "")
            .replace("__PROJECTS__", "")
        )
        return Response(html, media_type="text/html; charset=utf-8")

    # ---- 根路径 ------------------------------------------------------

    @app.get("/robots.txt", include_in_schema=False)
    async def robots() -> PlainTextResponse:
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    # ---- 页面与静态资源 ----------------------------------------------

    @app.get(base + "/", include_in_schema=False)
    async def index(request: Request) -> Response:
        """只有一个项目时直接跳进去——省掉一个只有一张卡片的首页。

        但那个跳转必须有旁路：没有它的话，从项目页点「所有项目 ▸」会被立刻弹回来，
        首页上的跨项目搜索和项目列表就成了永远够不着的东西。`?all=1` 就是旁路。
        """
        ps = core.scan_projects(data_root)
        if len(ps) == 1 and "all" not in request.query_params:
            return RedirectResponse(f"{base}/p/{ps[0].slug}/", status_code=302)
        return page("")

    @app.get(base + "/p/{project}/", include_in_schema=False)
    async def project_page(project: str) -> Response:
        return page(project)

    @app.get(base + "/static/{name}", include_in_schema=False)
    async def static(name: str) -> Response:
        if "/" in name or "\\" in name or name.startswith("."):
            return PlainTextResponse("bad path", status_code=400)
        p = WEB / name
        if not p.is_file():
            return PlainTextResponse("not found", status_code=404)
        media, _ = mimetypes.guess_type(p.name)
        return Response(p.read_bytes(), media_type=(media or "application/octet-stream"))

    @app.get(base + "/p/{project}/files/{sid}/{relpath:path}", include_in_schema=False)
    async def files(project: str, sid: str, relpath: str) -> Response:
        steps = sd(project)
        target = W.resolve_attachment(steps, W.load(steps), sid, relpath)
        if not target.is_file():
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(target)

    # ---- 项目 --------------------------------------------------------

    @app.get(base + "/api/projects")
    async def api_projects() -> JSONResponse:
        return JSONResponse({"projects": await state.projects(), "version": state.version})

    @app.post(base + "/api/projects")
    async def api_create_project(request: Request) -> JSONResponse:
        require_token(request)
        payload = await body_json(request)
        p = W.create_project(data_root, payload.get("name", ""))
        await touched([f"project:{p.slug}"])
        return JSONResponse(p.to_dict(), status_code=201)

    @app.patch(base + "/api/projects/{project}")
    async def api_update_project(project: str, request: Request) -> JSONResponse:
        """改显示名和/或洞察。slug 不动——它是 URL 的一部分。"""
        require_token(request)
        payload = await body_json(request)
        add = None
        if payload.get("add_insight"):
            a = payload["add_insight"]
            if not isinstance(a, dict) or not a.get("kind"):
                raise W.WriteError('add_insight 要形如 {"kind": "idea", "text": "…"}')
            add = (a["kind"], a.get("text", ""))
        p = W.update_project(data_root, project,
                             name=payload.get("name"),
                             insights=payload.get("insights"),
                             add=add)
        await touched([f"project:{p.slug}"])
        return JSONResponse(p.to_dict())

    # ---- 读 API ------------------------------------------------------

    @app.get(base + "/api/p/{project}/forest")
    async def api_forest(project: str) -> JSONResponse:
        sd(project)
        return JSONResponse(await state.forest(project))

    @app.get(base + "/api/p/{project}/steps/{sid}")
    async def api_step(project: str, sid: str) -> JSONResponse:
        sd(project)
        forest = await state.forest(project)
        idx = {s["id"]: s for s in forest["steps"]}
        if sid not in idx:
            return JSONResponse({"error": f"步骤 {sid} 不存在"}, status_code=404)
        out = dict(idx[sid])
        chain, cur, seen = [], sid, set()
        while cur and cur in idx and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = idx[cur]["parent"]
        out["lineage"] = list(reversed(chain))
        return JSONResponse(out)

    @app.get(base + "/api/search")
    async def api_search(request: Request, q: str = "", query: str = "", project: str = "",
                         limit: int = SEARCH_LIMIT) -> JSONResponse:
        """跨项目搜索。不给 project 就搜全部项目——和 MCP 的 trace_search 一样。

        为什么必须有这个端点：网页那侧的搜索是在**当前项目**已下载的 forest 上做的，
        项目索引页压根搜不了。于是「之前好像在某个课题里试过 X，最后放弃了」这个
        问题，agent 一次调用就答得出，人却要一个项目一个项目点进去各搜一遍。
        读接口一律公开，和 forest 一致。

        关键词两个名字都收：q（前端顺手）和 query（MCP 那边的参数名就叫这个，
        免得照着 trace_search 抄的人第一次调用拿到空结果还以为是真没记过）。
        """
        q = q or query
        limit = max(1, min(limit, SEARCH_LIMIT_MAX))
        if project:
            sd(project)                       # 项目不存在就 404，别静默返回空结果
            names = {p.slug: p.name for p in await asyncio.to_thread(core.scan_projects, data_root)}
            slugs = [(project, names.get(project, project))]
        else:
            slugs = [(p.slug, p.name) for p in await asyncio.to_thread(core.scan_projects, data_root)]
        forests = [(slug, name, await state.forest(slug)) for slug, name in slugs]
        hits, total = await asyncio.to_thread(search_hits, forests, q, limit)
        return JSONResponse({
            "query": q,
            "projects": [slug for slug, _ in slugs],
            "hits": hits,
            "total": total,
            "truncated": total > len(hits),
            "limit": limit,
            "version": state.version,
        })

    @app.get(base + "/api/git")
    async def api_git(request: Request) -> JSONResponse:
        """最近一次 git 自动同步的结果。

        无声失败等于用户以为自己有备份而其实没有，所以这条状态必须查得到。
        带写入令牌时多给一个 detail（git 的原样输出），它可能含数据仓路径和
        远端地址，不能对着公网无条件吐出去。
        """
        return JSONResponse(git.status(detail=has_token(request)))

    @app.get(base + "/api/status")
    async def api_status(request: Request) -> JSONResponse:
        ps = await state.projects()
        return JSONResponse(
            {
                "title": cfg.get("title"),
                "version": state.version,
                "projects": len(ps),
                "steps": sum(p["steps"] for p in ps),
                # 以前直接回 git.last，里面的 detail 是 git 原文（含服务器绝对路径、
                # 远端地址），而这个端点不要令牌。改成分类过的摘要视图。
                "git": git.status(detail=has_token(request)),
                "write_protected": bool(token),
            }
        )

    @app.get(base + "/api/events", include_in_schema=False)
    async def events() -> StreamingResponse:
        async def gen():
            last = -1
            while True:
                v = state.version
                if v != last:
                    last = v
                    yield f"data: {json.dumps({'version': v})}\n\n"
                else:
                    yield ": ping\n\n"
                await asyncio.sleep(1.5)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # ---- 写 API ------------------------------------------------------

    @app.post(base + "/api/p/{project}/steps")
    async def api_create(project: str, request: Request) -> JSONResponse:
        require_token(request)
        payload = await body_json(request)
        step, created = W.create_step(
            sd(project),
            parent=payload.get("parent"),
            title=payload.get("title", ""),
            status=payload.get("status", core.DEFAULT_STATUS),
            body=payload.get("body"),
            date=payload.get("date", ""),
            commit=payload.get("commit", ""),
            author=payload.get("author", ""),
            key=payload.get("key", ""),
            tags=payload.get("tags"),
            paths=payload.get("paths"),
        )
        if created:
            await touched([f"{project}/{step.id}"])
        out = with_digest(sd(project), step)
        out["created"] = created
        return JSONResponse(out, status_code=201 if created else 200)

    @app.patch(base + "/api/p/{project}/steps/{sid}")
    async def api_update(project: str, sid: str, request: Request) -> JSONResponse:
        """改一步。带 expect / If-Match 时做乐观并发控制。

        没有它的时候，这是个静默毁数据的洞：你在网页里编辑 004，编辑期间
        （app.js 明确规定编辑时不刷新）你自己的 agent 跑完实验往 004 里追加了
        「结果」和产物路径，你一按保存，那份**打开编辑器那一刻**的旧快照整组盖回去，
        agent 写的东西无声消失，两次请求都是 200。

        409 的响应体里必须带上服务器当前的内容：只说一句「冲突了」，客户端除了
        重新拉一遍别无选择，而人真正需要的是把两边摆在一起看——尤其是被冲掉的
        那一段往往是刚跑完的实验结果，再也跑不出来了。
        """
        require_token(request)
        payload = await body_json(request)
        expect = read_expect(request, payload)
        steps = sd(project)
        try:
            step = W.update_step(steps, sid, payload, expect=expect)
        except W.Conflict as exc:
            body: dict[str, Any] = {"error": str(exc), "expected": expect}
            cur = W.load(steps).get(sid)
            if cur is not None:
                body["current"] = cur.to_dict()      # scan() 填的 digest 就是磁盘上那一份
                body["digest"] = cur.digest
            return JSONResponse(body, status_code=409)
        await touched([f"{project}/{sid}"])
        return JSONResponse(with_digest(steps, step))

    @app.put(base + "/api/p/{project}/steps/{sid}/files/{relpath:path}")
    async def api_attach(project: str, sid: str, relpath: str, request: Request) -> JSONResponse:
        require_token(request)
        info = W.attach_file(sd(project), sid, relpath, await request.body())
        await touched([f"{project}/{sid}"])
        return JSONResponse(info, status_code=201)

    @app.post(base + "/api/p/{project}/steps/{sid}/files")
    async def api_attach_auto(project: str, sid: str, request: Request) -> JSONResponse:
        """服务端定名的上传，供网页粘贴截图 / 拖文件用。"""
        require_token(request)
        # HTTP 头只能是 latin-1，中文文件名由前端 encodeURIComponent 编过
        raw_name = unquote(request.headers.get("x-filename", ""))
        info = W.attach_auto(
            sd(project), sid, await request.body(),
            filename=raw_name,
            mime=request.headers.get("content-type", ""),
        )
        await touched([f"{project}/{sid}"])
        return JSONResponse(info, status_code=201)

    @app.delete(base + "/api/p/{project}/steps/{sid}/files/{relpath:path}")
    async def api_detach(project: str, sid: str, relpath: str, request: Request) -> JSONResponse:
        require_token(request)
        W.delete_file(sd(project), sid, relpath)
        await touched([f"{project}/{sid}"])
        return JSONResponse({"ok": True})

    @app.delete(base + "/api/p/{project}/steps/{sid}")
    async def api_delete_step(project: str, sid: str, request: Request) -> JSONResponse:
        """真删一个步骤。P2 的有意例外，见 trace_write.delete_step 的说明。"""
        require_token(request)
        payload = await body_json(request) if await request.body() else {}
        info = W.delete_step(sd(project), sid, payload.get("reason", ""),
                             by=payload.get("by", ""), date=payload.get("date", ""))
        await touched([f"{project}/{sid} 已删除"])
        return JSONResponse(info)

    @app.post(base + "/api/sync")
    async def api_sync(request: Request) -> JSONResponse:
        require_token(request)
        await asyncio.to_thread(git.commit_now, None)
        # 回 status() 而不是 commit_now 的原始返回：形状和 GET /api/git 一样，
        # 调用方（doctor、运维脚本）只需要认一种结构。
        return JSONResponse(git.status(detail=True))

    return app


app = create_app() if CONFIG_PATH.is_file() else None
