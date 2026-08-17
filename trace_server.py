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
import time
from contextlib import asynccontextmanager
from datetime import date as _date
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse

import trace_core as core
import trace_mcp as mcp          # untranslated_report / step_context / open_forks：判据只留一份
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
    # 定期核对外部路径。参数的取值理由见 sweep_path_checks 和 poller。
    "paths": {"enabled": True, "every_hours": 24, "stale_days": 30, "budget": 500,
              "first_delay": 300},
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


# ---------------------------------------------------------------- 路径核对


def sweep_path_checks(root: Path, *, stale_days: int = 30, budget: int = 500,
                      today: str = "") -> dict[str, int]:
    """把本机看得见的外部路径逐条 stat 一遍，结果写回 `checked=` / `missing=`。

    同步函数（磁盘 I/O 不小），调用方负责放进线程——poller 里那一段就是这么调的。
    纯粹按参数工作、不读全局配置，所以测试可以直接调它而不用起一个服务。

    **为什么服务端要自己跑一遍**：外部产物不在仓库里，只记了位置，而位置会失效。
    用户上一次是**手工**核对 164 条路径才发现三个目录已经被删了（其中一个 57 GB）——
    那本该是机器每天顺手做的事。

    四道闸门，每一道都对着一种具体的伤害：

    ① **远端一律不探测。** `s3://` / `https://` 交给 probe_path 判成够不着。
       这条不是省事：任何能写记录的人都能往 `path:` 里塞一个内网地址，服务端要是
       去发请求，就等于把「从这台机器发起请求」的权力交给了他（SSRF），而这台机器
       恰好看得见整个数据仓。远端产物还在不在，只能由人核对后写回结论。

    ② **够不着 ≠ 不存在。** 判据在 probe_path 里（上级目录看得见才敢下结论）。
       服务器上没挂 /blue 时，一整批好好的记录会被盖上「已确认不存在」——
       而 missing 是**结论**（P4），错误的结论比没有结论贵得多。

    ③ **不数目录条目数。** stat 一个目录是毫秒级的，数它底下有多少个文件却要遍历
       整棵树；那个 57 GB 的目录在网络盘上能跑几分钟，而这个 sweep 跑在服务进程里。
       所以 n= 只在人显式要求时才数（trace_cli paths --check --count / MCP 的 count 参数）。

    ④ **只在结论会变时才写。** 每天把 164 条 `checked=` 全刷一遍，等于每天给数据仓
       造一次 164 个文件的 git 提交，而里面一个事实都没变。所以：从没核对过的写、
       结论翻转的写、上次核对已经超过 stale_days 的写，其余跳过。

    返回 {"probed", "written", "gone", "unreachable", "skipped"} —— 只是给日志用。
    """
    today = today or _date.today().isoformat()
    fresh_after = (_date.fromisoformat(today) - timedelta(days=max(0, stale_days))).isoformat()
    stat = {"probed": 0, "written": 0, "gone": 0, "unreachable": 0, "skipped": 0}

    for p in core.scan_projects(root):
        steps_dir = core.steps_dir_of(root, p.slug)
        try:
            steps, _files, _warns = core.scan(steps_dir, with_files=False)
        except OSError:
            continue
        for s in steps:
            for row in s.paths:
                if stat["probed"] >= budget:
                    stat["skipped"] += 1
                    continue
                last = row.get("checked") or row.get("missing") or ""
                if row.get("state") and last >= fresh_after:
                    stat["skipped"] += 1          # 还新鲜，没必要再看一眼
                    continue
                stat["probed"] += 1
                state, size = mcp.probe_path(row["location"])
                if state == mcp.PROBE_UNREACHABLE:
                    stat["unreachable"] += 1
                    continue
                exists = state == mcp.PROBE_PRESENT
                try:
                    W.record_path_check(steps_dir, s.id, row["location"],
                                        exists=exists, date=today, size=size)
                except (W.WriteError, OSError):
                    # 一条路径写不进去（记录被人手工改过、磁盘满了）不该让整轮扫描停下来。
                    continue
                stat["written"] += 1
                if not exists:
                    stat["gone"] += 1
    return stat


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

    判定规则和 MCP 的 trace_search 一致（id / 标题 / 正文 / 标签 /
    **产物与代码的位置** / **`decision:` 与候选说明** / **各语言的译文**，
    大小写不敏感），人和 agent 搜同一个词
    必须搜到同一批东西——FORMAT.md 第 0 节的「信息对等」。

    **译文也要搜**，这条不是可选项：这套系统的底线是「删掉全部程序，`grep -r` 还能
    回答『为什么放弃了 X』」，加了双语之后英文的 grep 也要能回答同一个问题。
    `grep -r abandoned` 命中 note.en.md 而站内搜索命中不了，等于自带的搜索比 grep 弱——
    信息明明在仓库里，工具却说「没搜到」，而「没搜到」会被读成「没试过」，
    于是有人再试一遍那条已经走死的路。命中在哪个语言由 `langs` / `snippet_lang` 说清楚，
    不含糊成一条来路不明的命中。

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
            # 「东西在哪」也要搜得到：「/orange/…/run042/best.pt 是哪一步产出的」
            # 「谁用了 20260809 那个快照」正是 path: / code: 存在的主要用途，
            # 而 `grep -rn best.pt projects/` 一秒就答得出。工具比 grep 弱的地方，
            # 恰好是 agent 唯一够得到的地方。判据在 core，MCP 那侧用的是同一份。
            if q in core.locations_haystack(s).lower():
                where.append("paths")
            # 「当年是在哪个岔路口纠结这件事」同理：`decision:` 是整套东西里唯一
            # 只能人写的一句话，`grep -rn 类别不平衡 projects/` 一秒就答得出，
            # 站内搜索答不出就等于比 grep 弱。判据在 core，MCP 和网页用的是同一份。
            if q in core.fork_haystack(s).lower():
                where.append("fork")

            # 摘要优先取原文：原文是权威，译文只在原文没命中时才拿来当上下文，
            # 否则同一条命中在不同语言下会给出两段不一样的摘要。
            snippet = _snippet(body, at) if at >= 0 else ""
            snippet_lang = (s.get("lang") or "") if at >= 0 else ""
            langs: list[str] = []
            for code in sorted(s.get("tr") or {}):
                t = (s["tr"] or {})[code]
                hit = False
                if q in (t.get("title") or "").lower():
                    if "tr.title" not in where:
                        where.append("tr.title")
                    hit = True
                k = (t.get("body") or "").lower().find(q)
                if k >= 0:
                    if "tr.body" not in where:
                        where.append("tr.body")
                    hit = True
                    if not snippet:
                        snippet, snippet_lang = _snippet(t.get("body") or "", k), code
                if hit:
                    langs.append(code)

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
                "snippet": snippet,
                # 命中落在哪些译文里（空 = 只有原文命中），以及上面那段摘要是从
                # 哪个语言的正文里截的（空 = 原文且原文没声明语言）。
                "langs": langs,
                "snippet_lang": snippet_lang,
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

    # 定期核对外部路径。默认开着、一天一遍：这件事的价值全在「不用记得去做」，
    # 而它的代价被 sweep_path_checks 里那四道闸门压到了「几百次 stat + 只在结论
    # 变了时才写」。first_delay 让头几分钟留给首屏——起服务的那一刻正是编译整棵树、
    # 跑 git preflight 的时候，扫盘该往后站；顺带也让短命的进程（测试、`--selfcheck`）
    # 一次都不会碰用户的记录。
    psweep = {**(DEFAULT_CONFIG["paths"]), **(cfg.get("paths") or {})}

    async def poller() -> None:
        next_sweep = time.monotonic() + float(psweep.get("first_delay", 300))
        while True:
            try:
                await asyncio.sleep(POLL_SECONDS)
                if psweep.get("enabled") and time.monotonic() >= next_sweep:
                    next_sweep = time.monotonic() + float(psweep.get("every_hours", 24)) * 3600
                    report = await asyncio.to_thread(
                        sweep_path_checks, data_root,
                        stale_days=int(psweep.get("stale_days", 30)),
                        budget=int(psweep.get("budget", 500)))
                    if report["gone"]:
                        # 唯一值得打印的那一件事：有位置从「在」变成了「不在」。
                        print(f"[trace] 路径核对：{report['gone']} 处外部产物已确认不存在"
                              f"（记录保留，见各步的 path: … missing=）")
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
            # 服务模式下那张图按需去 /pipeline/figure.svg 取（同源 fetch），
            # 所以这里不预灌：它会随记录改变，灌进 HTML 就成了一份会过期的拷贝。
            .replace("__PIPESVG__", "")
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
        """改显示名和/或洞察。slug 不动——它是 URL 的一部分。

        洞察那一条走 W.add_insight / W.update_insight（而不是 update_project 的 add=），
        是为了把**分配到的 id** 放进响应：不知道刚写的那条叫 `p3`，调用方就说不出
        下一句「· 取代 p3」，而「取代了谁」只写在取代者身上，没有别处能补。
        带 `id` 就是就地改写那一条既有洞察——被它取代的那条一个字都不删。
        """
        require_token(request)
        payload = await body_json(request)
        info: dict[str, Any] = {}
        a = payload.get("add_insight")
        if a:
            if not isinstance(a, dict) or not (a.get("kind") or a.get("id")):
                raise W.WriteError(
                    'add_insight 要形如 {"kind": "idea", "text": "…"}；'
                    '要改既有的那一条就给 {"id": "p1", "text": "…"} 或 {"id": "p1", "supersedes": "p0"}')
            lang = str(a.get("lang") or "")
            if a.get("id"):
                info = W.update_insight(data_root, project, str(a["id"]),
                                        text=a.get("text"), supersedes=a.get("supersedes"), lang=lang)
            else:
                info = W.add_insight(data_root, project, str(a["kind"]), str(a.get("text") or ""),
                                     supersedes=a.get("supersedes") or "", lang=lang)
        if payload.get("name") is not None or payload.get("insights") is not None:
            p = W.update_project(data_root, project,
                                 name=payload.get("name"), insights=payload.get("insights"))
        else:
            hit = next((x for x in core.scan_projects(data_root) if x.slug == project), None)
            if hit is None:
                raise W.NotFound(f"项目 {project} 不存在")
            p = hit
        out = p.to_dict()
        if info:
            out["insight"] = info
        await touched([f"project:{p.slug}"])
        return JSONResponse(out)

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
        # 「我是不是某一组互斥候选里的一个」「这条汇回边上的两条线在哪分开」都要看
        # 整片森林才答得出，而单步这个响应正是详情面板唯一拉的东西。判据和渲染都在
        # trace_mcp.step_context 那一份里，MCP 的本地后端用的是同一个函数——
        # 各写一遍的话，同一步在网页上和在 agent 眼里会给出不同的归属。
        out.update(mcp.step_context(forest, sid))
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

    @app.get(base + "/api/p/{project}/untranslated")
    async def api_untranslated(project: str, lang: str = "en") -> JSONResponse:
        """还欠哪些语言版本。**读，所以公开**——和 forest / search 一致。

        判据本身来自 trace_mcp.untranslated_report：REST、MCP、CLI 三个门面共用
        同一份实现。各写一遍的话，三处对「什么叫还没翻译」的答案迟早分家，
        而 agent 是照着其中一个的答案去补的。

        lang 走 norm_lang 而不是原样比较：客户端传 EN / zh-hant 时，和文件名
        （note.en.md / note.zh-Hant.md）对不上就会报「全都没翻译」——一个
        必然让人去重复翻译的假答案。非法语言码在这里就是 400，不是空结果。
        """
        sd(project)
        lang = W.norm_lang(lang)
        forest = await state.forest(project)
        ps = await asyncio.to_thread(core.scan_projects, data_root)
        p = next((x.to_dict() for x in ps if x.slug == project), None)
        out = mcp.untranslated_report(forest, p, lang)
        out["version"] = state.version
        return JSONResponse(out)

    # 岔路口（互斥候选组）。**读，所以公开**——和 forest / search 一致。
    #
    # 数据全部来自同一次 compile_forest，这里只是把「还没做决定的那几组」摘出来并
    # 配上标题。要它单独成一条路由，是因为「我还有几个岔路口悬着」是研究者隔几天
    # 回来第一句问的话，而让每个客户端各自下载整片森林再自己筛一遍，等于把这条
    # 判据复制到每个门面里——迟早分家。
    #
    @app.get(base + "/api/p/{project}/forks")
    async def api_forks(project: str, scope: str = "open") -> JSONResponse:
        sd(project)
        forest = await state.forest(project)
        groups = forest.get("branch_groups") or []
        want = groups if scope in ("any", "all") else mcp.open_forks(forest)
        ids = {i for g in want for i in g.get("options") or []} | {
            g["at"] for g in want if g.get("at")}
        titles = {s["id"]: s["title"] for s in forest["steps"] if s["id"] in ids}
        return JSONResponse({
            "project": project,
            "forks": want,
            "total": len(groups),
            # 「还没决定」是从 status 派生的（其余候选标 dead 就是选择本身），
            # 磁盘上没有任何「选中了谁」的字段，所以这个数字每次都是现算的。
            "open": len(mcp.open_forks(forest)),
            "titles": titles,
            "version": state.version,
        })

    # 定稿流程。**读，所以公开**——和 forest / forks 一致。
    #
    # 为什么它必须是一条**单独**的路由，而不是让客户端从 forest 里取那个键：
    # 一个 `result:` 都没声明的项目，forest 里刻意**整个 pipeline 键都不出现**
    # （现存项目必须完全无感，内核那侧有测试钉着）。于是空态那句「教你怎么办」
    # 只有主动问起来的这条路上拿得到 —— 而空态恰恰是这个功能最需要说话的时候：
    # 没有它，用户看到的是一块什么都不说的空面板。
    #
    # 派生一次、三个门面共用：这里、MCP 的 trace_pipeline、CLI 的 `pipeline`，
    # 拼 payload 的是同一个 mcp.pipeline_payload。各拼一遍的话，迟早有一个字段
    # 在某个门面上分家 —— 而分家的那一份正好是要写进论文的。
    def _pipeline(project: str, forest: dict) -> dict:
        # 抬头用显示名而不是目录名：三样导出是要交出去的产物，抬头写着 slug
        # 的话，收到的人只会以为拿错了文件。
        name = next((p.name for p in core.scan_projects(data_root) if p.slug == project),
                    project)
        return mcp.pipeline_payload(forest, project, core.compute_pipeline({}, []), name)

    @app.get(base + "/api/p/{project}/pipeline")
    async def api_pipeline(project: str) -> JSONResponse:
        sd(project)
        out = _pipeline(project, await state.forest(project))
        out["version"] = state.version
        return JSONResponse(out)

    # 两样导出走**服务端**而不是让前端各画一遍。理由不是省事：markdown 和 SVG
    # 各写一份 Python 一份 JS，两份迟早不一致，**而其中一份会进论文**。
    # 生成器是纯函数、逐字节确定（P3），所以这两条路由没有缓存、也不需要缓存。
    @app.get(base + "/api/p/{project}/pipeline/figure.svg", include_in_schema=False)
    async def api_pipeline_svg(project: str) -> Response:
        sd(project)
        svg = mcp.pipeline_svg(_pipeline(project, await state.forest(project)))
        return Response(svg, media_type="image/svg+xml; charset=utf-8")

    @app.get(base + "/api/p/{project}/pipeline/methods.md", include_in_schema=False)
    async def api_pipeline_methods(project: str) -> Response:
        sd(project)
        md = mcp.pipeline_methods(_pipeline(project, await state.forest(project)))
        return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")

    # 第三样：能直接发给合作者的那一页（单文件、无脚本、无外部资源）。
    # 它和上面两条是同一批字节的第三种包装，走同一个纯函数——网页上那个
    # 「独立页面」按钮就是指到这里，前端一行渲染逻辑都不再有。
    @app.get(base + "/api/p/{project}/pipeline/page.html", include_in_schema=False)
    async def api_pipeline_page(project: str) -> Response:
        sd(project)
        forest = await state.forest(project)
        html = mcp.pipeline_page(_pipeline(project, forest))
        return Response(html, media_type="text/html; charset=utf-8")

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
                # 两个版本是两回事，别合并：
                #   version  —— **内容**版本，文件一变就涨，SSE 靠它决定要不要重编译
                #   software —— 这台机器上跑的**代码**版本
                # 加 software 是因为更新流程缺一环：`git pull` + 重启之后，
                # 没有任何办法从外面确认新代码真的起来了（旧进程还活着、
                # 服务没重启、拉错了分支，症状都是"看起来一切正常"）。
                "version": state.version,
                "software": mcp.SERVER_VERSION,
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
            # 数据依赖和代码位置在建步骤时就该能写：一步的输入是「开跑之前」就知道的事，
            # 逼调用方建完再 PATCH 一次，等于给「先建 wip 再开跑」这条规矩加了一道摩擦。
            inputs=payload.get("inputs"),
            code=payload.get("code"),
            lang=payload.get("lang", ""),
            # 分叉语义同理，而且更急：A / B 两条互斥的路多半是同一次想清楚之后一起
            # 建出来的，`decision`（整件事里唯一推导不出来的那句话）如果要等建完再
            # PATCH 一次才写得进去，大概率就永远空着了。
            branch=payload.get("branch", ""),
            decision=payload.get("decision", ""),
            # 「这一步进不进最终流程」偶尔在动手之前就知道（「拿来试试看的」）。
            # 不在这里收下，那句**必填**的理由就得等一次 PATCH，而多一道摩擦
            # 它就永远空着——和 decision 是同一个道理。
            pipeline=payload.get("pipeline", ""),
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

        # `parent` 出现在 PATCH 里，而且和现在的值不一样 → 这是一次**移动**，
        # 分流到 move_step（它会追加一行 moved: 审计，reason 必填）。
        # 用户要的就是这个形状：PATCH {"parent": "013b", "reason": "…"}。
        #
        # 只在「真的变了」时分流：把整份步骤原样 PATCH 回来（网页保存正文就是这么干的）
        # 里面必然带着一个没变的 parent，那不是一次移动，不该因为没写 reason 就 400，
        # 更不该在历史里留下一条什么都没改的审计。
        if "parent" in payload:
            cur = W.load(steps).get(sid)
            want = (str(payload.get("parent") or "").strip() or None)
            if cur is not None and want != (cur.parent or None):
                mixed = sorted(set(payload) - {"parent", "reason", "author", "date"})
                if mixed:
                    # 移动是一次独立的行为，它有自己的审计行。和改正文/状态混在一起发，
                    # 要么得写两次盘（第二次的 expect 已经过期），要么得悄悄丢掉一半——
                    # 两条都比多发一个请求糟。
                    raise W.WriteError(
                        "移动要单独发一次请求。这一次的请求体里还带着 "
                        + "、".join(mixed)
                        + " —— 先 PATCH {\"parent\": …, \"reason\": …} 把它移过去，"
                        "再 PATCH 剩下的字段；那样两件事各自留下自己的记录。")
                info = W.move_step(steps, sid, want, str(payload.get("reason") or ""),
                                   by=str(payload.get("author") or ""),
                                   date=str(payload.get("date") or ""), expect=expect)
                await touched([f"{project}/{sid} 已移动"])
                return JSONResponse(info)
            payload.pop("parent")
            payload.pop("reason", None)

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

    @app.post(base + "/api/p/{project}/forks")
    async def api_mark_fork(project: str, request: Request) -> JSONResponse:
        """把一组兄弟**成组**标成互斥候选，顺手把「在决定什么」写在它们的父节点上。

        为什么不让客户端对每个孩子各 PATCH 一次 `branch`（那条路一直都在，也没被
        关掉）：候选组是派生的——「同一个父节点底下所有 alternative 的孩子」。一次
        只看得见一个孩子的话，把两个**不同父节点**下的步骤各标一次，得到的是两个
        各含一个候选的组：一条错都不报，而人以为自己刚记下了一个岔路口。同父校验
        只有在看得见整组的地方做得了，那个地方就是 W.mark_alternatives。

        落盘的东西一个字没多：每个孩子一行 `branch:`，父节点一行 `decision:`。
        **父节点上绝不写孩子清单**，兄弟之间也不互相登记——那是双真相源，而且一次
        move_step 就让存下来的清单变成谎话。
        """
        require_token(request)
        payload = await body_json(request)
        notes = payload.get("notes")
        if notes is not None and not isinstance(notes, dict):
            raise W.WriteError('notes 要写成 {"步骤id": "这个候选自己的角度"} 这样的对象')
        info = W.mark_alternatives(sd(project), payload.get("ids") or payload.get("steps") or [],
                                   decision=str(payload.get("decision") or ""),
                                   notes=notes)
        await touched([f"{project}/{i}" for i in info["marked"]])
        return JSONResponse(info)

    # ---- 成果声明 ----------------------------------------------------
    #
    # 按步骤 id 增删，**没有「整组替换」这条路**。这不是遗漏：一个用打开页面那一刻
    # 的旧列表整组提交的表单，会静默删掉这期间 agent 刚声明的那一条——和洞察那边
    # `_merge_insights` 挡的是同一次事故。要做「编辑成果列表」的界面，请按行发增删。
    #
    # 为什么它值得单开一条路由，而不是塞进 PATCH /api/projects 的一个字段：
    # 它决定的东西比它看起来重得多——重写的是整条定稿流程、论文 Methods 里出现哪几步、
    # 导出的那张图长什么样。和「改个显示名」走同一个随手的口子，在语义上就把它降级
    # 成了一个字段；而且它要的校验（步骤必须存在、不能是 dead）得 load 一遍步骤树，
    # 那是 update_project 够不着的。

    @app.put(base + "/api/p/{project}/results/{sid}")
    async def api_set_result(project: str, sid: str, request: Request) -> JSONResponse:
        """把某一步声明成成果（或就地改写它那句说明）。

        悬空的 `result:` 比悬空的 `input:` 后果重得多：后者只是让一条数据边不参与
        计算（读侧警告一声），前者让整条流程从一个不存在的起点出发，结果是空，
        而页面上一个字都不报。所以这条校验在写入侧就是硬的（W.set_result 里）。
        """
        require_token(request)
        sd(project)                         # 项目不存在就 404，别把它当成一次成功的写
        payload = await body_json(request)
        info = W.set_result(data_root, project, sid, str(payload.get("note") or ""))
        await touched([f"project:{project}"])
        return JSONResponse(info, status_code=201 if info.get("created") else 200)

    @app.delete(base + "/api/p/{project}/results/{sid}")
    async def api_drop_result(project: str, sid: str, request: Request) -> JSONResponse:
        """撤掉一条成果声明。**不要求写原因**，也不留审计。

        `result:` 不是一段历史，是一个当前指针（「论文现在报的是哪一步」）。撤掉它
        不销毁任何事实：那一步和它的记录一个字都没动，开发路径上它还在原处。
        对照之下 move_step 的 reason 是必填的——移动**销毁**了「原来挂在哪」，
        那条 `moved:` 是它仅存的证据。判据是同一条。
        """
        require_token(request)
        sd(project)
        info = W.drop_result(data_root, project, sid)
        await touched([f"project:{project}"])
        return JSONResponse(info)

    @app.post(base + "/api/p/{project}/steps/{sid}/paths/check")
    async def api_check_path(project: str, sid: str, request: Request) -> JSONResponse:
        """记一次「这条路径还在不在」的核对结果。**只动机器字段，记录一律不删。**

        为什么核对结果要有自己的写接口，而不是让扫描任务自己写文件：这个仓库只有
        一条写入路径（trace_write），第二条写入路径正是上一代系统出 bug 的形状。
        服务端的定期扫描、agent 的 trace_check_paths、CLI 的 `paths --check`，
        三个入口最后都落到同一个 W.record_path_check 上。

        `exists` **必须显式给 true / false**。默认成 false 的话，一个漏填字段的
        客户端就能把「我没查」写成「已确认不存在」——而这条记录是要被后来人当成
        结论读的。「够不着」的正确做法是**什么都不发**（见 trace_mcp.probe_path）。
        """
        require_token(request)
        payload = await body_json(request)
        exists = payload.get("exists")
        if not isinstance(exists, bool):
            raise W.WriteError(
                'exists 必须显式给 true 或 false。「没给」不等于「不存在」——'
                "看不见那条路径（远端位置、这台机器上没挂那个盘）时正确的做法是"
                "不发这个请求，而不是发一个空的：missing 是要被人当成结论读的。")
        for k in ("size", "n"):
            v = payload.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
                raise W.WriteError(f"{k} 要么不给，要么是整数（size 是**字节数**，别写 \"12 GB\"）")
        info = W.record_path_check(
            sd(project), sid, str(payload.get("loc") or payload.get("location") or ""),
            exists=exists, date=str(payload.get("date") or ""),
            size=payload.get("size"), n=payload.get("n"))
        await touched([f"{project}/{sid} 的路径核对"])
        return JSONResponse(info)

    # ---- 翻译 --------------------------------------------------------
    #
    # 三条写端点都只碰 note.<lang>.md / project.<lang>.md，原文一个字节都不动。
    # 这也是刻意的形状：既然翻译永远走不到原文那条路上，「立刻翻译」和「几天后
    # 补翻译」就是同一次调用，只是发生的时刻不同，不需要在建步骤时先决定。
    #
    # expect 对的是**这份译文自己**的 digest（不是 note.md 的）。两条链各管各的：
    # 正文被改过并不会让手上那份译文的 expect 作废，否则每改一次正文，所有语言的
    # 译文都得先重读一遍才写得进去。

    @app.put(base + "/api/p/{project}/steps/{sid}/tr/{lang}")
    async def api_write_translation(project: str, sid: str, lang: str, request: Request) -> JSONResponse:
        require_token(request)
        payload = await body_json(request)
        expect = read_expect(request, payload)
        info = W.write_translation(sd(project), sid, lang,
                                   title=payload.get("title", ""),
                                   body=payload.get("body", ""), expect=expect)
        await touched([f"{project}/{sid} 的 {info['lang']} 译文"])
        return JSONResponse(info)

    @app.delete(base + "/api/p/{project}/steps/{sid}/tr/{lang}")
    async def api_drop_translation(project: str, sid: str, lang: str, request: Request) -> JSONResponse:
        """撤掉一个语言版本。**这不是 P2 的例外**：只追加约束的是记录本身
        （id / parent / 结论），译文是原文的衍生物，删掉之后界面自动回退到原文，
        一个事实都没丢——所以它也不要求写原因。"""
        require_token(request)
        info = W.drop_translation(sd(project), sid, lang)
        await touched([f"{project}/{sid} 的 {info['lang']} 译文已删除"])
        return JSONResponse(info)

    @app.put(base + "/api/p/{project}/tr/{lang}")
    async def api_write_project_translation(project: str, lang: str, request: Request) -> JSONResponse:
        """项目笔记的译文。正文走和中文版同一道闸门：提交的文本只替换那四个洞察
        小节，磁盘上的 `## 已删除` / `## Deleted` 逐字保留（删除记录是目录没了之后
        仅存的证据，中文版护得住、英文版护不住说不过去）。"""
        require_token(request)
        sd(project)                       # 项目不存在 / slug 不合法就在这儿 404
        payload = await body_json(request)
        expect = read_expect(request, payload)
        info = W.write_project_translation(data_root, project, lang,
                                           name=payload.get("name", ""),
                                           body=payload.get("body", ""), expect=expect)
        await touched([f"project:{project} 的 {info['lang']} 译文"])
        return JSONResponse(info)

    @app.delete(base + "/api/p/{project}/tr/{lang}")
    async def api_drop_project_translation(project: str, lang: str, request: Request) -> JSONResponse:
        """撤掉项目笔记的一个语言版本。理由同步骤译文的 DELETE。

        单独有这条路由，是为了让「删项目译文」也走 trace_write 那一个写入点。
        没有它的话，要么让人手工去删文件（那就绕过了唯一写入路径），
        要么在这里自己 unlink 一把——那正是这个仓库用结构杜绝的第二条写入路径。
        """
        require_token(request)
        sd(project)                       # 项目不存在 / slug 不合法就在这儿 404
        info = W.drop_project_translation(data_root, project, lang)
        await touched([f"project:{project} 的 {info['lang']} 译文已删除"])
        return JSONResponse(info)

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
