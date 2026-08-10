"""插件清单的断言。

清单是 JSON，写坏了插件装上就是坏的，而且坏在用户机器上、不在我这里。
所以它和代码之间的每一处耦合都要有测试盯着。
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

import trace_mcp as M

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
MARKET = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))


def test_manifests_are_valid_and_named_consistently():
    assert PLUGIN["name"] == "research-trace"
    assert [p["name"] for p in MARKET["plugins"]] == [PLUGIN["name"]]
    assert MARKET["plugins"][0]["version"] == PLUGIN["version"], "市场条目和清单的版本要一致"


def test_declared_component_directories_exist_and_are_not_empty():
    """指着空目录会让 Claude Code 在加载时报警告。"""
    for key in ("skills", "commands"):
        d = ROOT / PLUGIN[key].lstrip("./")
        assert d.is_dir(), f"{key} 指向的 {d} 不存在"
        assert any(d.iterdir()), f"{key} 指向的 {d} 是空的"
    assert (ROOT / "skills" / "research-trace" / "SKILL.md").is_file()


def test_plugin_never_points_at_something_that_is_not_there():
    """路径字段可以是目录字符串，也可以是文件数组（两种真实插件里都有）。"""
    for key in ("skills", "commands", "agents", "hooks", "outputStyles", "lspServers"):
        val = PLUGIN.get(key)
        if val is None:
            continue
        for rel in ([val] if isinstance(val, str) else val):
            target = ROOT / str(rel).lstrip("./")
            assert target.exists(), f"清单声明了 {key} → {rel}，但 {target} 不存在"
            if target.is_dir():
                assert any(target.iterdir()), f"{key} 指向的 {target} 是空目录，加载时会报警告"


# ------------------------------------------------------------ MCP server 声明


def test_mcp_server_points_at_a_file_that_exists():
    srv = PLUGIN["mcpServers"]["trace"]
    args = srv["args"]
    assert len(args) == 1
    rel = args[0].replace("${CLAUDE_PLUGIN_ROOT}/", "")
    assert (ROOT / rel).is_file(), f"清单指向的 {rel} 不在仓库里"


def test_mcp_server_only_sets_env_vars_the_code_actually_reads():
    """清单和 make_backend() 之间的耦合：改了一边忘了另一边，插件就会静默失灵。"""
    declared = set(PLUGIN["mcpServers"]["trace"]["env"])
    src = (ROOT / "trace_mcp.py").read_text(encoding="utf-8")
    read = set(re.findall(r'os\.environ(?:\.get)?[\(\[]"(TRACE_[A-Z_]+)"', src))
    assert declared <= read, f"清单设了但代码不读: {declared - read}"


def test_every_user_config_key_is_actually_substituted_somewhere():
    """声明了却没人用的配置项＝白问用户一次。"""
    keys = set(PLUGIN["userConfig"])
    blob = json.dumps(PLUGIN["mcpServers"], ensure_ascii=False)
    used = set(re.findall(r"\$\{user_config\.([A-Za-z_][A-Za-z0-9_]*)\}", blob))
    assert keys == used, f"声明了没用: {keys - used}；用了没声明: {used - keys}"


def test_user_config_entries_have_the_required_fields():
    for key, spec in PLUGIN["userConfig"].items():
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key), f"{key} 不是合法标识符"
        for field in ("type", "title", "description"):
            assert spec.get(field), f"{key} 缺 {field}"
        assert spec["type"] in ("string", "number", "boolean", "directory", "file"), spec["type"]
    assert PLUGIN["userConfig"]["token"].get("sensitive") is True, "令牌必须标 sensitive"


def test_python_default_is_python3_not_python():
    """`python` 在 Windows 上经常指向别的软件自带的 2.x。"""
    assert PLUGIN["userConfig"]["python"]["default"] == "python3"
    assert PLUGIN["mcpServers"]["trace"]["command"] == "${user_config.python}"


def test_the_python_field_tells_the_user_how_to_find_the_right_interpreter():
    """清单只能给一个静态默认值，探测不了当前环境 —— 所以说明必须能替代探测。

    用户的原话是「默认用当前环境下的」。装插件时唯一能拿到「当前环境」的人是用户自己，
    所以这里的验收标准是：说明里得有一条**照抄就能得到正确答案**的命令，
    而不是让他去猜 python 还是 python3。另外这个默认值在 Windows 上多半是错的，
    错法还很隐蔽（应用商店占位程序不报错、只是连不上），所以必须点名。
    """
    d = PLUGIN["userConfig"]["python"]["description"]
    assert "sys.executable" in d, "要给出一条能打印出当前环境解释器绝对路径的命令"
    assert "Windows" in d and "绝对路径" in d, "要点名 Windows 上默认值多半是错的"
    assert "selfcheck" in d or "doctor" in d, "要指向装完之后的自证手段"


def test_the_advertised_tool_count_matches_reality():
    """清单里的「N 个 MCP 工具」是用户装之前唯一看得到的规格，对不上就是虚标。"""
    n = len(M.TOOLS)
    for label, text in (("plugin.json", PLUGIN["description"]),
                        ("marketplace.json", MARKET["plugins"][0]["description"])):
        counts = {int(x) for x in re.findall(r"(\d+)\s*个 MCP 工具", text)}
        assert counts == {n}, f"{label} 写着 {counts or '没写'} 个 MCP 工具，实际 {n} 个"


# ------------------------------------------------------------ 空值语义
# 用户把某一项留空时，环境变量会是空串。代码必须把空串当成"没配"。


def test_blank_user_config_values_behave_as_unset(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "")          # 用户没填远端
    monkeypatch.setenv("TRACE_TOKEN", "")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    be = M.make_backend()
    assert isinstance(be, M.LocalBackend), "留空的远端地址不该盖过本地目录"


def test_whitespace_only_values_also_count_as_unset(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "   ")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    assert isinstance(M.make_backend(), M.LocalBackend)


def test_remote_wins_when_both_are_filled(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "https://例子/t/abc")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    be = M.make_backend()
    assert isinstance(be, M.HttpBackend) and be.base == "https://例子/t/abc"


def test_all_blank_gives_an_actionable_error(monkeypatch, tmp_path):
    for k in ("TRACE_URL", "TRACE_TOKEN", "TRACE_DATA"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "没有这个文件.json"))
    with pytest.raises(M.ToolError) as e:
        M.make_backend()
    assert "TRACE_DATA" in str(e.value) and "trace.json" in str(e.value)


# ------------------------------------------------------------ 令牌不该让人手抄
# 令牌在 init 时就随机生成好了。本地模式根本用不上；服务在本机时程序能自己找到。


# 这个 space 是**编出来的**，不要换成真实部署里的值。
# space 是读取侧唯一的保护（挂在不可猜路径下），而这个仓库是公开的——
# 把真值写进测试，等于把它连同 git 历史一起发出去。
FAKE_SPACE = "EXAMPLE-space-not-a-real-one"


def _server_repo(tmp_path: Path, space: str, token: str) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"space": space, "token": token}), encoding="utf-8")
    return d


def test_local_mode_needs_no_token_at_all(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    for k in ("TRACE_URL", "TRACE_TOKEN"):
        monkeypatch.setenv(k, "")
    assert isinstance(M.make_backend(), M.LocalBackend)


def test_token_is_discovered_from_the_local_config_when_the_space_matches(tmp_path: Path, monkeypatch):
    """服务跑在本机时，令牌就在 config.json 里，不该再让人抄一遍。"""
    repo = _server_repo(tmp_path, FAKE_SPACE, "秘密令牌")
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "http://127.0.0.1:8123/t/" + FAKE_SPACE)
    monkeypatch.setenv("TRACE_TOKEN", "")
    monkeypatch.setenv("TRACE_DATA", str(repo))
    assert M.make_backend().token == "秘密令牌"


def test_a_token_for_a_different_server_is_never_used(tmp_path: Path, monkeypatch):
    """本地留着的可能是另一台服务器的令牌，拿去用只会换来莫名其妙的 401。"""
    repo = _server_repo(tmp_path, "本机的space", "本机的令牌")
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "https://别的域名/t/完全不同的space")
    monkeypatch.setenv("TRACE_TOKEN", "")
    monkeypatch.setenv("TRACE_DATA", str(repo))
    assert M.make_backend().token == "", "space 对不上就不能用那个令牌"


def test_an_explicit_token_always_wins(tmp_path: Path, monkeypatch):
    repo = _server_repo(tmp_path, "space1", "自动找到的")
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "https://x/t/space1")
    monkeypatch.setenv("TRACE_TOKEN", "手填的")
    monkeypatch.setenv("TRACE_DATA", str(repo))
    assert M.make_backend().token == "手填的"


def test_discovery_survives_a_broken_config_json(tmp_path: Path, monkeypatch):
    d = tmp_path / "repo"
    d.mkdir()
    (d / "config.json").write_text("{ 这不是 JSON", encoding="utf-8")
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "https://x/t/space1")
    monkeypatch.setenv("TRACE_TOKEN", "")
    monkeypatch.setenv("TRACE_DATA", str(d))
    assert M.make_backend().token == ""      # 不崩，只是找不到


# ------------------------------------------------------------ agents / skills 的约定
# 对照真实插件（addy-agent-skills、claude-brain-sync）的写法。


AGENT_DIR = ROOT / "agents"
SKILL_DIR = ROOT / "skills"


# trace 自己的 MCP server 在 Claude Code 里的完整前缀。
# 这个串不是猜的，是两处独立证据对上的结果：
#   1. 官方 plugin-dev 的 mcp-integration 文档写死了 `mcp__plugin_<插件名>_<server名>__<工具名>`；
#   2. claude 二进制里插件的 MCP server 是以 `plugin:<插件名>:<server名>` 为键注册的，
#      而工具名前缀由 `mcp__${of(serverName)}__` 拼出，`of()` 把非 [A-Za-z0-9_-] 的字符换成 `_`
#      —— 于是 `plugin:research-trace:trace` → `plugin_research-trace_trace`（连字符原样保留）。
# 写错前缀的后果是**静默的**：agent 的白名单里那一条永远匹配不上任何工具，
# 正文让它调 trace_read 它却根本看不见这个工具。所以这里集中定义一次，由测试钉住。
MCP_PREFIX = "mcp__plugin_research-trace_trace__"

# trace_mcp.py 实际注册的全部工具，按「读」「写」分开——
# auditor 只读这条线是需求，不是风格偏好。
# trace_untranslated 归读（它只回答「还欠哪些语言版本」，一个字节都不写）；
# trace_translate 归写：它落一个 note.<lang>.md 到磁盘上。译文虽然碰不到原文，
# 但 auditor 的约束是「只查证、不改动仓库」，写译文一样违反它。
# trace_flow 归读：它只是沿 inputs 求一遍传递闭包，派生结果，不存。
# trace_check_paths 归**写**，尽管它读起来像查证：它把 checked= / missing= 写进 note.md。
# 这一条值得单说——「路径还在不在」正是 auditor 该干的活，但按现在的工具集它够不着，
# 只能报告给人再由人写回（见报告里的接缝）。
READ_TOOLS = {"trace_projects", "trace_read", "trace_search", "trace_untranslated", "trace_flow"}
WRITE_TOOLS = {"trace_new_project", "trace_insight", "trace_delete_step",
               "trace_new_step", "trace_update_step", "trace_move_step",
               "trace_check_paths", "trace_attach", "trace_translate"}


def _split_tools(value) -> list:
    """把 frontmatter 里的工具字段拍平成列表。

    Claude Code 两种写法都收（YAML 数组、逗号/空格分隔的标量），
    这里照它的 `cy()` 一样按逗号和空白切，免得测试和运行时对同一份文件有两种理解。
    """
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        for tok in re.split(r"[,\s]+", str(item)):
            if tok:
                out.append(tok)
    return out


def _frontmatter(p: Path) -> dict:
    """只解析 front-matter，值可能是字符串，也可能是 YAML 块列表。

    刻意不依赖 PyYAML：这些断言是插件能不能用的底线，
    不该因为某台机器上少装一个包就整组跳过。
    """
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{p.name} 缺 front-matter"
    body = text.split("\n---", 1)
    assert len(body) == 2, f"{p.name} 的 front-matter 没有闭合"
    out: dict = {}
    key = None
    for line in body[0].split("\n")[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and line.lstrip().startswith("- "):
            assert key is not None, f"{p.name} 的 front-matter 有孤立的列表项：{line!r}"
            if not isinstance(out[key], list):
                out[key] = []
            out[key].append(line.lstrip()[2:].strip())
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        out[key] = raw
    return out


def _reachable_trace_tools(fm: dict) -> set:
    """按 Claude Code 的语义推导：这个 agent 到底够得着哪几个 trace_* 工具。

    语义取自 claude 二进制里的工具解析函数：
      · `tools` 缺省（或就一个 `*`）→ 继承父上下文的全部工具，MCP 工具也在内；
      · `tools` 写了就是白名单，只有列进去的名字（或 `mcp__<server>__*` 这种整服务通配）才在；
      · `disallowedTools` 是精确名匹配、没有通配，且**先于**白名单生效。
    """
    listed = _split_tools(fm.get("tools"))
    banned = set(_split_tools(fm.get("disallowedTools")))
    wildcard = not listed or listed == ["*"]
    names = READ_TOOLS | WRITE_TOOLS
    if wildcard:
        granted = set(names)
    else:
        granted = {n for n in names
                   if MCP_PREFIX + n in listed or MCP_PREFIX + "*" in listed}
    return {n for n in granted if MCP_PREFIX + n not in banned}


def test_every_declared_agent_file_exists():
    for rel in PLUGIN["agents"]:
        assert (ROOT / rel.lstrip("./")).is_file(), f"清单声明了 {rel} 但文件不在"


def test_agents_have_the_conventional_frontmatter():
    for p in sorted(AGENT_DIR.glob("*.md")):
        fm = _frontmatter(p)
        assert fm.get("name") == p.stem, f"{p.name} 的 name 要和文件名一致"
        assert len(fm.get("description", "")) > 80, f"{p.name} 的 description 太短，触发不了"
        assert fm.get("model") in ("sonnet", "opus", "haiku", "inherit"), fm.get("model")


def test_the_auditor_cannot_write():
    """审计只查证不改动 —— 要不要复现是用户的决定，不是它的。"""
    fm = _frontmatter(AGENT_DIR / "trace-auditor.md")
    banned = set(_split_tools(fm.get("disallowedTools")))
    assert {"Write", "Edit"} <= banned, "auditor 必须禁掉写工具"
    assert "Write" not in _split_tools(fm.get("tools"))


# ------------------------------------------------------------ agent 够不够得着它被要求调用的工具
# 这一组防的是一类特定的错：**正文让 agent 调 X，但按 frontmatter 的语义 X 根本不在它手里。**
# 这种错不会报语法错、装得上、跑得起来，只在用户真的问「004 可靠吗」的那一刻才炸，
# 而且炸出来的样子是 agent 空手回来说「我没有这个工具」——最难归因的一种失败。
# 所以做成机械检查，别指望改文档的人记得同步改白名单。


def test_the_auditor_can_read_the_log_it_is_asked_to_audit():
    """审计的对象在 MCP 后端里，不在工作目录里 —— 没有 trace_read 就是空手上阵。"""
    got = _reachable_trace_tools(_frontmatter(AGENT_DIR / "trace-auditor.md"))
    assert "trace_read" in got, "auditor 读不到要审计的记录"
    assert {"trace_search", "trace_projects"} <= got, "找不到项目/搜不了旧步骤，链就查不全"


def test_the_auditor_reaches_no_write_tool_at_all():
    """只读是需求本身：结论要等用户拍板才落盘，agent 不能自己先写。"""
    got = _reachable_trace_tools(_frontmatter(AGENT_DIR / "trace-auditor.md"))
    assert not (got & WRITE_TOOLS), f"auditor 够得着写工具：{sorted(got & WRITE_TOOLS)}"


def test_the_reproducer_can_write_the_repro_tag_back():
    """「给系统里是否复现成功加一个 tag」这一半就落在 trace_update_step 上。

    它要是不在 reproducer 的工具集里，跑完真实机时也没有任何东西被写回，
    只能靠协调者照着报告转录一遍 —— 多一层 LLM 转录就多一次抄错数字的机会。
    """
    got = _reachable_trace_tools(_frontmatter(AGENT_DIR / "trace-reproducer.md"))
    assert "trace_update_step" in got, "reproducer 写不回 repro:"
    assert "trace_read" in got, "写回之前先得读得到要复现的那一步"


def test_the_reproducer_cannot_delete_anything():
    """复现是只追加的动作：对不上只能追加一条说明，不能把原记录删掉。"""
    got = _reachable_trace_tools(_frontmatter(AGENT_DIR / "trace-reproducer.md"))
    assert "trace_delete_step" not in got


def test_every_trace_tool_an_agent_is_told_to_call_is_reachable():
    """正文点名的每个 trace_* 工具，都必须在它的工具集里 —— 除非是**故意**禁掉的。

    第二种情况（写进 disallowedTools）是合法的：agent 正文里解释「这个工具没给你、为什么」
    比闭口不提要好。所以这里只拦「说要调、却既没授权也没明确禁止」的那一类。
    """
    for p in sorted(AGENT_DIR.glob("*.md")):
        fm = _frontmatter(p)
        body = p.read_text(encoding="utf-8").split("\n---", 1)[1]
        mentioned = {n for n in READ_TOOLS | WRITE_TOOLS if n in body}
        assert mentioned, f"{p.name} 正文一个 trace_* 工具都没提，它凭什么读得到记录"
        banned = set(_split_tools(fm.get("disallowedTools")))
        reachable = _reachable_trace_tools(fm)
        for name in sorted(mentioned):
            assert name in reachable or MCP_PREFIX + name in banned, (
                f"{p.name} 正文让它用 {name}，但白名单里没有、也没写进 disallowedTools")


def test_agents_spell_mcp_tools_with_the_prefix_claude_code_actually_generates():
    """前缀写错是静默失效：白名单里那一条永远匹配不上，等于没写。"""
    pat = re.compile(r"mcp__[A-Za-z0-9_-]+__[A-Za-z0-9_]+")
    for p in sorted(AGENT_DIR.glob("*.md")):
        for name in pat.findall(p.read_text(encoding="utf-8")):
            assert name.startswith(MCP_PREFIX), f"{p.name} 里的 {name} 前缀不对"
            assert name[len(MCP_PREFIX):] in READ_TOOLS | WRITE_TOOLS, \
                f"{p.name} 里的 {name} 不是 trace_mcp.py 注册过的工具"


def test_the_frontmatter_mcp_names_match_the_tools_the_server_registers():
    """白名单里写了个 trace_mcp.py 根本没注册的工具名 = 那一条白写。"""
    real = {t["name"] for t in M.TOOLS}
    assert real == READ_TOOLS | WRITE_TOOLS, "工具增删了，这个测试里的读/写分类要跟着改"
    for p in sorted(AGENT_DIR.glob("*.md")):
        fm = _frontmatter(p)
        for entry in _split_tools(fm.get("tools")) + _split_tools(fm.get("disallowedTools")):
            if entry.startswith("mcp__"):
                assert entry.startswith(MCP_PREFIX), f"{p.name}: {entry} 前缀不对"
                assert entry[len(MCP_PREFIX):] in real | {"*"}, f"{p.name}: 没有 {entry} 这个工具"


def test_subagents_never_try_to_ask_the_user_themselves():
    """`AskUserQuestion` 在子 agent 里不存在 —— Claude Code 组装子 agent 的工具集时
    会无条件把它（连同 EnterPlanMode / ExitPlanMode）剔除，frontmatter 怎么写都没用。

    所以「问作者」只能由 skill 在主循环里做。两个 agent 的正文必须把问题交还协调者，
    而不是自己发起提问 —— 一旦它们打算自己问，流程就会卡死或者 agent 转而自己猜答案。
    """
    for p in sorted(AGENT_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        assert "AskUserQuestion" not in _split_tools(_frontmatter(p).get("tools")), \
            f"{p.name} 把 AskUserQuestion 写进了工具白名单，运行时拿不到"
        assert "要问作者的" in text, f"{p.name} 没有把「要问作者的」交还给协调者的出口"
    skill = (SKILL_DIR / "trace-audit" / "SKILL.md").read_text(encoding="utf-8")
    assert "AskUserQuestion" in skill and "要问作者的" in skill, \
        "skill 必须在主循环里问，并且认领 agent 报告里的「要问作者的」"


def test_every_agent_file_is_declared_in_the_manifest():
    declared = {Path(r).name for r in PLUGIN["agents"]}
    on_disk = {p.name for p in AGENT_DIR.glob("*.md")}
    assert declared == on_disk, f"清单少了: {on_disk - declared}；多了: {declared - on_disk}"


def test_skills_follow_the_directory_convention():
    """真实插件的写法：skills/<名字>/SKILL.md，name 与目录名一致。"""
    dirs = [d for d in SKILL_DIR.iterdir() if d.is_dir()]
    assert dirs, "skills/ 是空的"
    for d in dirs:
        f = d / "SKILL.md"
        assert f.is_file(), f"{d.name} 里没有 SKILL.md"
        fm = _frontmatter(f)
        assert fm.get("name") == d.name, f"{d.name}/SKILL.md 的 name 要和目录名一致"
        assert len(fm.get("description", "")) > 60, f"{d.name} 的 description 太短"


def test_the_audit_skill_delegates_instead_of_doing_the_work_itself():
    """查证归 auditor、重跑归 reproducer，skill 只负责问用户和写回。"""
    s = (SKILL_DIR / "trace-audit" / "SKILL.md").read_text(encoding="utf-8")
    assert "trace-auditor" in s and "trace-reproducer" in s
    assert "AskUserQuestion" in s, "问用户是 skill 的活，子 agent 问不了"


def test_the_plugin_has_a_license():
    assert (ROOT / "LICENSE").is_file()
    assert PLUGIN.get("license") == "MIT"


# ------------------------------------------------------------ 官方校验器
# 与其我逐条对照文档，不如让 Claude Code 自带的校验器说话。
# 它抓到过一个我自己踩的坑：agent 的 description 里有 "Read-only: it never…"，
# 未加引号的 YAML 标量含 ": " 直接解析失败 —— 运行时会**静默丢掉整个 frontmatter**，
# disallowedTools 那道禁写闸门等于不存在。


def _claude_cli():
    import shutil
    return shutil.which("claude") or shutil.which("claude.cmd") or shutil.which("claude.exe")


@pytest.mark.parametrize("manifest", ["plugin.json", "marketplace.json"])
def test_official_validator_passes_in_strict_mode(manifest):
    import subprocess

    exe = _claude_cli()
    if not exe:
        pytest.skip("claude CLI 不在 PATH 上")
    p = subprocess.run([exe, "plugin", "validate", str(ROOT / ".claude-plugin" / manifest), "--strict"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert p.returncode == 0, (p.stdout + p.stderr)[-2000:]


def test_every_frontmatter_actually_parses_as_yaml():
    """校验器只在装了 CLI 时跑，这条是无依赖的兜底。"""
    yaml = pytest.importorskip("yaml", reason="装了 PyYAML 才跑这条")
    files = list(AGENT_DIR.glob("*.md")) + list(SKILL_DIR.glob("*/SKILL.md")) \
        + list((ROOT / "commands").glob("*.md"))
    assert files
    for f in files:
        text = f.read_text(encoding="utf-8")
        head = text.split("\n---", 1)[0].removeprefix("---\n")
        try:
            meta = yaml.safe_load(head)
        except yaml.YAMLError as e:
            raise AssertionError(f"{f.relative_to(ROOT)} 的 frontmatter 不是合法 YAML：{e}") from None
        assert isinstance(meta, dict) and meta.get("description"), f.name


def _frontmatter_files():
    return sorted(AGENT_DIR.glob("*.md")) + sorted(SKILL_DIR.glob("*/SKILL.md")) \
        + sorted((ROOT / "commands").glob("*.md"))


def test_no_unquoted_scalar_hides_a_colon_space():
    """复现过一次的真实事故，做成机械检查。

    front-matter 里 `description: Read-only: it never…` 这种未加引号的标量含 `": "`，
    YAML 会解析失败，而 Claude Code 的反应是**静默丢掉整个 front-matter**——
    于是 disallowedTools 那道禁写闸门等于不存在，还没有任何报错提示你。
    单靠「解析得动」不够，因为下一个人加字段时同样看不见这个坑，所以这里直接盯着写法。
    """
    for f in _frontmatter_files():
        head = f.read_text(encoding="utf-8").split("\n---", 1)[0].removeprefix("---\n")
        for line in head.split("\n"):
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s+(\S.*)$", line)
            if not m:
                continue
            v = m.group(2).strip()
            quoted = len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'"
            assert quoted or ": " not in v, \
                f"{f.relative_to(ROOT)} 的 {m.group(1)} 含 ': ' 却没加引号"


def test_every_frontmatter_yields_the_keys_its_loader_will_look_for():
    """解析得动还不够：解析出来的键和类型也要是加载器要的那几个。

    键名打错（disallowTools / tool）不会报错，只会安静地少一条约束；
    值的类型写岔（tools 写成一段散文）同样安静。所以逐个文件对着预期的形状验一遍。
    """
    yaml = pytest.importorskip("yaml", reason="装了 PyYAML 才跑这条")
    for f in _frontmatter_files():
        head = f.read_text(encoding="utf-8").split("\n---", 1)[0].removeprefix("---\n")
        meta = yaml.safe_load(head)
        where = f.relative_to(ROOT)
        assert isinstance(meta, dict), f"{where} 的 front-matter 不是映射"
        assert isinstance(meta.get("description"), str) and meta["description"].strip(), where

        if f.parent == AGENT_DIR:
            assert meta.get("name") == f.stem, f"{where} 的 name 要和文件名一致"
            assert meta.get("model") in ("sonnet", "opus", "haiku", "inherit"), where
            assert isinstance(meta.get("maxTurns"), int) and meta["maxTurns"] > 0, where
            for key in ("tools", "disallowedTools"):
                val = meta.get(key)
                assert isinstance(val, (list, str)) and _split_tools(val), \
                    f"{where} 的 {key} 要是非空的列表或逗号分隔串"
            unknown = set(meta) - {"name", "description", "tools", "disallowedTools",
                                   "model", "maxTurns", "color", "effort"}
            assert not unknown, f"{where} 有加载器不认识的键：{sorted(unknown)}（打错字会被静默忽略）"
        elif f.name == "SKILL.md":
            assert meta.get("name") == f.parent.name, f"{where} 的 name 要和目录名一致"


# ------------------------------------------------------------ 角色
# 装的时候问清楚是服务端还是客户端，配错了当场报出来 ——
# 「我选了客户端，怎么读到的是本地空目录」这种问题最难查。


@pytest.fixture(autouse=False)
def clean_env(monkeypatch, tmp_path):
    for k in ("TRACE_ROLE", "TRACE_URL", "TRACE_TOKEN", "TRACE_DATA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "没有这个文件.json"))
    return monkeypatch


def test_role_is_offered_at_install_time():
    spec = PLUGIN["userConfig"]["role"]
    assert spec["default"] == "auto"
    for v in ("server", "client", "auto"):
        assert v in spec["description"], f"说明里要写清 {v} 是干什么的"
    assert "TRACE_ROLE" in PLUGIN["mcpServers"]["trace"]["env"]


def test_client_without_a_url_says_so(clean_env):
    clean_env.setenv("TRACE_ROLE", "client")
    with pytest.raises(M.ToolError, match="客户端"):
        M.make_backend()


def test_server_without_a_data_dir_says_so(clean_env):
    clean_env.setenv("TRACE_ROLE", "server")
    with pytest.raises(M.ToolError, match="服务端"):
        M.make_backend()


def test_an_unknown_role_is_refused(clean_env):
    clean_env.setenv("TRACE_ROLE", "随便")
    with pytest.raises(M.ToolError, match="auto/server/client"):
        M.make_backend()


def test_the_role_decides_even_when_both_are_filled(clean_env, tmp_path):
    clean_env.setenv("TRACE_URL", "https://x/t/s")
    clean_env.setenv("TRACE_DATA", str(tmp_path))
    clean_env.setenv("TRACE_ROLE", "client")
    assert isinstance(M.make_backend(), M.HttpBackend)
    clean_env.setenv("TRACE_ROLE", "server")
    assert isinstance(M.make_backend(), M.LocalBackend), "服务端本地直读，不该绕 HTTP"
    clean_env.setenv("TRACE_ROLE", "auto")
    assert isinstance(M.make_backend(), M.HttpBackend), "auto 下远端优先"


def test_role_can_come_from_the_config_file_too(clean_env, tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"role": "server", "data": str(tmp_path / "仓")}), encoding="utf-8")
    clean_env.setenv("TRACE_CONFIG", str(cfg))
    assert isinstance(M.make_backend(), M.LocalBackend)


# ------------------------------------------------------------ 自检
# 新机器上装完跑一条命令就知道能不能用。不需要 Claude、不需要网络。


def test_selfcheck_passes_on_a_working_local_setup(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("TRACE_ROLE", "server")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "无.json"))
    assert M.selfcheck() == 0
    out = capsys.readouterr().out
    assert "全部通过" in out
    assert f"{len(M.TOOLS)} 个工具" in out


def test_selfcheck_fails_loudly_on_a_misconfiguration(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("TRACE_ROLE", "client")
    monkeypatch.delenv("TRACE_URL", raising=False)
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "无.json"))
    assert M.selfcheck() == 1
    assert "客户端" in capsys.readouterr().out


def test_selfcheck_warns_when_the_data_dir_is_the_projects_dir_itself(tmp_path: Path, monkeypatch, capsys):
    """指到 projects/ 本身而不是它的父目录 —— 新机器上最容易犯的配置错，要当场报。"""
    import trace_core as core

    core.ensure_layout(tmp_path)                                   # tmp/projects/
    monkeypatch.setenv("TRACE_ROLE", "server")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path / "projects"))   # 指深了一层
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "无.json"))
    M.selfcheck()
    out = capsys.readouterr().out
    assert "指深了一层" in out and "父目录" in out


def test_selfcheck_flags_the_ghost_project_left_behind(tmp_path: Path, monkeypatch, capsys):
    """指错一层会留下一个空的 projects/projects，之后指对了它就冒出来当项目。"""
    import trace_core as core

    core.ensure_layout(tmp_path)
    (tmp_path / "projects" / "projects" / "steps").mkdir(parents=True)   # 上次指错留下的
    monkeypatch.setenv("TRACE_ROLE", "server")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "无.json"))
    M.selfcheck()
    out = capsys.readouterr().out
    assert "空壳项目" in out and "projects" in out


def test_no_real_secret_ever_reaches_a_tracked_file():
    """本仓库是公开的；config.json 里的 space 和 token 一个字节都不能进去。

    这条测试是从一次真实的疏忽里长出来的：我把本机的 space 硬编码进了这个文件，
    而 space 正是读取侧唯一的保护。令牌至少还会被 401 挡住，space 泄露了就是
    "谁都能读你的全部科研记录"。所以做成机械检查，不靠人记得。
    """
    cfg = ROOT / "config.json"
    if not cfg.is_file():
        pytest.skip("这台机器上没有 config.json（客户端模式）")
    conf = json.loads(cfg.read_text(encoding="utf-8"))
    secrets = [str(conf.get(k, "")) for k in ("space", "token")]
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, check=False)
    if out.returncode != 0:
        pytest.skip("不在 git 仓库里")
    for rel in out.stdout.decode("utf-8").split(chr(0)):
        if not rel:
            continue
        f = ROOT / rel
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for secret in secrets:
            if len(secret) >= 8 and secret in text:
                pytest.fail(f"{rel} 里出现了 config.json 的真实机密 —— 这个仓库是公开的")
