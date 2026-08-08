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


def _frontmatter(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{p.name} 缺 front-matter"
    body = text.split("\n---", 1)
    assert len(body) == 2, f"{p.name} 的 front-matter 没有闭合"
    out = {}
    for line in body[0].split("\n")[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


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
    banned = {t.strip() for t in fm.get("disallowedTools", "").split(",")}
    assert {"Write", "Edit"} <= banned, "auditor 必须禁掉写工具"
    assert "Write" not in fm.get("tools", "")


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
