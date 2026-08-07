"""trace_server — 文件的遥控器，不是数据库。

进程内不持有任何独占状态：所有写入立刻落成 note.md，所有读取都是对目录的
一次纯函数编译。因此重启服务器、手工 vim 一个 note.md、git pull 一批新步骤，
效果完全等价——这是把"实时交互"叠加到"纯文件即数据库"上而不破坏后者的关键。

SSE 推的是"版本变了，重新编译"信号，不是增量 patch（保持 P3：编译而非同步）。
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

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

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
    "steps_dir": "steps",
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
    return cfg


def make_config(title: str = "科研溯源") -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["title"] = title
    cfg["space"] = secrets.token_urlsafe(16)[:22]
    cfg["token"] = secrets.token_urlsafe(32)
    return cfg


# ---------------------------------------------------------------- 状态


class State:
    """唯一可变状态：版本号 + 编译缓存。两者都可以随时丢弃重算。"""

    def __init__(self, steps_dir: Path) -> None:
        self.steps_dir = steps_dir
        self.version = 0
        self.sig = ""
        self._cache: dict[str, Any] | None = None
        self._cache_sig = ""
        self.refresh()

    def refresh(self) -> bool:
        sig = core.signature(self.steps_dir)
        if sig != self.sig:
            self.sig = sig
            self.version += 1
            return True
        return False

    def forest(self) -> dict[str, Any]:
        if self._cache is None or self._cache_sig != self.sig:
            self._cache = core.compile_forest(self.steps_dir)
            self._cache_sig = self.sig
        out = dict(self._cache)
        out["version"] = self.version
        return out

    def by_id(self) -> dict[str, Any]:
        return {s["id"]: s for s in self.forest()["steps"]}


# ---------------------------------------------------------------- 应用


def create_app(config: dict[str, Any] | None = None) -> FastAPI:
    cfg = config or load_config()
    steps_dir = (ROOT / cfg["steps_dir"]).resolve()
    steps_dir.mkdir(parents=True, exist_ok=True)

    space = (cfg.get("space") or "").strip("/")
    base = f"/t/{space}" if space else ""
    token = cfg.get("token") or ""

    state = State(steps_dir)
    git = GitSync(
        ROOT,
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

    # ---- 通用 --------------------------------------------------------

    @app.middleware("http")
    async def no_index(request: Request, call_next):
        resp = await call_next(request)
        # "读公开但 URL 不可猜"只有在爬虫永远看不到这个路径时才成立。
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return resp

    @app.exception_handler(W.WriteError)
    async def _write_error(_: Request, exc: W.WriteError):
        code = 409 if isinstance(exc, W.Conflict) else 404 if isinstance(exc, W.NotFound) else 400
        return JSONResponse({"error": str(exc)}, status_code=code)

    def require_token(request: Request) -> None:
        if not token:
            return  # 未配置 token（本地开发）时不拦
        supplied = ""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
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

    def touched(ids: list[str]) -> None:
        state.refresh()
        git.touch(ids)

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
        html = (WEB / "index.html").read_text(encoding="utf-8")
        html = (
            html.replace("__ASSET__", f"{base}/static/")
            .replace("__BASE__", base)
            .replace("__TITLE__", cfg.get("title", "科研溯源"))
            .replace("__MODE__", "server")
            .replace("__DATA__", "")
        )
        return Response(html, media_type="text/html; charset=utf-8")

    @app.get(base + "/static/{name}", include_in_schema=False)
    async def static(name: str) -> Response:
        if "/" in name or "\\" in name or name.startswith("."):
            return PlainTextResponse("bad path", status_code=400)
        p = WEB / name
        if not p.is_file():
            return PlainTextResponse("not found", status_code=404)
        media, _ = mimetypes.guess_type(p.name)
        return Response(p.read_bytes(), media_type=(media or "application/octet-stream"))

    @app.get(base + "/files/{sid}/{relpath:path}", include_in_schema=False)
    async def files(sid: str, relpath: str) -> Response:
        by_id = W.load(steps_dir)
        target = W.resolve_attachment(steps_dir, by_id, sid, relpath)
        if not target.is_file():
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(target)

    # ---- 读 API ------------------------------------------------------

    @app.get(base + "/api/forest")
    async def api_forest() -> JSONResponse:
        return JSONResponse(state.forest())

    @app.get(base + "/api/steps/{sid}")
    async def api_step(sid: str) -> JSONResponse:
        forest = state.forest()
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
        return JSONResponse(
            {
                "title": cfg.get("title"),
                "version": state.version,
                "steps": len(state.forest()["steps"]),
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

    @app.post(base + "/api/steps")
    async def api_create(request: Request) -> JSONResponse:
        require_token(request)
        payload = await body_json(request)
        step, created = W.create_step(
            steps_dir,
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
            touched([step.id])
        out = step.to_dict()
        out["created"] = created
        return JSONResponse(out, status_code=201 if created else 200)

    @app.patch(base + "/api/steps/{sid}")
    async def api_update(sid: str, request: Request) -> JSONResponse:
        require_token(request)
        payload = await body_json(request)
        step = W.update_step(steps_dir, sid, payload)
        touched([sid])
        return JSONResponse(step.to_dict())

    @app.put(base + "/api/steps/{sid}/files/{relpath:path}")
    async def api_attach(sid: str, relpath: str, request: Request) -> JSONResponse:
        require_token(request)
        data = await request.body()
        info = W.attach_file(steps_dir, sid, relpath, data)
        touched([sid])
        return JSONResponse(info, status_code=201)

    @app.delete(base + "/api/steps/{sid}/files/{relpath:path}")
    async def api_detach(sid: str, relpath: str, request: Request) -> JSONResponse:
        require_token(request)
        W.delete_file(steps_dir, sid, relpath)
        touched([sid])
        return JSONResponse({"ok": True})

    @app.post(base + "/api/sync")
    async def api_sync(request: Request) -> JSONResponse:
        require_token(request)
        return JSONResponse(await asyncio.to_thread(git.commit_now, None))

    return app


app = create_app() if CONFIG_PATH.is_file() else None
