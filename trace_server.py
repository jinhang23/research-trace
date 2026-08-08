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


# ---------------------------------------------------------------- 状态


class State:
    """唯一可变状态：版本号 + 每个项目的编译缓存。两者都可以随时丢弃重算。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.version = 0
        self.sigs: dict[str, str] = {}
        self._cache: dict[str, tuple[str, dict[str, Any]]] = {}
        self.refresh()

    def _sig(self, slug: str) -> str:
        return core.signature(core.steps_dir_of(self.root, slug))

    def refresh(self) -> bool:
        sigs = {p.slug: self._sig(p.slug) for p in core.scan_projects(self.root)}
        if sigs != self.sigs:
            self.sigs = sigs
            self.version += 1
            return True
        return False

    def forest(self, slug: str) -> dict[str, Any]:
        sig = self.sigs.get(slug) or self._sig(slug)
        hit = self._cache.get(slug)
        if hit is None or hit[0] != sig:
            hit = (sig, core.compile_forest(core.steps_dir_of(self.root, slug)))
            self._cache[slug] = hit
        out = dict(hit[1])
        out["version"] = self.version
        out["project"] = slug
        return out

    def projects(self) -> list[dict[str, Any]]:
        out = []
        for p in core.scan_projects(self.root):
            f = self.forest(p.slug)
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
                await asyncio.to_thread(state.refresh)
            except asyncio.CancelledError:
                raise
            except Exception:  # 轮询失败不该拖垮服务
                pass

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if migrated:
            print(f"[trace] 已把旧的 steps/ 迁移到 projects/{migrated}/steps/")
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

    def require_token(request: Request) -> None:
        if not token:
            return  # 未配置 token（本地开发）时不拦
        auth = request.headers.get("authorization", "")
        supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        supplied = supplied or request.headers.get("x-trace-token", "").strip()
        if not (supplied and hmac.compare_digest(supplied, token)):
            raise PermissionError

    @app.exception_handler(PermissionError)
    async def _forbidden(_: Request, __: PermissionError):
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

    def touched(ids: list[str]) -> None:
        state.refresh()
        git.touch(ids)

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
    async def index() -> Response:
        ps = core.scan_projects(data_root)
        if len(ps) == 1:
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
        return JSONResponse({"projects": state.projects(), "version": state.version})

    @app.post(base + "/api/projects")
    async def api_create_project(request: Request) -> JSONResponse:
        require_token(request)
        payload = await body_json(request)
        p = W.create_project(data_root, payload.get("name", ""))
        touched([f"project:{p.slug}"])
        return JSONResponse(p.to_dict(), status_code=201)

    @app.patch(base + "/api/projects/{project}")
    async def api_rename_project(project: str, request: Request) -> JSONResponse:
        require_token(request)
        payload = await body_json(request)
        p = W.rename_project(data_root, project, payload.get("name", ""))
        touched([f"project:{p.slug}"])
        return JSONResponse(p.to_dict())

    # ---- 读 API ------------------------------------------------------

    @app.get(base + "/api/p/{project}/forest")
    async def api_forest(project: str) -> JSONResponse:
        sd(project)
        return JSONResponse(state.forest(project))

    @app.get(base + "/api/p/{project}/steps/{sid}")
    async def api_step(project: str, sid: str) -> JSONResponse:
        sd(project)
        forest = state.forest(project)
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

    @app.get(base + "/api/status")
    async def api_status() -> JSONResponse:
        ps = state.projects()
        return JSONResponse(
            {
                "title": cfg.get("title"),
                "version": state.version,
                "projects": len(ps),
                "steps": sum(p["steps"] for p in ps),
                "git": git.last,
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
        )
        if created:
            touched([f"{project}/{step.id}"])
        out = step.to_dict()
        out["created"] = created
        return JSONResponse(out, status_code=201 if created else 200)

    @app.patch(base + "/api/p/{project}/steps/{sid}")
    async def api_update(project: str, sid: str, request: Request) -> JSONResponse:
        require_token(request)
        payload = await body_json(request)
        step = W.update_step(sd(project), sid, payload)
        touched([f"{project}/{sid}"])
        return JSONResponse(step.to_dict())

    @app.put(base + "/api/p/{project}/steps/{sid}/files/{relpath:path}")
    async def api_attach(project: str, sid: str, relpath: str, request: Request) -> JSONResponse:
        require_token(request)
        info = W.attach_file(sd(project), sid, relpath, await request.body())
        touched([f"{project}/{sid}"])
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
        touched([f"{project}/{sid}"])
        return JSONResponse(info, status_code=201)

    @app.delete(base + "/api/p/{project}/steps/{sid}/files/{relpath:path}")
    async def api_detach(project: str, sid: str, relpath: str, request: Request) -> JSONResponse:
        require_token(request)
        W.delete_file(sd(project), sid, relpath)
        touched([f"{project}/{sid}"])
        return JSONResponse({"ok": True})

    @app.post(base + "/api/sync")
    async def api_sync(request: Request) -> JSONResponse:
        require_token(request)
        return JSONResponse(await asyncio.to_thread(git.commit_now, None))

    return app


app = create_app() if CONFIG_PATH.is_file() else None
