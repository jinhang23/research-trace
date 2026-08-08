"""MCP 工具层的断言。

分两层：
  * 工具处理函数直接调（不经过协议），覆盖行为；
  * 最后一个用例真的起一个子进程，走 JSON-RPC 握手 → tools/list → tools/call，
    确认协议层没接错。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import trace_mcp as M

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def be(tmp_path: Path):
    backend = M.LocalBackend(tmp_path)
    import trace_write as W

    W.create_project(tmp_path, "alpha")
    return backend


def call(be, tool, **args):          # 参数名不能叫 name —— 工具本身有 name 参数
    return M.dispatch(be, tool, args)


# ------------------------------------------------------------ 读


def test_projects_lists_counts(be):
    call(be, "trace_new_step", project="alpha", title="第一步", status="done")
    out = call(be, "trace_projects")
    assert "alpha" in out and "1 步" in out and "done 1" in out


def test_read_renders_an_indented_tree(be):
    call(be, "trace_new_step", project="alpha", title="根")
    call(be, "trace_new_step", project="alpha", parent="001", title="主线")
    call(be, "trace_new_step", project="alpha", parent="001", title="旁支", status="dead")
    out = call(be, "trace_read", project="alpha")
    lines = [l for l in out.splitlines() if "  0" in l or l.strip().startswith(("●", "○", "▣"))]
    assert any(l.startswith("○ 001") for l in lines), out
    assert any(l.startswith("  ○ 002") for l in lines), "子步骤要缩进一层"
    assert any(l.startswith("  ▣ 002b") for l in lines), "dead 用 ▣"


def test_read_a_single_step_shows_lineage(be):
    call(be, "trace_new_step", project="alpha", title="根")
    call(be, "trace_new_step", project="alpha", parent="001", title="中间")
    call(be, "trace_new_step", project="alpha", parent="002", title="末端", body="## 为什么\n因为要试试")
    out = call(be, "trace_read", project="alpha", step="003")
    assert "溯源: 001 → 002 → 003" in out
    assert "因为要试试" in out


def test_read_unknown_step_errors(be):
    with pytest.raises(M.ToolError):
        call(be, "trace_read", project="alpha", step="999")


def test_search_finds_body_only_words(be):
    call(be, "trace_new_step", project="alpha", title="试 focal loss", status="dead",
         body="## 结论\n正样本太少，MMseqs2 聚类之后再说。放弃这条路。")
    out = call(be, "trace_search", query="MMseqs2")
    assert "alpha/001" in out and "放弃这条路" in out


def test_search_reports_nothing_found(be):
    assert "没有搜到" in call(be, "trace_search", query="不存在的词")


# ------------------------------------------------------------ 写


def test_new_step_defaults_to_wip_and_agent_author(be):
    out = call(be, "trace_new_step", project="alpha", title="开跑")
    assert "[wip]" in out
    assert be.step("alpha", "001")["author"] == M.DEFAULT_AUTHOR


def test_new_step_nags_when_the_body_is_still_the_template(be):
    """「为什么」是唯一无法自动生成的字段，光建个空壳没有意义。"""
    assert "⚠" in call(be, "trace_new_step", project="alpha", title="空壳")
    assert "⚠" not in call(be, "trace_new_step", project="alpha", title="写了的",
                           body="## 为什么\n上一步发现验证集有重复样本，先确认污染比例。")


def test_idempotency_key_prevents_duplicates(be):
    call(be, "trace_new_step", project="alpha", title="扫参", key="sweep-1")
    out = call(be, "trace_new_step", project="alpha", title="扫参重试", key="sweep-1")
    assert "已存在同 key" in out
    assert len(be.forest("alpha")["steps"]) == 1


def test_update_append_keeps_the_existing_body(be):
    call(be, "trace_new_step", project="alpha", title="x", body="## 为什么\n先建着")
    call(be, "trace_update_step", project="alpha", step="001", status="done",
         append="## 结果\n准确率 0.951。")
    s = be.step("alpha", "001")
    assert s["status"] == "done"
    assert "先建着" in s["body"] and "0.951" in s["body"]


def test_update_rejects_body_and_append_together(be):
    call(be, "trace_new_step", project="alpha", title="x")
    with pytest.raises(M.ToolError):
        call(be, "trace_update_step", project="alpha", step="001", body="a", append="b")


def test_update_cannot_change_parent(be):
    """只追加原则要一路守到 MCP 这层。"""
    call(be, "trace_new_step", project="alpha", title="根")
    call(be, "trace_new_step", project="alpha", parent="001", title="子")
    with pytest.raises(M.ToolError):
        M.dispatch(be, "trace_update_step", {"project": "alpha", "step": "002", "status": "done", "parent": None})


def test_update_with_nothing_to_change_errors(be):
    call(be, "trace_new_step", project="alpha", title="x")
    with pytest.raises(M.ToolError):
        call(be, "trace_update_step", project="alpha", step="001")


# ------------------------------------------------------------ 附件


def test_attach_text_content(be):
    call(be, "trace_new_step", project="alpha", title="x")
    out = call(be, "trace_attach", project="alpha", step="001",
               text="epoch\tloss\n1\t0.42\n", name="train.log")
    assert "train.log" in out
    assert [f["path"] for f in be.step("alpha", "001")["files"]] == ["train.log"]


def test_attach_from_a_local_path(be, tmp_path: Path):
    call(be, "trace_new_step", project="alpha", title="x")
    p = tmp_path / "run.sh"
    p.write_text("python train.py --seed 0\n", encoding="utf-8")
    call(be, "trace_attach", project="alpha", step="001", path=str(p))
    assert [f["path"] for f in be.step("alpha", "001")["files"]] == ["run.sh"]


def test_image_without_a_caption_is_refused(be):
    """agent 看不到图里的内容，图注是唯一的信息来源——所以这里是硬拒绝。"""
    call(be, "trace_new_step", project="alpha", title="x")
    with pytest.raises(M.ToolError, match="caption"):
        call(be, "trace_attach", project="alpha", step="001", text="fake", name="loss.png")


def test_image_with_a_caption_is_inserted_into_the_body(be):
    call(be, "trace_new_step", project="alpha", title="x", body="## 结果")
    call(be, "trace_attach", project="alpha", step="001", text="fake", name="loss.png",
         caption="第 12 轮之后验证集回升，再往后是纯过拟合")
    body = be.step("alpha", "001")["body"]
    assert '![](loss.png "第 12 轮之后验证集回升，再往后是纯过拟合")' in body


def test_attach_text_without_a_name_errors(be):
    call(be, "trace_new_step", project="alpha", title="x")
    with pytest.raises(M.ToolError):
        call(be, "trace_attach", project="alpha", step="001", text="内容")


def test_attach_needs_path_or_text(be):
    call(be, "trace_new_step", project="alpha", title="x")
    with pytest.raises(M.ToolError):
        call(be, "trace_attach", project="alpha", step="001", name="a.txt")


def test_unknown_tool_errors(be):
    with pytest.raises(M.ToolError):
        M.dispatch(be, "trace_delete_everything", {})


def test_backend_requires_configuration(monkeypatch):
    monkeypatch.delenv("TRACE_URL", raising=False)
    monkeypatch.delenv("TRACE_DATA", raising=False)
    with pytest.raises(M.ToolError, match="TRACE_URL"):
        M.make_backend()


# ------------------------------------------------------------ 协议冒烟


# ------------------------------------------------------------ 参数校验
# 官方 SDK 会按 inputSchema 校验参数；自己说协议就得自己做这件事。


def test_required_arguments_are_enforced():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    for bad in ({}, {"a": ""}, {"a": None}):
        with pytest.raises(M.ToolError, match="必填"):
            M.validate_args(schema, bad)
    M.validate_args(schema, {"a": "x"})


def test_unknown_argument_is_rejected():
    with pytest.raises(M.ToolError, match="不认识的参数"):
        M.validate_args({"type": "object", "properties": {"a": {"type": "string"}}}, {"b": 1})


def test_types_enums_and_array_items_are_checked():
    schema = {"type": "object", "properties": {
        "n": {"type": "number"},
        "s": {"type": "string", "enum": ["wip", "done"]},
        "xs": {"type": "array", "items": {"type": "string"}},
    }}
    with pytest.raises(M.ToolError, match="应当是 number"):
        M.validate_args(schema, {"n": "十"})
    with pytest.raises(M.ToolError, match="只能是"):
        M.validate_args(schema, {"s": "success"})
    with pytest.raises(M.ToolError, match="每一项"):
        M.validate_args(schema, {"xs": ["a", 2]})
    M.validate_args(schema, {"n": 1.5, "s": "done", "xs": ["a", "b"]})


def test_every_tool_schema_is_self_consistent():
    for t in M.TOOLS:
        props = t["inputSchema"].get("properties", {})
        for req in t["inputSchema"].get("required", []):
            assert req in props, f"{t['name']} 的 required 里有未声明的 {req}"
        assert t["description"].strip(), t["name"]


# ------------------------------------------------------------ 协议层（纯函数）


def test_initialize_echoes_a_supported_protocol_version():
    s = M.Session()
    r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18"}}, s)
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert r["result"]["serverInfo"]["name"] == "trace"
    assert r["result"]["capabilities"]["tools"] is not None
    assert "为什么" in r["result"]["instructions"]


def test_unknown_protocol_version_falls_back_to_ours():
    r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "1999-01-01"}}, M.Session())
    assert r["result"]["protocolVersion"] == M.PROTOCOL_VERSIONS[0]


def test_notifications_get_no_response():
    s = M.Session()
    assert M.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, s) is None
    assert M.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}, s) is None
    assert M.handle({"jsonrpc": "2.0", "method": "谁知道这是什么"}, s) is None


def test_ping_and_tools_list():
    s = M.Session()
    assert M.handle({"jsonrpc": "2.0", "id": 1, "method": "ping"}, s)["result"] == {}
    tools = M.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, s)["result"]["tools"]
    assert [t["name"] for t in tools] == [t["name"] for t in M.TOOLS]
    assert all("inputSchema" in t for t in tools)


def test_unknown_method_is_a_jsonrpc_error():
    r = M.handle({"jsonrpc": "2.0", "id": 9, "method": "resources/list"}, M.Session())
    assert r["error"]["code"] == -32601


def test_tool_failure_comes_back_as_isError_not_a_jsonrpc_error(tmp_path: Path):
    """工具层失败要让模型看得到并能改；JSON-RPC 的 error 留给协议层问题。"""
    s = M.Session()
    s.backend = M.LocalBackend(tmp_path)
    r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "trace_read", "arguments": {"project": "不存在"}}}, s)
    assert "error" not in r
    assert r["result"]["isError"] is True
    assert r["result"]["content"][0]["type"] == "text"


def test_unknown_tool_is_a_jsonrpc_error():
    r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "rm_rf", "arguments": {}}}, M.Session())
    assert r["error"]["code"] == -32602


def test_garbage_message_does_not_crash():
    assert M.handle("我不是对象", M.Session())["error"]["code"] == -32600
    assert M.handle({"jsonrpc": "2.0", "id": 1}, M.Session())["error"]["code"] == -32600


# ------------------------------------------------------------ 完整读写循环


def test_serve_stdio_full_loop(tmp_path: Path):
    """把整个 stdio 循环喂一遍，不起子进程。"""
    import io
    import trace_write as W

    M.LocalBackend(tmp_path)
    W.create_project(tmp_path, "alpha")
    os.environ["TRACE_DATA"] = str(tmp_path)
    os.environ.pop("TRACE_URL", None)

    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},          # 通知 → 不回
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "trace_new_step",
                    "arguments": {"project": "alpha", "title": "循环里建的",
                                  "body": "## 为什么\n验证协议链路。"}}},
    ]
    sin = io.StringIO("\n".join(json.dumps(x) for x in lines) + "\n")
    sout = io.StringIO()
    M.serve_stdio(sin, sout)

    out = [json.loads(l) for l in sout.getvalue().splitlines() if l.strip()]
    assert [r["id"] for r in out] == [1, 2, 3], "通知不该产生回应"
    assert "alpha/001" in out[2]["result"]["content"][0]["text"]
    assert (tmp_path / "projects" / "alpha" / "steps").is_dir()


def test_bad_json_line_gets_a_parse_error_and_the_loop_continues():
    import io

    sin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n这不是 JSON\n'
                      '{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
    sout = io.StringIO()
    M.serve_stdio(sin, sout)
    out = [json.loads(l) for l in sout.getvalue().splitlines() if l.strip()]
    assert [r.get("id") for r in out] == [1, None, 2]
    assert out[1]["error"]["code"] == -32700


def test_output_is_pure_ascii(tmp_path: Path):
    """输出全部转义成 ASCII，Windows 的终端编码就影响不到协议通道。"""
    import io

    M.LocalBackend(tmp_path)
    import trace_write as W
    W.create_project(tmp_path, "中文项目名")
    os.environ["TRACE_DATA"] = str(tmp_path)
    os.environ.pop("TRACE_URL", None)

    sin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "trace_projects", "arguments": {}}}) + "\n")
    sout = io.StringIO()
    M.serve_stdio(sin, sout)
    text = sout.getvalue()
    assert text.isascii(), "协议输出里不该有非 ASCII 字节"
    assert "中文项目名" in json.loads(text)["result"]["content"][0]["text"]


# ------------------------------------------------------------ 子进程 + 官方 SDK 互操作


def test_stdio_subprocess_handshake(tmp_path: Path):
    """真起一个子进程走完握手——验证 stdout 没有被别的输出污染。"""
    import trace_write as W

    M.LocalBackend(tmp_path)
    W.create_project(tmp_path, "alpha")
    env = dict(os.environ, TRACE_DATA=str(tmp_path), PYTHONIOENCODING="utf-8")
    env.pop("TRACE_URL", None)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "trace_mcp.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, text=True, encoding="utf-8", bufsize=1,
    )
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-06-18"}}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert line, "没有回应，stderr: " + proc.stderr.read()[:500]
        assert json.loads(line)["result"]["serverInfo"]["name"] == "trace"
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=10)


def test_interop_with_the_official_sdk_client(tmp_path: Path):
    """自己说协议就有说错的风险，所以拿官方 SDK 的客户端连上来跑一遍。

    这是**测试时**的依赖，运行时零依赖。SDK 不在就跳过。
    """
    pytest.importorskip("mcp", reason="装了官方 SDK 才能跑互操作验证")
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    import trace_write as W

    M.LocalBackend(tmp_path)
    W.create_project(tmp_path, "alpha")
    env = dict(os.environ, TRACE_DATA=str(tmp_path), PYTHONIOENCODING="utf-8")
    env.pop("TRACE_URL", None)

    async def go():
        params = StdioServerParameters(command=sys.executable,
                                       args=[str(ROOT / "trace_mcp.py")], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                ok = await session.call_tool("trace_new_step", {
                    "project": "alpha", "title": "官方客户端建的",
                    "body": "## 为什么\n验证互操作。"})
                bad = await session.call_tool("trace_read", {"project": "没有这个项目"})
                return init, tools, ok, bad

    init, tools, ok, bad = asyncio.run(go())

    assert init.serverInfo.name == "trace"
    assert init.instructions and "为什么" in init.instructions
    assert [t.name for t in tools.tools] == [t["name"] for t in M.TOOLS]
    assert tools.tools[0].inputSchema is not None
    assert not ok.isError and "alpha/001" in ok.content[0].text
    assert bad.isError, "工具层失败应当被客户端识别成 isError"
    assert (tmp_path / "projects" / "alpha" / "steps").is_dir()


# ------------------------------------------------------------ 数据仓路径体检
# 「填错一个字符 → 凭空造出一棵合法的空树 → 自检还报全部通过」是这套系统里最贵的
# 一个静默失败：用户在假树上记几十步之后才发现老项目一个都看不见。
# 难点在于「首次全新安装」和「路径打岔」在磁盘上是**一模一样**的观测，所以修法
# 不是禁止创建（禁了就挡住正常首装），而是把状态说出来。


def test_a_real_data_repo_is_recognised_without_noise(tmp_path: Path):
    (tmp_path / "projects").mkdir()
    root, state, note = M.check_data_root(tmp_path)
    assert state == M.DATA_ROOT_READY and note == ""


def test_an_empty_directory_is_initialisable_but_announced(tmp_path: Path):
    _, state, note = M.check_data_root(tmp_path)
    assert state == M.DATA_ROOT_EMPTY
    assert note, "空目录必须出声——它和「路径打岔了」在磁盘上长得一模一样"


def test_a_missing_directory_is_still_allowed_because_first_install_needs_it(tmp_path: Path):
    _, state, note = M.check_data_root(tmp_path / "还没建")
    assert state == M.DATA_ROOT_ABSENT and note


def test_a_directory_full_of_other_stuff_is_flagged(tmp_path: Path):
    (tmp_path / "论文.docx").write_text("x", encoding="utf-8")
    _, state, note = M.check_data_root(tmp_path)
    assert state == M.DATA_ROOT_OCCUPIED
    assert "论文.docx" in note and "填错" in note


def test_sibling_data_repos_are_offered_as_the_thing_you_probably_meant(tmp_path: Path):
    (tmp_path / "trace-data" / "projects").mkdir(parents=True)
    _, _, note = M.check_data_root(tmp_path / "trace-dataa")
    assert "trace-data" in note and "确定不是想指" in note


def test_a_path_whose_parent_is_missing_is_refused_as_a_typo(tmp_path: Path):
    """「目录不存在会自动建」只对下一级成立。整条路径都不存在 = 路径写错了。"""
    with pytest.raises(M.ToolError, match="路径写错"):
        M.check_data_root(tmp_path / "没这层" / "也没这层" / "仓")


def test_an_unexpanded_config_template_is_refused(tmp_path: Path):
    """实测过：宿主没展开 ${user_config.data_dir} 时，会在 cwd 下真建出一个同名幽灵目录。"""
    with pytest.raises(M.ToolError, match="模板"):
        M.check_data_root("${user_config.data_dir}")


def test_dotfiles_do_not_count_as_content(tmp_path: Path):
    """点开头的 .git / .trace-lock 在的空数据仓仍然是空数据仓，不该被当成「有别的东西」。"""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".trace-lock").write_bytes(b"")
    _, state, _ = M.check_data_root(tmp_path)
    assert state == M.DATA_ROOT_EMPTY


def test_the_local_backend_remembers_that_it_just_created_the_repo(tmp_path: Path):
    """自检要能说出「这棵树是我刚给你造的」，靠的就是这个状态。"""
    backend = M.LocalBackend(tmp_path / "全新")
    assert backend.root_state == M.DATA_ROOT_ABSENT and backend.root_note
    assert (backend.root / "projects").is_dir(), "说归说，该建还是要建"


def test_selfcheck_says_out_loud_that_the_tree_is_brand_new(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("TRACE_ROLE", "server")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path / "打岔了"))
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "无.json"))
    assert M.selfcheck() == 0, "全新安装不能判失败——否则就挡住了正常的第一次初始化"
    out = capsys.readouterr().out
    assert "全新的空仓" in out and "填错" in out


# ------------------------------------------------------------ 自检要验写
# 服务端的读路由不要令牌，所以只验读的自检在「令牌漏填」时会满屏对勾 + 「全部通过」，
# 直到 agent 真正开始记录、第一次写入撞上 401 —— 最不该卡住的那个时刻。


class FakeHttp(M.HttpBackend):
    """按脚本回答的假远端。只用来测 probe_write 的判断逻辑，不碰真网络。"""

    def __init__(self, protected=True, on_patch=None):
        super().__init__("https://例子/t/s", "token")
        self.protected, self.on_patch, self.calls = protected, on_patch, []

    def _call(self, method, path, payload=None, raw=None, headers=None):
        self.calls.append((method, path))
        if path == "/api/status":
            return {"write_protected": self.protected}
        if self.on_patch:
            raise M.ToolError(self.on_patch)
        return {}


def test_write_probe_reports_a_missing_token_instead_of_passing():
    backend = FakeHttp(on_patch="401 需要写入令牌：Authorization: Bearer <token>")
    good, detail = M.probe_write(backend)
    assert not good
    assert "401" in detail and "令牌" in detail, "要说清后果：读没事，写才炸"


def test_write_probe_accepts_a_404_because_that_means_the_token_got_through():
    good, _ = M.probe_write(FakeHttp(on_patch="404 项目不存在"))
    assert good


def test_write_probe_never_creates_anything_on_the_remote():
    """探针必须打在一个必然不存在的项目上——自检不该在任何人的记录里留下一个字节。"""
    backend = FakeHttp(on_patch="404 不存在")
    M.probe_write(backend)
    assert all(m in ("GET", "PATCH") for m, _ in backend.calls), "只许 GET 和一个注定 404 的 PATCH"
    assert any(M.WRITE_PROBE in p for _, p in backend.calls)


def test_write_probe_says_so_when_the_server_has_no_token_at_all():
    good, detail = M.probe_write(FakeHttp(protected=False))
    assert good and "谁都能写" in detail


def test_write_probe_on_a_local_repo_leaves_no_droppings(tmp_path: Path):
    before = sorted(p.name for p in tmp_path.iterdir())
    good, _ = M.probe_write(M.LocalBackend(tmp_path))
    assert good
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(before + ["projects"])


def test_selfcheck_checks_writing_not_just_reading(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("TRACE_ROLE", "server")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "无.json"))
    assert M.selfcheck() == 0
    assert "写入" in capsys.readouterr().out, "只验读的自检会对漏填令牌给出假阳性"


# ------------------------------------------------------------ 插件配置的可见性
# 插件的 TRACE_* 只灌给 MCP 子进程，不进 shell。在一台**配好了的**机器上跑自检
# 会报「没有配置后端」——假阴性，正砸在「换机器要能自证接通」这条需求上。


def test_selfcheck_explains_why_it_cannot_see_the_plugin_config(monkeypatch, capsys, tmp_path: Path):
    for k in ("TRACE_ROLE", "TRACE_URL", "TRACE_TOKEN", "TRACE_DATA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "无.json"))
    M.selfcheck()
    out = capsys.readouterr().out
    assert "子进程" in out, "要说清为什么看不见，否则用户会去改一份本来正确的配置"
    assert "trace_projects" in out, "要给出能看到真值的那条路"
    assert "--role" in out and "--data" in out, "要给出能把 /plugin 里的值喂进来的命令"


def test_selfcheck_flags_feed_straight_into_the_real_make_backend(monkeypatch, tmp_path: Path):
    """从 /plugin 抄出来的值必须走和运行时逐字相同的那条路，否则验了也不算数。"""
    # 用 setenv 而不是 delenv 打底：apply_selfcheck_flags 直接改 os.environ，
    # 只有被 monkeypatch 记录过的键才会在用例结束时被还原，否则会污染后面的用例。
    for k in ("TRACE_ROLE", "TRACE_URL", "TRACE_DATA", "TRACE_TOKEN"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "无.json"))
    rest = M.apply_selfcheck_flags(["--role", "server", f"--data={tmp_path}", "别的"])
    assert rest == ["别的"]
    assert os.environ["TRACE_ROLE"] == "server"
    assert isinstance(M.make_backend(), M.LocalBackend)


def test_selfcheck_spells_out_what_the_role_actually_does(tmp_path: Path, monkeypatch, capsys):
    """role=server 会**丢掉**远端地址，不是「远端优先」。选错的症状是读到另一头的数据。"""
    monkeypatch.setenv("TRACE_ROLE", "server")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "无.json"))
    M.selfcheck()
    out = capsys.readouterr().out
    assert "忽略" in out, "要说清另一项会被丢掉"
    assert "改角色" in out and "TRACE_ROLE" in out, "诊断工具得告诉人怎么改 role"


# ------------------------------------------------------------ 格式标准的送达
# README 主推的「只要 MCP」装法（pip install git+…）只打包三个 .py，那台机器上
# 根本不存在 FORMAT.md。而 initialize 的 instructions 是唯一无论怎么装都一定送达的通道。


@pytest.mark.parametrize("must", ["表格", "图注", "L0", "L4", "runnable", "verified",
                                  "结论", "假设", "最弱"])
def test_the_format_standard_travels_with_the_protocol_not_with_a_file(must):
    assert must in M.INSTRUCTIONS, f"instructions 里缺「{must}」——不装 skill 的 agent 就拿不到"


def test_instructions_do_not_point_at_a_file_that_may_not_exist():
    """原来写着「完整标准在插件根目录的 FORMAT.md」，pip 装的机器上那个文件不存在。"""
    if M._format_doc() is None:
        assert "FORMAT.md" not in M.INSTRUCTIONS
    else:
        assert str(M._format_doc()) in M.INSTRUCTIONS, "指路就要指到真实存在的绝对路径"


# ------------------------------------------------------------ date 由服务端生成
# 内核让 create_step 在调用方没给 date 时填服务端本地日期。MCP 这一侧要保证
# 不会传一个空 date 过去把它顶掉，而且远端和本地两条后端行为一致。


class Recorder:
    """只记录 payload 的假后端，用来看 MCP 到底往下传了什么。"""

    def __init__(self):
        self.payload = None

    def projects(self):
        return [{"slug": "alpha", "name": "alpha", "steps": 0,
                 "counts": {"wip": 0, "done": 0, "dead": 0}, "warnings": 0, "latest": ""}]

    def create(self, project, payload):
        self.payload = payload
        return {"id": "001", "status": "wip", "title": payload["title"], "created": True}


def test_mcp_never_sends_a_blank_date_downstream():
    """传空串会覆盖掉服务端刚填好的日期，等于把修复绕过去。"""
    rec = Recorder()
    M.dispatch(rec, "trace_new_step", {"project": "alpha", "title": "x", "date": ""})
    assert "date" not in rec.payload


def test_an_explicit_date_still_wins():
    rec = Recorder()
    M.dispatch(rec, "trace_new_step", {"project": "alpha", "title": "x", "date": "2020-01-02"})
    assert rec.payload["date"] == "2020-01-02"


def test_the_local_backend_fills_the_date_server_side(be):
    import datetime

    call(be, "trace_new_step", project="alpha", title="没给日期")
    out = call(be, "trace_read", project="alpha", step="001")
    assert f"date={datetime.datetime.now():%Y-%m-%d}" in out


def test_the_http_backend_gets_the_same_date_from_the_server(tmp_path: Path, monkeypatch):
    """远端那条路上填日期的是服务端。两条后端必须给出同一个结果，否则记录会分裂成两种。"""
    import datetime

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import trace_core as core
    import trace_server as S
    import trace_write as W

    monkeypatch.setattr(S, "ROOT", tmp_path)
    app = S.create_app({"data_dir": ".", "space": "", "token": "", "git": {"enabled": False}})
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "alpha")
    with TestClient(app) as c:
        r = c.post("/api/p/alpha/steps", json={"title": "没给日期"})
    assert r.json()["date"] == f"{datetime.datetime.now():%Y-%m-%d}"
