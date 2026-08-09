"""HTTP 层的断言。

内核和写入路径各自有测试，但它们之间还隔着一层：鉴权、状态码、错误体。
这一层出问题的后果和别处不一样——**写入端点漏掉令牌检查，就是公网上的任意写**。
读是公开的（用户的明确选择），所以"哪些端点必须要令牌"是条硬边界，值得机械地全测一遍。
"""

import asyncio
import errno
import hashlib
import shutil
import subprocess
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import trace_core as core  # noqa: E402
import trace_git as G  # noqa: E402
import trace_server as S  # noqa: E402
import trace_write as W  # noqa: E402

TOKEN = "test-token-not-a-real-one"  # HTTP header 只能是 ASCII
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(S, "ROOT", tmp_path)
    app = S.create_app({"data_dir": ".", "space": "", "token": TOKEN, "git": {"enabled": False}})
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    with TestClient(app) as c:
        c.root = tmp_path
        c.sd = core.steps_dir_of(tmp_path, "课题")
        yield c


def mkstep(c, **kw):
    s, _ = W.create_step(c.sd, **kw)
    return s


def note_of(c, sid: str) -> Path:
    return c.sd / W.load(c.sd)[sid].dirname / core.NOTE_NAME


def waited(cond, seconds: float = 4.0):
    """轮询等一个条件成立。给后台任务（poller / debounce）留出跑的机会。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        got = cond()
        if got:
            return got
        time.sleep(0.03)
    return cond()


# ------------------------------------------------------- 令牌边界


# 这张表是硬边界：**每一个会改磁盘的端点都必须在这里**。
# 新增写端点却忘了加进来，等于公网上多一个任意写的口子。
WRITES = [
    ("POST", "/api/projects", {"name": "另一个"}),
    ("PATCH", "/api/projects/课题", {"name": "改名"}),
    ("POST", "/api/p/课题/steps", {"title": "x"}),
    ("PATCH", "/api/p/课题/steps/001", {"status": "done"}),
    ("DELETE", "/api/p/课题/steps/001", {"reason": "误建"}),
    ("PUT", "/api/p/课题/steps/001/files/a.txt", {}),
    ("POST", "/api/p/课题/steps/001/files", {}),
    ("DELETE", "/api/p/课题/steps/001/files/a.txt", {}),
    ("POST", "/api/sync", {}),
    # 译文也是磁盘上的文件。「它碰不到原文」不等于「谁都能写」——
    # 一个不设防的 PUT .../tr/en 足够让任何人往每一步挂一份自己写的英文版，
    # 而界面默认就是英文，读者第一眼看到的就是它。
    ("PUT", "/api/p/课题/steps/001/tr/en", {"title": "Anyone can write this"}),
    ("DELETE", "/api/p/课题/steps/001/tr/en", {}),
    ("PUT", "/api/p/课题/tr/en", {"name": "Anyone can rename this"}),
    ("DELETE", "/api/p/课题/tr/en", {}),
]


@pytest.mark.parametrize(("method", "path", "payload"), WRITES)
def test_every_write_endpoint_requires_the_token(client, method, path, payload):
    mkstep(client, title="在那儿")
    r = client.request(method, path, json=payload)
    assert r.status_code == 401, f"{method} {path} 没要令牌 —— 这是公网上的任意写"


@pytest.mark.parametrize(("method", "path", "payload"), WRITES)
def test_a_wrong_token_is_not_good_enough(client, method, path, payload):
    mkstep(client, title="在那儿")
    r = client.request(method, path, json=payload, headers={"Authorization": "Bearer wrong-guess"})
    assert r.status_code == 401


def test_reads_stay_public(client):
    """用户的明确选择：读公开，只有写要令牌。"""
    mkstep(client, title="公开可读")
    for path in ("/api/projects", "/api/p/课题/forest", "/api/p/课题/steps/001"):
        assert client.get(path).status_code == 200, path


# ------------------------------------------------------- DELETE


def test_delete_removes_the_step_and_reports_the_damage(client):
    a = mkstep(client, title="根")
    b = mkstep(client, parent=a.id, title="要删的")
    mkstep(client, parent=b.id, title="孩子", body=f"## 为什么\n见 [[{b.id}]]")

    r = client.request("DELETE", f"/api/p/课题/steps/{b.id}",
                       json={"reason": "误建的测试步骤", "by": "human"}, headers=AUTH)
    assert r.status_code == 200
    info = r.json()
    assert info["orphaned"] == ["003"] and info["dangling_refs"] == ["003"]

    assert not (client.sd / b.dirname).exists()
    assert client.get(f"/api/p/课题/steps/{b.id}").status_code == 404
    forest = client.get("/api/p/课题/forest").json()
    assert [s["id"] for s in forest["steps"]] == ["001", "003"]
    assert any(w["code"] == "dangling_parent" for w in forest["warnings"])


def test_delete_without_a_reason_is_a_400_not_a_500(client):
    mkstep(client, title="x")
    for payload in ({}, {"reason": "   "}):
        r = client.request("DELETE", "/api/p/课题/steps/001", json=payload, headers=AUTH)
        assert r.status_code == 400, "缺原因是用户的错，不是服务器的错"
        assert "原因" in r.json()["error"]
    assert (client.sd / "001_x").exists() or list(client.sd.glob("001_*")), "报错之后不该已经删了"


def test_deleting_a_missing_step_is_a_404(client):
    r = client.request("DELETE", "/api/p/课题/steps/999", json={"reason": "随便"}, headers=AUTH)
    assert r.status_code == 404


def test_delete_bumps_the_version_so_open_pages_refresh(client):
    """页面靠 SSE 推的 version 变化来决定重编译。删除不 bump，别人的页面就一直显示已经没了的步骤。"""
    mkstep(client, title="x")
    before = client.get("/api/p/课题/forest").json()["version"]
    client.request("DELETE", "/api/p/课题/steps/001", json={"reason": "误建"}, headers=AUTH)
    assert client.get("/api/p/课题/forest").json()["version"] != before


def test_the_reason_lands_in_project_md(client):
    mkstep(client, title="误建的")
    client.request("DELETE", "/api/p/课题/steps/001",
                   json={"reason": "手滑建的，不是真实验", "by": "human", "date": "2026-08-08"},
                   headers=AUTH)
    text = (core.project_dir(client.root, "课题") / core.PROJECT_NOTE).read_text(encoding="utf-8")
    assert "## 已删除" in text and "手滑建的" in text


def test_a_malformed_body_is_a_400_not_a_traceback(client):
    mkstep(client, title="x")
    r = client.request("DELETE", "/api/p/课题/steps/001",
                       content="{ 这不是 json".encode("utf-8"),
                       headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == 400


# ------------------------------------------------------- 冲突控制（乐观并发）


def test_the_forest_and_the_single_step_expose_the_digest(client):
    """客户端要先能读到指纹，才谈得上把它当 expect 传回来。"""
    mkstep(client, title="有指纹的", body="正文")
    raw = note_of(client, "001").read_bytes()
    want = hashlib.sha256(raw).hexdigest()[:12]

    forest = client.get("/api/p/课题/forest").json()
    assert forest["steps"][0]["digest"] == want

    one = client.get("/api/p/课题/steps/001").json()
    assert one["digest"] == want, "单步读不到 digest 的话，网页编辑器就没法带 expect 保存"


def test_a_stale_save_is_rejected_instead_of_silently_overwriting(client):
    """需求 1 的招牌场景：人在网页里编辑，同一时间自己的 agent 往同一步写结果。

    以前两次 PATCH 都返回 200，agent 写的那段无声消失——而那段往往是刚跑完、
    再也跑不出来的实验结果。
    """
    mkstep(client, title="004", body="base")
    stale = client.get("/api/p/课题/steps/001").json()["digest"]

    # agent 先写（不带 expect：追加式写入本来就不该被挡）
    assert client.patch("/api/p/课题/steps/001",
                        json={"body": "base\nagent 跑完写的结果"}, headers=AUTH).status_code == 200

    # 人拿着打开编辑器那一刻的旧快照保存
    r = client.patch("/api/p/课题/steps/001",
                     json={"body": "base\n人手打的", "expect": stale}, headers=AUTH)
    assert r.status_code == 409
    assert "agent 跑完写的结果" in note_of(client, "001").read_text(encoding="utf-8"), \
        "被拒的写入不许改动磁盘上任何一个字节"


def test_the_conflict_response_carries_the_servers_current_content(client):
    """只说一句「冲突了」的话，客户端除了整个重拉别无选择，人也看不到自己要盖掉的是什么。"""
    mkstep(client, title="004", body="base")
    stale = client.get("/api/p/课题/steps/001").json()["digest"]
    client.patch("/api/p/课题/steps/001", json={"body": "base\nagent 写的"}, headers=AUTH)

    body = client.patch("/api/p/课题/steps/001",
                        json={"body": "覆盖", "expect": stale}, headers=AUTH).json()
    assert "agent 写的" in body["current"]["body"], "409 必须把服务器当前的正文一起带回来"
    assert body["current"]["digest"] == body["digest"] != stale
    assert body["expected"] == stale


def test_a_write_without_expect_still_goes_through(client):
    """agent 的追加式写入不带 expect，旧行为必须原样保留。"""
    mkstep(client, title="004", body="base")
    client.patch("/api/p/课题/steps/001", json={"body": "别人改的"}, headers=AUTH)
    r = client.patch("/api/p/课题/steps/001", json={"body": "我也改"}, headers=AUTH)
    assert r.status_code == 200


def test_if_match_header_is_accepted_as_the_baseline_too(client):
    """curl / agent 更习惯 HTTP 自己的 If-Match，网页更习惯 JSON 字段，两条都收。"""
    mkstep(client, title="004", body="base")
    good = client.get("/api/p/课题/steps/001").json()["digest"]
    ok = client.patch("/api/p/课题/steps/001", json={"body": "一"},
                      headers={**AUTH, "If-Match": f'"{good}"'})
    assert ok.status_code == 200
    bad = client.patch("/api/p/课题/steps/001", json={"body": "二"},
                       headers={**AUTH, "If-Match": good})   # 现在磁盘上已经是别的了
    assert bad.status_code == 409


def test_a_patch_returns_the_digest_of_what_it_just_wrote(client):
    """回旧指纹的话，客户端连着存两次就会被自己刚写的东西判成冲突。"""
    mkstep(client, title="004", body="base")
    fresh = client.patch("/api/p/课题/steps/001", json={"body": "第一次"}, headers=AUTH).json()["digest"]
    on_disk = hashlib.sha256(note_of(client, "001").read_bytes()).hexdigest()[:12]
    assert fresh == on_disk
    assert client.patch("/api/p/课题/steps/001",
                        json={"body": "第二次", "expect": fresh}, headers=AUTH).status_code == 200


def test_a_new_step_comes_back_with_a_usable_digest(client):
    r = client.post("/api/p/课题/steps", json={"title": "新建的"}, headers=AUTH)
    sid = r.json()["id"]
    assert r.json()["digest"] == hashlib.sha256(note_of(client, sid).read_bytes()).hexdigest()[:12]


def test_expect_never_becomes_an_unsupported_field(client):
    """expect 是并发控制参数，不是可写字段；漏进 patch 会变成 400「不支持的字段」。"""
    mkstep(client, title="004")
    good = client.get("/api/p/课题/steps/001").json()["digest"]
    assert client.patch("/api/p/课题/steps/001",
                        json={"expect": good}, headers=AUTH).status_code == 200


# ------------------------------------------------------- 文件系统错误


def boom(exc):
    def raise_it(*_a, **_kw):
        raise exc
    return raise_it


def test_a_locked_file_is_not_reported_as_a_missing_token(client, monkeypatch):
    """曾经的雷：require_token 抛的是 PermissionError，而它是 OSError 的子类。

    于是「文件被杀毒软件/同步盘占用」会被报成 401「需要写入令牌」，
    agent 跑去重新找令牌，而真正的问题在磁盘上。
    """
    exc = PermissionError(errno.EACCES, "另一个程序正在使用此文件", "note.md", 32)
    monkeypatch.setattr(S.W, "update_step", boom(exc))
    mkstep(client, title="x")
    r = client.patch("/api/p/课题/steps/001", json={"status": "done"}, headers=AUTH)
    assert r.status_code != 401, "磁盘写不进去和令牌不对是两回事"
    assert r.status_code != 500
    assert r.json()["kind"] in ("locked", "permission")
    assert r.json()["written"] is False


def test_a_full_disk_says_it_is_full_and_that_nothing_was_written(client, monkeypatch):
    monkeypatch.setattr(S.W, "update_step", boom(OSError(errno.ENOSPC, "No space left on device")))
    mkstep(client, title="x")
    r = client.patch("/api/p/课题/steps/001", json={"status": "done"}, headers=AUTH)
    assert r.status_code == 507
    assert r.json()["kind"] == "disk_full"
    assert "磁盘满" in r.json()["error"] and "没有发生" in r.json()["error"]


def test_a_name_too_long_is_the_callers_problem_not_a_500(client, monkeypatch):
    """agent 拿到 500 只会读成"服务器坏了"，正确的读法是"换个短名字重试"。"""
    monkeypatch.setattr(S.W, "attach_file",
                        boom(OSError(errno.ENAMETOOLONG, "File name too long", "x" * 300)))
    mkstep(client, title="x")
    r = client.put("/api/p/课题/steps/001/files/long.txt", content=b"x", headers=AUTH)
    assert r.status_code == 400 and r.json()["kind"] == "name_too_long"


def test_the_disk_error_never_leaks_the_server_path(client, monkeypatch):
    victim = client.root / "projects" / "课题" / "steps" / "001_x" / core.NOTE_NAME
    monkeypatch.setattr(S.W, "update_step", boom(OSError(errno.EROFS, "Read-only file system", str(victim))))
    mkstep(client, title="x")
    r = client.patch("/api/p/课题/steps/001", json={"status": "done"}, headers=AUTH)
    assert str(client.root) not in r.text, "返回体里不许出现服务器的绝对路径"
    assert "projects" not in r.text


# ------------------------------------------------------- 编译不占事件循环


def slow_compile(monkeypatch, seconds: float, counter: list):
    real = core.compile_forest

    def slow(*a, **kw):
        counter.append(1)
        time.sleep(seconds)
        return real(*a, **kw)

    monkeypatch.setattr(core, "compile_forest", slow)


def test_compiling_a_forest_does_not_freeze_the_event_loop(client, monkeypatch):
    """1000 步的项目一次编译要好几秒。同步跑在事件循环里的话，这几秒内
    SSE 心跳、别人的读写、反向代理的探活全部停摆。"""
    slow_compile(monkeypatch, 0.6, [])
    ticks: list[float] = []

    async def go():
        st = S.State(client.root)
        t0 = time.monotonic()

        async def heartbeat():
            for _ in range(4):
                await asyncio.sleep(0.02)
                ticks.append(time.monotonic() - t0)

        await asyncio.gather(st.forest("课题"), heartbeat())

    mkstep(client, title="x")
    asyncio.run(go())
    assert ticks and ticks[-1] < 0.4, f"编译期间事件循环停摆了：心跳落在 {ticks}"


def test_the_same_project_is_compiled_once_under_concurrent_requests(client, monkeypatch):
    """并发不该变成"每个请求各起一个线程算一遍同一棵树"。"""
    calls: list[int] = []
    slow_compile(monkeypatch, 0.2, calls)
    mkstep(client, title="x")

    async def go():
        st = S.State(client.root)
        out = await asyncio.gather(*[st.forest("课题") for _ in range(5)])
        return out

    forests = asyncio.run(go())
    assert len(calls) == 1, f"同一个项目被并发编译了 {len(calls)} 次"
    assert all(f["steps"] == forests[0]["steps"] for f in forests)


# ------------------------------------------------------- project.md 进指纹


def test_editing_project_md_bumps_the_version(client):
    """洞察存在 project.md 里。它不进指纹的话，SSE 永远不推，
    另一台机器上的洞察面板一直是旧的，只能等下一次步骤级改动搭便车。"""
    st = S.State(client.root)
    before = st.version
    assert st.refresh() is False

    note = core.project_dir(client.root, "课题") / core.PROJECT_NOTE
    note.write_text(note.read_text(encoding="utf-8") + "\n## 无效\n- 试过 X，不行\n", encoding="utf-8")

    assert st.refresh() is True, "改 project.md 必须让指纹变"
    assert st.version == before + 1


def test_writing_an_insight_through_the_api_bumps_the_version(client):
    before = client.get("/api/p/课题/forest").json()["version"]
    r = client.patch("/api/projects/课题",
                     json={"add_insight": {"kind": "fails", "text": "对比学习预训练没用"}}, headers=AUTH)
    assert r.status_code == 200
    assert client.get("/api/p/课题/forest").json()["version"] != before


def test_refresh_reports_which_project_changed(client):
    """git 自动同步要知道是**哪个**项目动了，才能写出有意义的 commit message。"""
    W.create_project(client.root, "第二个")
    st = S.State(client.root)
    assert st.refresh_changed() == []
    mkstep(client, title="只动了课题")
    assert st.refresh_changed() == ["课题"]


# ------------------------------------------------------- 同步由文件变化驱动


class FakeGit:
    """只记下"谁让我同步"，不真的碰 git。"""

    def __init__(self, root, **_kw):
        self.root = Path(root)
        self.enabled = True
        self.configured = True
        self.touched: list[str] = []
        self.last = {"state": "idle", "detail": "", "summary": "", "hint": "", "pushed": False, "at": ""}

    def touch(self, ids):
        self.touched.extend([ids] if isinstance(ids, str) else ids)

    def preflight(self):
        return self.last

    def status(self, *, detail: bool = False):
        return dict(self.last, ok=False, enabled=True)

    def commit_now(self, ids=None):
        return self.last


@pytest.fixture()
def poll_client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(S, "ROOT", tmp_path)
    monkeypatch.setattr(S, "POLL_SECONDS", 0.05)
    made: dict = {}

    def spy(root, **kw):
        made["git"] = FakeGit(root, **kw)
        return made["git"]

    monkeypatch.setattr(S, "GitSync", spy)
    app = S.create_app({"data_dir": ".", "space": "", "token": TOKEN, "git": {"enabled": True}})
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    with TestClient(app) as c:
        c.root = tmp_path
        c.sd = core.steps_dir_of(tmp_path, "课题")
        c.git = made["git"]
        yield c


def test_a_step_written_outside_the_api_still_gets_synced(poll_client):
    """P1 说文件才是真相，那么同步就该由**文件变化**驱动，不是由 API 调用驱动。

    以前只有 HTTP 写路由会触发同步，于是服务器上 vim 直接改的 note.md、
    role=server 的本地 MCP、trace_cli.py new/rm 写的步骤，全都进不了 git——
    而 deploy/README 把 git push 当成换机器和灾难恢复的唯一依据。
    """
    c = poll_client
    waited(lambda: c.git.touched)      # 建项目本身那一次
    c.git.touched.clear()

    W.create_step(c.sd, title="vim 直接写的")   # 完全不经过 HTTP

    got = waited(lambda: c.git.touched)
    assert got, "轮询发现文件变了却没有安排同步"
    assert any("课题" in x for x in got)


# ------------------------------------------------------- git 同步的可见性


def test_the_git_endpoint_exists_and_says_whether_the_last_sync_worked(client):
    d = client.get("/api/git").json()
    assert d["state"] == "disabled" and d["ok"] is False
    assert "summary" in d and "at" in d


def test_git_details_need_the_token_but_the_summary_does_not(tmp_path: Path, monkeypatch):
    """detail 是 git 原文，含数据仓路径和远端地址；而读接口是不要令牌的。"""
    monkeypatch.setattr(S, "ROOT", tmp_path)
    app = S.create_app({"data_dir": ".", "space": "", "token": TOKEN, "git": {"enabled": True}})
    core.ensure_layout(tmp_path)
    with TestClient(app) as c:
        public = c.get("/api/git").json()
        assert "detail" not in public and "root" not in public
        assert public["state"] == "misconfigured", "开着 git 却不是仓库 —— 用户以为有备份其实没有"
        assert public["hint"], "光说失败没用，要说照着做什么能修"
        private = c.get("/api/git", headers=AUTH).json()
        assert "detail" in private and "root" in private


def test_status_no_longer_hands_the_raw_git_output_to_anyone(client):
    g = client.get("/api/status").json()["git"]
    assert "detail" not in g and "summary" in g


@pytest.mark.parametrize(("out", "must_contain"), [
    ("*** Please tell me who you are.\nfatal: unable to auto-detect email address", "user.email"),
    ("git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.", "deploy key"),
    ("fatal: 'origin' does not appear to be a git repository", "git remote"),
    ("error: src refspec main does not match any", "git.branch"),
    ("! [rejected] main -> main (fetch first)", "git pull"),
    ("Host key verification failed.", "known_hosts"),
    ("fatal: could not read Username for 'https://github.com': No such device or address", "ssh"),
])
def test_every_known_push_failure_says_what_to_do(out, must_contain):
    """「error: ...」那一行原样丢给用户等于没说。第一次 push 炸掉的原因就那么几种。"""
    summary, hint = G.classify(out)
    assert summary, f"没认出来：{out}"
    assert must_contain in hint, f"提示里没有可操作的东西：{hint}"


@pytest.mark.skipif(shutil.which("git") is None, reason="这台机器上没有 git")
def test_a_data_repo_without_identity_or_remote_is_flagged_before_the_first_push(tmp_path: Path):
    """身份和 remote 都是装机时漏一步造成的静态错误，起服务的当下就能查出来，
    不必等 45 秒 debounce 之后在没人看着的时刻第一次 push 失败。"""
    repo = tmp_path / "data"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", ""], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", ""], cwd=repo, capture_output=True)

    g = G.GitSync(repo, enabled=True)
    assert g.enabled and g.last["state"] == "idle"
    st = g.preflight()
    assert st["state"] == "misconfigured"
    assert "user.email" in st["hint"] and "remote" in st["hint"]
    assert g.status()["ok"] is False


def test_a_sync_that_cannot_run_is_written_to_the_log(tmp_path: Path, caplog):
    """无声失败 = 用户以为自己有备份。至少要在服务端日志里留下痕迹。"""
    g = G.GitSync(tmp_path, enabled=True)          # 不是 git 仓库
    assert not g.enabled
    with caplog.at_level("WARNING", logger="trace.git"):
        g.touch(["课题/001"])
    assert any("git" in r.message or "git" in r.getMessage() for r in caplog.records)
    assert g.status()["ok"] is False


def test_a_push_failure_is_not_counted_as_a_successful_sync():
    """commit 成功、push 失败 = 文件还只在这一台机器上，而这条链路存在的意义
    正是"不只在这一台机器上"。它不能算成功。"""
    assert "committed" not in G.OK_STATES
    assert G.OK_STATES == {"pushed", "clean"}


# ------------------------------------------------------- 跨项目搜索


def test_search_spans_every_project_and_says_which_one_matched(client):
    """人这一侧以前完全没有跨项目搜索：项目索引页搜不了，项目内搜索只搜当前项目。
    同一个能力 agent 有（trace_search 不给 project 就搜全部），人没有。"""
    W.create_project(client.root, "另一个课题")
    other = core.steps_dir_of(client.root, "另一个课题")
    mkstep(client, title="这边的", body="## 结论\n对比学习预训练没提升，放弃")
    W.create_step(other, title="那边的", body="## 为什么\n想试试对比学习预训练")

    d = client.get("/api/search", params={"q": "对比学习"}).json()
    assert d["total"] == 2
    assert {h["project"] for h in d["hits"]} == {"课题", "另一个课题"}
    assert all(h["id"] and h["title"] for h in d["hits"])
    assert any("放弃" in h["snippet"] for h in d["hits"]), "命中要给出上下文，不然还得一条条点开"


def test_search_can_be_limited_to_one_project(client):
    W.create_project(client.root, "另一个课题")
    W.create_step(core.steps_dir_of(client.root, "另一个课题"), title="x", body="对比学习")
    mkstep(client, title="y", body="对比学习")
    d = client.get("/api/search", params={"q": "对比学习", "project": "课题"}).json()
    assert d["total"] == 1 and d["hits"][0]["project"] == "课题"


def test_search_matches_title_tags_and_id_case_insensitively(client):
    mkstep(client, title="MMseqs2 聚类", tags=["baseline"], body="正文无关")
    for q in ("mmseqs2", "BASELINE", "001"):
        assert client.get("/api/search", params={"q": q}).json()["total"] == 1, q


def test_search_reports_the_total_even_when_it_only_returns_a_page(client):
    for i in range(5):
        mkstep(client, title=f"重复词 {i}")
    d = client.get("/api/search", params={"q": "重复词", "limit": 2}).json()
    assert len(d["hits"]) == 2 and d["total"] == 5 and d["truncated"] is True


def test_searching_an_unknown_project_is_a_404_not_an_empty_result(client):
    """静默返回空结果会让人以为"确实没记过"，而真相是项目名打错了。"""
    assert client.get("/api/search", params={"q": "x", "project": "不存在"}).status_code == 404


def test_an_empty_query_matches_nothing_instead_of_everything(client):
    mkstep(client, title="x")
    assert client.get("/api/search", params={"q": "  "}).json()["total"] == 0


def test_search_also_answers_to_the_mcp_parameter_name(client):
    """MCP 那边的参数叫 query。照着 trace_search 抄的人不该拿到空结果还以为真没记过。"""
    mkstep(client, title="MMseqs2 聚类")
    assert client.get("/api/search", params={"query": "MMseqs2"}).json()["total"] == 1


# ------------------------------------------------------- 搜索要搜到译文


def test_search_finds_a_word_that_only_exists_in_the_translation(client):
    """底线是「删掉全部程序，grep -r 还能回答为什么放弃了 X」。双语之后英文的
    grep 也要能回答同一个问题——`grep -r abandoned` 命中 note.en.md 而站内搜索
    命中不了的话，自带的搜索就比 grep 弱，而「没搜到」会被读成「没试过」。"""
    mkstep(client, title="对比学习", body="## 结论\n没有提升，放弃这条路。")
    W.write_translation(client.sd, "001", "en",
                        title="Contrastive pretraining",
                        body="## Conclusion\nNo gain. This line is abandoned.")
    d = client.get("/api/search", params={"q": "abandoned"}).json()
    assert d["total"] == 1, "只有英文版里有的词也必须搜得到"
    hit = d["hits"][0]
    assert hit["langs"] == ["en"], "要说清命中落在哪个语言，不能是一条来路不明的命中"
    assert hit["snippet_lang"] == "en" and "abandoned" in hit["snippet"]
    assert "tr.body" in hit["where"]


def test_search_still_prefers_the_original_for_the_snippet(client):
    """原文是权威。两边都命中时给译文的摘要，会让同一条命中在不同语言下长得不一样。"""
    mkstep(client, title="x", body="## 结论\nMMseqs2 聚类之后再说")
    W.write_translation(client.sd, "001", "en", title="x", body="## Conclusion\nMMseqs2 later")
    hit = client.get("/api/search", params={"q": "MMseqs2"}).json()["hits"][0]
    assert "聚类" in hit["snippet"], "摘要该取自原文"
    assert hit["langs"] == ["en"], "但命中也落在英文版里这件事仍然要说出来"


def test_the_hit_shape_is_the_same_for_a_step_without_translations(client):
    """新加的两个字段对单语记录必须是空的，不是 None——前端只需要认一种形状。"""
    mkstep(client, title="只有中文", body="正文")
    hit = client.get("/api/search", params={"q": "只有中文"}).json()["hits"][0]
    assert hit["langs"] == [] and hit["snippet_lang"] == ""


# ------------------------------------------------------- 译文的读写端点


def tr_path(client, sid: str, lang: str) -> Path:
    return client.sd / W.load(client.sd)[sid].dirname / f"note.{lang}.md"


def test_writing_a_translation_does_not_touch_the_original(client):
    """整套双语设计的地基：译文和原文各写各的文件。这一条塌了，
    「立刻翻译」和「延迟翻译」就不再是同一条路径，原文也不再是唯一权威。"""
    mkstep(client, title="加入标题字段", body="## 为什么\n基线丢掉了词序")
    before = note_of(client, "001").read_bytes()
    r = client.put("/api/p/课题/steps/001/tr/en",
                   json={"title": "Add title field", "body": "## Why\nTF-IDF drops word order."},
                   headers=AUTH)
    assert r.status_code == 200 and r.json()["path"] == "note.en.md"
    assert note_of(client, "001").read_bytes() == before, "原文一个字节都不许动"
    assert "## Why" in tr_path(client, "001", "en").read_text(encoding="utf-8")


def test_the_forest_carries_the_translation_and_the_declared_language(client):
    """网页要能切语言，前提是首屏那份 forest 里就带着译文——否则每切一次语言
    都要按步骤各拉一次。core 已经算好了，这条钉的是「服务端确实透传了」。"""
    mkstep(client, title="中文标题", body="## 为什么\n因为")
    client.put("/api/p/课题/steps/001/tr/en", json={"title": "English title", "body": "## Why\nBecause"},
               headers=AUTH)
    s = client.get("/api/p/课题/forest").json()["steps"][0]
    assert s["tr"]["en"]["title"] == "English title"
    assert "Because" in s["tr"]["en"]["body"]
    assert s["lang"] == "", "note.md 没声明 lang 就是空串，绝不去猜"
    assert client.get("/api/p/课题/steps/001").json()["tr"]["en"]["title"] == "English title"


def test_dropping_a_translation_leaves_the_original_alone(client):
    """撤掉一个语言版本不是 P2 的例外：译文是原文的衍生物，删掉之后界面回退到
    原文，一个事实都没丢——所以它也不该要求写原因。"""
    mkstep(client, title="x", body="## 为什么\n原文还在")
    client.put("/api/p/课题/steps/001/tr/en", json={"title": "x", "body": "## Why\nhere"}, headers=AUTH)
    r = client.request("DELETE", "/api/p/课题/steps/001/tr/en", headers=AUTH)
    assert r.status_code == 200 and r.json()["removed"] is True
    assert not tr_path(client, "001", "en").exists()
    assert "原文还在" in note_of(client, "001").read_text(encoding="utf-8")
    assert client.request("DELETE", "/api/p/课题/steps/001/tr/en",
                          headers=AUTH).status_code == 404, "已经没有的语言版本要 404"


def test_a_stale_translation_save_is_rejected(client):
    """译文自己也会被两个人同时改（网页一边、agent 一边），一样要挡。"""
    mkstep(client, title="x")
    first = client.put("/api/p/课题/steps/001/tr/en", json={"body": "## Why\nv1"},
                       headers=AUTH).json()["digest"]
    client.put("/api/p/课题/steps/001/tr/en", json={"body": "## Why\nv2"}, headers=AUTH)
    r = client.put("/api/p/课题/steps/001/tr/en",
                   json={"body": "## Why\nv3", "expect": first}, headers=AUTH)
    assert r.status_code == 409
    assert "v2" in tr_path(client, "001", "en").read_text(encoding="utf-8"), "被拒的写入不许改磁盘"


def test_editing_the_original_does_not_invalidate_the_translations_expect(client):
    """两条链各管各的。译文的 expect 要是对着 note.md 的 digest，那么每改一次正文，
    所有语言的译文都得先重读一遍才写得进去——而正文和译文常常是两个人在改。"""
    mkstep(client, title="x", body="v1")
    d = client.put("/api/p/课题/steps/001/tr/en", json={"body": "## Why\nen"},
                   headers=AUTH).json()["digest"]
    client.patch("/api/p/课题/steps/001", json={"body": "v2 正文改过了"}, headers=AUTH)
    assert client.put("/api/p/课题/steps/001/tr/en",
                      json={"body": "## Why\nen2", "expect": d}, headers=AUTH).status_code == 200


def test_a_bad_language_code_is_refused_instead_of_becoming_a_filename(client):
    """lang 直接变成文件名的一段。放过 ../ 就是任意写；放过结尾的点，Windows 会把它
    剥掉，于是 `en.` 悄悄改写的是 note.en.md。"""
    mkstep(client, title="x")
    # 路由层就接不住带斜杠的（%2F 解不出一段合法路径），剩下的由 norm_lang 拦成 400
    assert client.put("/api/p/课题/steps/001/tr/..%2F..%2Fevil",
                      json={"title": "x"}, headers=AUTH).status_code == 404
    for junk in ("en.", "md", "CON"):
        r = client.put(f"/api/p/课题/steps/001/tr/{junk}", json={"title": "x"}, headers=AUTH)
        assert r.status_code == 400, f"{junk} 不该被当成语言码收下"
    assert not (client.root / "evil.md").exists()
    assert not list((client.sd / W.load(client.sd)["001"].dirname).glob("note.*.md"))


def test_the_project_note_translation_lands_next_to_project_md(client):
    r = client.put("/api/p/课题/tr/en",
                   json={"name": "My topic", "body": "## Works\n- dedup helps"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["path"] == "project.en.md"
    text = (core.project_dir(client.root, "课题") / "project.en.md").read_text(encoding="utf-8")
    assert "name: My topic" in text and "## Works" in text
    assert "id:" not in text, "译文里只准有 name，结构键连写都不写"


def test_a_translation_bumps_the_version_so_open_pages_refresh(client):
    mkstep(client, title="x")
    before = client.get("/api/p/课题/forest").json()["version"]
    client.put("/api/p/课题/steps/001/tr/en", json={"title": "x"}, headers=AUTH)
    assert client.get("/api/p/课题/forest").json()["version"] != before


# ------------------------------------------------------- 还欠哪些翻译


def test_untranslated_is_public_and_lists_what_is_missing(client):
    """读一律公开，和 forest / search 一致。"""
    mkstep(client, title="第一步")
    mkstep(client, title="第二步")
    client.put("/api/p/课题/steps/001/tr/en", json={"title": "First"}, headers=AUTH)

    d = client.get("/api/p/课题/untranslated").json()          # 不带令牌
    assert d["lang"] == "en" and d["total"] == 2 and d["translated"] == 1
    assert [m["id"] for m in d["missing"]] == ["002"]
    assert d["missing"][0]["title"] == "第二步", "光给 id 的话还得再查一遍才知道要翻什么"
    assert d["project_note"]["missing"] is True


def test_untranslated_does_not_ask_you_to_translate_a_step_that_is_already_english(client):
    """原文自己声明了 lang: en 时，写同语言译文会被 trace_write 拒绝。
    把它列进「还缺」等于派 agent 去做一件必然失败的事。"""
    mkstep(client, title="English step")
    note = note_of(client, "001")
    note.write_text(note.read_text(encoding="utf-8").replace("status:", "lang: en\nstatus:", 1),
                    encoding="utf-8")
    d = client.get("/api/p/课题/untranslated", params={"lang": "en"}).json()
    assert d["missing"] == [] and d["native"] == 1


def test_untranslated_normalizes_the_language_code(client):
    """EN 和 en 是同一个文件（NTFS 上真的是同一个）。不归一化的话，传 EN 会得到
    「一份译文都没有」——一个必然让人把所有东西重翻一遍的假答案。"""
    mkstep(client, title="x")
    client.put("/api/p/课题/steps/001/tr/en", json={"title": "x"}, headers=AUTH)
    d = client.get("/api/p/课题/untranslated", params={"lang": "EN"}).json()
    assert d["lang"] == "en" and d["translated"] == 1 and d["missing"] == []


def test_untranslated_rejects_a_junk_language_code(client):
    assert client.get("/api/p/课题/untranslated", params={"lang": "../x"}).status_code == 400


def test_untranslated_on_a_missing_project_is_a_404(client):
    assert client.get("/api/p/没有这个/untranslated").status_code == 404
