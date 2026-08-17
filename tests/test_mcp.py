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


# ------------------------------------------------------------ 翻译


def note_dir(be, sid="001"):
    import trace_write as W

    sd = be._sd("alpha")
    return sd / W.load(sd)[sid].dirname


def test_translate_writes_a_separate_file_and_never_touches_the_original(be):
    """整套双语设计的地基。译文要是能写进 note.md，「原文永远赢」就成了空话，
    而这正是上一代系统（父子关系存两处）的死法。"""
    call(be, "trace_new_step", project="alpha", title="加入标题字段",
         body="## 为什么\n基线的 TF-IDF 丢掉词序")
    before = (note_dir(be) / "note.md").read_bytes()
    out = call(be, "trace_translate", project="alpha", step="001", lang="en",
               title="Add title field", body="## Why\nThe TF-IDF baseline discards word order.")
    assert "note.en.md" in out and "原文" in out
    assert (note_dir(be) / "note.md").read_bytes() == before
    assert "## Why" in (note_dir(be) / "note.en.md").read_text(encoding="utf-8")
    assert be.step("alpha", "001")["tr"]["en"]["title"] == "Add title field"


def test_translating_later_is_the_same_call_as_translating_now(be):
    """「立刻」和「延迟」必须是同一条路径——否则建步骤时就得先决定要不要双语，
    而那个决定往往几天后才做得出来。这里就是同一次调用发生在两个时刻。"""
    call(be, "trace_new_step", project="alpha", title="x", body="## 为什么\n因为")
    call(be, "trace_translate", project="alpha", step="001", lang="en", body="## Why\nv1")
    call(be, "trace_update_step", project="alpha", step="001", status="done",
         append="## 结果\n0.951")
    call(be, "trace_translate", project="alpha", step="001", lang="en",
         body="## Why\nv1\n\n## Result\n0.951")
    s = be.step("alpha", "001")
    assert "0.951" in s["body"] and "0.951" in s["tr"]["en"]["body"]


def test_translate_without_a_step_translates_the_project_note(be):
    out = call(be, "trace_translate", project="alpha", lang="en",
               title="My topic", body="## Works\n- dedup helps")
    assert "project.en.md" in out
    text = (be.root / "projects" / "alpha" / "project.en.md").read_text(encoding="utf-8")
    assert "name: My topic" in text and "## Works" in text


def test_translate_refuses_a_body_that_carries_its_own_front_matter(be):
    """结构键从函数形状上就进不了译文的 front-matter，所以 agent 拼的那一段 `---`
    会原样落进正文：不报错、不生效、看着却像写上了。静默地什么都没发生最难查。"""
    call(be, "trace_new_step", project="alpha", title="x")
    with pytest.raises(M.ToolError, match="front-matter"):
        call(be, "trace_translate", project="alpha", step="001", lang="en",
             body="---\nid: 001\nparent: none\n---\n\n## Why\nbecause")
    assert not (note_dir(be) / "note.en.md").exists(), "拒绝之后不该已经写了半份"


def test_translate_rejects_a_language_the_original_already_declares(be):
    """原文声明了 lang: en 还写一份 en 译文，两份就会各说各话——同一个事实两处存储。"""
    call(be, "trace_new_step", project="alpha", title="x", body="## Why\nbecause")
    note = note_dir(be) / "note.md"
    note.write_text(note.read_text(encoding="utf-8").replace("status:", "lang: en\nstatus:", 1),
                    encoding="utf-8")
    with pytest.raises(M.ToolError, match="lang"):
        call(be, "trace_translate", project="alpha", step="001", lang="en", body="## Why\nagain")


def test_untranslated_says_what_is_still_missing_and_stops_saying_it(be):
    """延迟翻译能落地全靠它：隔几天回来，agent 得先知道还欠哪些。"""
    call(be, "trace_new_step", project="alpha", title="第一步")
    call(be, "trace_new_step", project="alpha", title="第二步")
    out = call(be, "trace_untranslated", project="alpha", lang="en")
    assert "002" in out and "第二步" in out and "还缺 2" in out
    assert "project.en.md" in out

    call(be, "trace_translate", project="alpha", step="002", lang="en", title="Second")
    out = call(be, "trace_untranslated", project="alpha", lang="en")
    assert "002" not in out.split("项目笔记")[0], "补完的那一步不该还挂在缺翻译清单上"


def test_untranslated_defaults_to_english(be):
    call(be, "trace_new_step", project="alpha", title="x")
    assert "en" in call(be, "trace_untranslated", project="alpha")


def test_untranslated_does_not_ask_for_a_translation_that_would_be_refused(be):
    """原文就是 en 的步骤，写 en 译文会被拒。列进「还缺」等于派 agent 去做
    一件必然失败的事，而它会照做、失败、然后重试。"""
    call(be, "trace_new_step", project="alpha", title="English step", body="## Why\nbecause")
    note = note_dir(be) / "note.md"
    note.write_text(note.read_text(encoding="utf-8").replace("status:", "lang: en\nstatus:", 1),
                    encoding="utf-8")
    out = call(be, "trace_untranslated", project="alpha", lang="en")
    assert "还缺 0" in out and "原文就是 en" in out


def test_search_finds_a_word_that_only_lives_in_the_translation(be):
    """G4 加了双语之后的形态：`grep -r abandoned` 命中 note.en.md，trace_search
    也必须命中——否则 agent 拿到「没搜到」，而它会把这四个字读成「没试过」。"""
    call(be, "trace_new_step", project="alpha", title="对比学习", status="dead",
         body="## 结论\n没有提升，放弃这条路。")
    call(be, "trace_translate", project="alpha", step="001", lang="en",
         title="Contrastive pretraining",
         body="## Conclusion\nNo gain at all. This line is abandoned.")
    out = call(be, "trace_search", query="abandoned")
    assert "alpha/001" in out and "命中 en 译文" in out
    assert "abandoned" in out, "摘要要给上下文，不然还得再读一遍全文"


def test_reading_a_step_says_which_translations_exist(be):
    """不知道已经有 en 版就重写一遍，等于把别人的译文覆盖掉。"""
    call(be, "trace_new_step", project="alpha", title="x", body="## 为什么\n因为")
    call(be, "trace_translate", project="alpha", step="001", lang="en", title="X")
    assert "已有译文: en" in call(be, "trace_read", project="alpha", step="001")


def test_the_tool_description_carries_the_section_table(be):
    """agent 手上没有别的地方能知道这张对照表：pip 装的机器上不存在 FORMAT.md，
    而小节名是精确匹配的——写成 `## Why not` 评级和 check 就都找不到内容。"""
    import trace_core as core  # noqa: PLC0415

    desc = next(t for t in M.TOOLS if t["name"] == "trace_translate")["description"]
    for names in core.SECTION_NAMES.values():
        for lang in ("zh", "en"):
            assert names[lang] in desc, f"{names[lang]} 没写进 trace_translate 的描述"
    for names in core.INSIGHT_NAMES.values():
        assert names["en"] in desc, f"{names['en']} 没写进 trace_translate 的描述"


def test_the_structural_key_list_matches_the_core_side(be):
    """trace_mcp 留了一份字面量（远端后端那条路上可能没有 trace_core）。
    两份对不上时，工具描述会向 agent 承诺一件内核并不执行的事。"""
    import trace_core as core  # noqa: PLC0415

    assert set(M.TR_STRUCT_KEYS) == set(core.TR_STRUCT_KEYS)


def test_the_instructions_tell_agents_how_bilingual_works(be):
    """initialize 的 instructions 是唯一无论怎么装都一定送达的通道。"""
    text = M.INSTRUCTIONS
    assert "trace_translate" in text and "note.en.md" in text
    assert "双真相源" in text, "「结构键只认原文」的理由要说出来，不然 agent 会觉得是多余的规矩"
    assert "trace_untranslated" in text


def test_why_is_blank_understands_english_section_names(be):
    """认死中文的话，一份英文 note.md 会被整篇判成「没写为什么」，
    每建一步吃一条假警告——而假警告会让真警告也被忽略。"""
    assert M._why_is_blank("## Why\n\n## What\n") is True
    assert M._why_is_blank("## Why\nThe baseline discards word order.\n") is False
    assert M._why_is_blank("## 为什么\n（承接上一步）") is True
    assert M._why_is_blank("## 为什么\n因为验证集有重复样本") is False


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


class RecordingHttp(M.HttpBackend):
    """只记下「打了哪个 URL」，不碰网络。"""

    def __init__(self):
        super().__init__("https://例子/t/s", "token")
        self.calls: list = []

    def _call(self, method, path, payload=None, raw=None, headers=None):
        self.calls.append((method, path, payload))
        return {"id": "001", "lang": "en", "path": "note.en.md", "digest": "d",
                "project": "alpha", "total": 0, "translated": 0, "native": 0,
                "missing": [], "project_note": {"missing": True, "native": False, "name": ""}}


def test_the_remote_backend_calls_urls_the_server_really_serves(tmp_path: Path, monkeypatch):
    """MCP 的两个后端是同一套语义的两个门面。远端那侧拼错一个 URL 的症状很坏：
    本地模式（作者的机器）一切正常，HPC 上走 TRACE_URL 的 agent 全是 404，
    而 404 在工具层被报成「工具执行失败」，看不出是路由错了还是数据没了。"""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient  # noqa: PLC0415

    import trace_core as core  # noqa: PLC0415
    import trace_server as S  # noqa: PLC0415
    import trace_write as W  # noqa: PLC0415

    monkeypatch.setattr(S, "ROOT", tmp_path)
    app = S.create_app({"data_dir": ".", "space": "", "token": "t", "git": {"enabled": False}})
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "alpha")
    W.create_step(core.steps_dir_of(tmp_path, "alpha"), title="x")

    be = RecordingHttp()
    be.translate("alpha", "001", "en", {"title": "T", "body": "## Why\nx"})
    be.translate_project("alpha", "en", {"name": "N", "body": "## Works\n- a"})
    be.untranslated("alpha", "en")
    assert [c[1] for c in be.calls] == ["/api/p/alpha/steps/001/tr/en",
                                        "/api/p/alpha/tr/en",
                                        "/api/p/alpha/untranslated?lang=en"]
    with TestClient(app) as c:
        for method, path, payload in be.calls:
            r = c.request(method, path, json=payload, headers={"Authorization": "Bearer t"})
            assert r.status_code == 200, f"{method} {path} → {r.status_code} {r.text[:120]}"


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


# ------------------------------------------------------------ 移动


def test_move_step_records_the_reason_and_keeps_the_id(be):
    call(be, "trace_new_step", project="alpha", title="013b")
    call(be, "trace_new_step", project="alpha", title="014")
    call(be, "trace_new_step", project="alpha", parent="002", title="016")
    out = call(be, "trace_move_step", project="alpha", step="003", parent="001",
               reason="016 的输入全部来自 001 的口袋组成，002 的产物从未进过下游计算")
    assert "002" in out and "001" in out
    step = be.step("alpha", "003")
    assert step["parent"] == "001"
    assert step["moved"][0]["reason"].startswith("016 的输入")
    assert step["id"] == "003", "id 不跟着变"


def test_move_step_needs_a_reason(be):
    call(be, "trace_new_step", project="alpha", title="a")
    call(be, "trace_new_step", project="alpha", title="b")
    with pytest.raises(M.ToolError, match="原因"):
        call(be, "trace_move_step", project="alpha", step="002", parent="001", reason="  ")


def test_move_step_says_the_whole_subtree_is_coming_along(be):
    call(be, "trace_new_step", project="alpha", title="a")
    call(be, "trace_new_step", project="alpha", title="b")
    call(be, "trace_new_step", project="alpha", parent="002", title="c")
    out = call(be, "trace_move_step", project="alpha", step="002", parent="001", reason="挂错了支")
    assert "003" in out and "后代" in out


def test_the_move_tool_description_separates_it_from_inputs(be):
    """agent 最容易把「数据来自那一步」当成「应该挂在那一步下面」，
    而混了之后画出来的图会骗人——所以这条区别必须写在工具描述里。"""
    d = next(t for t in M.TOOLS if t["name"] == "trace_move_step")["description"]
    assert "inputs" in d and "id" in d
    assert "reason" in d and "必填" in d
    assert "reason" in next(t for t in M.TOOLS
                            if t["name"] == "trace_move_step")["inputSchema"]["required"]


def test_update_step_points_at_the_move_tool_instead_of_just_refusing(be):
    """光说「不能改」的后果是人跑去**对调两个节点的正文**——那才是真的毁记录。"""
    call(be, "trace_new_step", project="alpha", title="a")
    call(be, "trace_new_step", project="alpha", title="b")
    with pytest.raises(M.ToolError, match="trace_move_step"):
        call(be, "trace_update_step", project="alpha", step="002", parent="001")
    with pytest.raises(M.ToolError, match="id"):
        call(be, "trace_update_step", project="alpha", step="002", id="999")


# ------------------------------------------------------------ 数据依赖


def test_a_step_reads_back_both_directions_of_the_data_flow(be):
    """agent 看不到网页，trace_read 是它唯一能读到数据流的地方。
    缺了下游那一半，就答不了「我要改 001 的产物，谁会跟着错」。"""
    call(be, "trace_new_step", project="alpha", title="口袋组成")
    call(be, "trace_new_step", project="alpha", title="配对分数")
    call(be, "trace_new_step", project="alpha", title="配对",
         inputs=["001 | pocket_composition.csv", "002 | rmscore_pairs.csv"])
    out = call(be, "trace_read", project="alpha", step="003")
    assert "pocket_composition.csv" in out and "rmscore_pairs.csv" in out
    assert "003" in call(be, "trace_read", project="alpha", step="001")


def test_flow_walks_the_data_dependencies_not_the_tree(be):
    """树是单父的（我当时接着哪一步想），数据流是 DAG（这些字节从哪来）。
    001 → 002 → 003 全在一棵扁平的树上（都没有 parent），照样要连得起来。"""
    call(be, "trace_new_step", project="alpha", title="原始 CIF")
    call(be, "trace_new_step", project="alpha", title="口袋", inputs=["001 | cif"])
    call(be, "trace_new_step", project="alpha", title="配对", inputs=["002 | pockets"])
    up = call(be, "trace_flow", project="alpha", step="003", direction="up")
    assert "002" in up and "001" in up, "上游要求传递闭包，不是只看直接的一层"
    down = call(be, "trace_flow", project="alpha", step="001", direction="down")
    assert "002" in down and "003" in down
    assert "parent" in call(be, "trace_flow", project="alpha", step="002")


def test_flow_survives_a_cycle_in_the_records(be):
    """两个人分别手改了 note.md 就会有环。查询不能因此转不出来。"""
    call(be, "trace_new_step", project="alpha", title="a")
    call(be, "trace_new_step", project="alpha", title="b", inputs=["001 | x"])
    call(be, "trace_update_step", project="alpha", step="001", inputs=["002 | y"])
    assert "002" in call(be, "trace_flow", project="alpha", step="001", direction="up")


# ------------------------------------------------------------ 代码位置


def test_a_snapshot_counts_as_code_so_nobody_stuffs_it_into_commit(be):
    """用户之前是把快照目录塞进 commit: 的。工具描述要让 agent 不再那么干，
    而写入这条路必须真的走得通，否则那句话就是空头支票。"""
    call(be, "trace_new_step", project="alpha", title="a",
         code=["snapshot | /orange/lab/snap/20260809 | manifest=MANIFEST.md5 n=43"])
    out = call(be, "trace_read", project="alpha", step="001")
    assert "代码快照" in out and "/orange/lab/snap/20260809" in out and "manifest=MANIFEST.md5" in out
    d = next(t for t in M.TOOLS if t["name"] == "trace_new_step")["inputSchema"]
    assert "snapshot" in d["properties"]["code"]["description"]
    assert "L2" in d["properties"]["code"]["description"]


# ------------------------------------------------------------ 路径核对


def test_probe_never_touches_the_network(tmp_path: Path):
    """远端位置一律不探测：任何能写记录的人都能往 path: 里塞一个内网地址，
    去发请求就等于把「从这台机器发起请求」的权力交给了他。"""
    for loc in ("https://zenodo.org/record/1234567", "s3://bucket/k",
                "//host/share/x", "relative/path"):
        assert M.probe_path(loc) == (M.PROBE_UNREACHABLE, None), loc


def test_probe_tells_unreachable_apart_from_missing(tmp_path: Path):
    """**够不着 ≠ 不存在。** 判据是「上级目录看得见」：/blue 根本没挂时什么都不说，
    上级在而这一条没了才是真的被删了。"""
    assert M.probe_path("/blue/这台机器上没有的挂载点/data")[0] == M.PROBE_UNREACHABLE
    assert M.probe_path(str(tmp_path / "从来没建过"))[0] == M.PROBE_MISSING
    f = tmp_path / "在.bin"
    f.write_bytes(b"0123456789")
    assert M.probe_path(str(f)) == (M.PROBE_PRESENT, 10)
    # 目录不报大小：st_size 是元数据块大小，写进 size= 会把 57 GB 记成 4 KB。
    assert M.probe_path(str(tmp_path)) == (M.PROBE_PRESENT, None)


def test_check_paths_writes_back_what_it_saw(be, tmp_path: Path):
    live = tmp_path / "还在.pt"
    live.write_bytes(b"x" * 7)
    gone = tmp_path / "没了"
    call(be, "trace_new_step", project="alpha", title="a",
         paths=[f"{live} | output | 权重", f"{gone} | output | 被删掉的那个",
                "s3://bucket/k | output | 远端"])
    out = call(be, "trace_check_paths", project="alpha", step="001")
    assert "还在" in out and "已确认不存在" in out and "够不着" in out
    rows = {p["location"]: p for p in be.step("alpha", "001")["paths"]}
    assert rows[str(live)]["state"] == "present" and rows[str(live)]["size"] == 7
    assert rows[str(gone)]["state"] == "missing"
    assert rows["s3://bucket/k"]["state"] == "", "够不着的那条一个字都不许写"
    assert rows[str(gone)]["note"] == "被删掉的那个", "说明是人写的判断，机器不许碰"


def test_check_paths_keeps_the_row_of_something_that_vanished(be, tmp_path: Path):
    """路径没了是溯源结论（P4），不是要顺手清掉的笔误。"""
    gone = tmp_path / "57GB的那个"
    call(be, "trace_new_step", project="alpha", title="a",
         paths=[f"{gone} | input | 原始 CIF | size=61203283968"])
    call(be, "trace_check_paths", project="alpha", step="001")
    row = be.step("alpha", "001")["paths"][0]
    assert row["state"] == "missing" and row["size"] == 61203283968, "没了的那个有多大要留着"


def test_check_paths_refuses_a_location_it_was_never_told_about(be):
    call(be, "trace_new_step", project="alpha", title="a")
    with pytest.raises(M.ToolError):
        call(be, "trace_check_paths", project="alpha", step="001", path="/凭空来的")


def test_the_check_tool_description_says_where_it_has_to_run(be):
    """跑在笔记本上只会得到一屏「够不着」。这一点不说清，人会以为工具坏了。"""
    d = next(t for t in M.TOOLS if t["name"] == "trace_check_paths")["description"]
    assert "够不着" in d and "不存在" in d
    assert "机器" in d


# ------------------------------------------------------------ 格式标准送到 agent 手里


def test_the_instructions_carry_the_new_keys(be):
    """initialize 的 instructions 是唯一无论怎么装都一定送达的通道——
    pip 装的机器上根本没有 FORMAT.md。"""
    text = M.INSTRUCTIONS
    assert "trace_move_step" in text and "inputs" in text
    assert "checked=" in text and "missing=" in text, "path 的属性写法要送到"
    assert "snapshot" in text and "container" in text
    for key in ("input", "code", "moved"):
        assert f"/ {key} " in text or f"`{key}`" in text or f" {key} " in text


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


# ------------------------------------------------------------ 三种关系
#
# agent 是最容易把这三种关系搞混的读者：它看不到网页上那些括弧和曲线，
# MCP 渲染出来的文本就是它对结构的全部认知。**渲染里不说，就等于这个功能不存在。**


def _fork_fixture(be):
    """011 底下两条互斥候选；002b 那条支线的产物又汇回到 002 这条线上的 004。"""
    call(be, "trace_new_step", project="alpha", title="根", decision="类别不平衡怎么处理？")
    call(be, "trace_new_step", project="alpha", parent="001", title="调采样权重",
         branch="alternative | 先试最便宜的")
    call(be, "trace_new_step", project="alpha", parent="001", title="改损失函数",
         branch="alternative")
    call(be, "trace_new_step", project="alpha", parent="002b", title="支线产物")
    call(be, "trace_new_step", project="alpha", parent="002", title="主路径后续",
         inputs=["003 | scores.csv"])


def test_the_tree_tells_the_three_kinds_of_edge_apart(be):
    """缩进只表达「谁挂在谁下面」。它表达不了「这两条只能选一条」，
    也表达不了「那条支线的产物又回到了另一条线上」——不标出来，agent 会把
    一条候选当成主线接着往下做。"""
    _fork_fixture(be)
    out = call(be, "trace_read", project="alpha")
    line = next(l for l in out.splitlines() if l.strip().startswith(("●", "○", "▣")) and " 002 " in l)
    assert "候选：先试最便宜的" in line, out
    assert "⑂ 岔路口" in out and "未决 · 2 选 1" in out
    assert "汇回→ 004" in out and "汇回← 003" in out


def test_the_tree_sums_up_how_many_forks_are_still_undecided(be):
    """「我还有几个岔路口悬着」逐个节点看是看不出来的，而它是隔几天回来
    第一句要问的话。**它是待办不是缺陷**，所以不写进警告那一栏。"""
    _fork_fixture(be)
    out = call(be, "trace_read", project="alpha")
    head, _, warn = out.partition("⚠")
    assert "还有 1 个岔路口没做决定（待办，不是缺陷）" in head
    assert "undecided" not in head.lower()
    assert "岔路口" in warn, "内核那条提醒照旧出现在警告栏里，这里只是多说一次人话"


def test_reading_a_candidate_says_it_is_one_of_a_group_that_only_keeps_one(be):
    _fork_fixture(be)
    out = call(be, "trace_read", project="alpha", step="002")
    assert "互斥候选" in out and "同组的其他候选: 002b" in out
    assert "类别不平衡怎么处理？" in out, "在决定什么写在父节点上，读候选时也得看得到"
    assert "只能选一条走下去" in out
    assert "先试最便宜的" in out, "这个候选自己的角度"


def test_reading_a_fork_point_says_what_is_being_decided(be):
    _fork_fixture(be)
    out = call(be, "trace_read", project="alpha", step="001")
    assert "决策分叉点" in out and "候选（只能选一条走下去）: 002 / 002b" in out
    assert "类别不平衡怎么处理？" in out


def test_a_fork_without_the_question_says_that_only_a_human_can_write_it(be):
    """候选有谁、选中了谁都算得出来，唯独「在决定什么」推导不出来。
    不点破的话 agent 只会以为这一栏是可选的装饰。"""
    call(be, "trace_new_step", project="alpha", title="根")
    call(be, "trace_new_step", project="alpha", parent="001", title="A", branch="alternative")
    call(be, "trace_new_step", project="alpha", parent="001", title="B", branch="alternative")
    out = call(be, "trace_read", project="alpha", step="001")
    assert "只能人写" in out and "decision" in out


def test_reading_a_step_marks_which_input_lines_are_rejoins(be):
    """同一行 `input:`，「顺着往下走读了上一步的产物」和「另一条支线的产物回到
    这条路上」是两件事。混成一样，「那条废掉的支其实还在喂着主线」就永远看不见。"""
    _fork_fixture(be)
    out = call(be, "trace_read", project="alpha", step="004")
    assert "⇠ 汇回: 003 的产物参与了本步（两条线在 001 分开）" in out
    assert "[汇回：来自另一条支线]" in out
    assert "别用 branch 去表达它" in out


def test_read_forks_lists_only_the_undecided_ones_unless_asked(be):
    _fork_fixture(be)
    call(be, "trace_new_step", project="alpha", title="另一个根")
    call(be, "trace_new_step", project="alpha", parent="005", title="C", branch="alternative")
    call(be, "trace_new_step", project="alpha", parent="005", title="D", branch="alternative",
         status="dead")
    out = call(be, "trace_read", project="alpha", forks=True)
    assert "共 2 个岔路口，其中 1 个还没做决定" in out
    assert "⑂ 001" in out and "⑂ 005" not in out
    assert "⑂ 005" in call(be, "trace_read", project="alpha", forks=True, all=True)


def test_the_undecided_wording_never_blames_anyone(be):
    """措辞一带责备味，人就会为了让输出干净随手把一条支标成 dead ——
    那是拿假结论换绿色，而假结论正是这套系统要防的东西。"""
    _fork_fixture(be)
    out = call(be, "trace_read", project="alpha", forks=True)
    assert "常态" in out and "不是错" in out
    for word in ("忘了", "应该", "遗漏", "错误"):
        assert word not in out, f"未决的岔路口不是毛病，别用「{word}」"


def test_marking_the_last_rival_dead_reports_the_fork_as_settled(be):
    """把一个候选标成 dead **就是**做出选择。改完不回执，做决定的那一刻反而是
    整条链上唯一没有回音的一步。"""
    _fork_fixture(be)
    out = call(be, "trace_update_step", project="alpha", step="002b", status="dead")
    assert "已定 → 002" in out
    assert "只有一个候选" not in out


def test_marking_a_single_step_as_a_candidate_says_the_group_is_not_a_choice_yet(be):
    """一组只有一个候选＝还不是选择，而这一步**不报错**（另一条支可能还没建）。
    不当场说一句，人会以为自己已经记下了一个岔路口。"""
    call(be, "trace_new_step", project="alpha", title="根")
    call(be, "trace_new_step", project="alpha", parent="001", title="A")
    out = call(be, "trace_update_step", project="alpha", step="002", branch="alternative")
    assert "只有一个候选" in out
    assert "001 上还没写 decision" in out


def test_taking_the_candidacy_back_is_one_call(be):
    """标错了要能改回来。收得进去、撤不回来的字段等于一次手滑就永久留在那儿。"""
    call(be, "trace_new_step", project="alpha", title="根")
    call(be, "trace_new_step", project="alpha", parent="001", title="A", branch="alternative")
    call(be, "trace_update_step", project="alpha", step="002", branch="")
    assert "互斥候选" not in call(be, "trace_read", project="alpha", step="002")


def test_flow_separates_rejoins_from_ordinary_data_dependencies(be):
    _fork_fixture(be)
    out = call(be, "trace_flow", project="alpha", step="004", direction="up")
    assert "⇢ 汇回：另一条支线，两条线在 001 分开" in out
    assert "树上看不见这条边" in out


def test_the_branch_description_keeps_the_three_relations_apart(be):
    """agent 手上唯一的说明就是这段描述。三件必须说清的事：
    「支线不等于互斥候选」「选了哪个＝其余标 dead」「汇回是 inputs 不是 branch」。"""
    d = next(t for t in M.TOOLS if t["name"] == "trace_new_step")["inputSchema"]
    b = d["properties"]["branch"]["description"]
    assert "只能选一条走下去" in b
    assert "不是** alternative" in b or "**不是** alternative" in b, "支线≠互斥候选要点破"
    assert "status=dead" in b, "「选了哪个」是从 dead 派生的，没有第二个字段"
    assert "`inputs`" in b and "汇回" in b, "汇回不要用 branch 表达"
    q = d["properties"]["decision"]["description"]
    assert "只能人写" in q and "父节点" in q


def test_the_inlined_standard_explains_the_three_kinds_of_edge(be):
    """pip 装的机器上没有 FORMAT.md，instructions 是唯一一定送达的通道。
    这三条不进去，那台机器上的 agent 永远不会写 branch / decision。"""
    ins = M.INSTRUCTIONS
    assert "branch: alternative" in ins and "decision:" in ins
    assert "status: dead" in ins or "status=dead" in ins or "标 dead" in ins
    for code in ("lone_alternative", "fork_without_decision", "undecided_fork"):
        assert code in ins, f"{code} 没送到 agent 手上"
    assert "trace_read(forks=true)" in ins


def test_the_translation_side_ignores_the_two_new_structural_keys(be):
    """`branch` / `decision` 是结构，不是正文。写进译文就是双真相源。"""
    import trace_core as core  # noqa: PLC0415

    for k in ("branch", "decision"):
        assert k in M.TR_STRUCT_KEYS and k in core.TR_STRUCT_KEYS
    with pytest.raises(M.ToolError):
        M._reject_front_matter("---\nbranch: alternative\n---\n\n## Why\na\n")


# ══════════════════════════════ 两条路径：开发路径 ↔ 定稿流程
#
# 这一组全部在防同一个坏结果：**agent 把开发路径当成唯一的真相，然后照着一棵
# 含 dead 的树去复现**。那棵树上有作者自己判定走不通的步骤，照着它跑一遍，
# 得到的是一次注定失败的复现，而失败的原因不在数据也不在代码，在读错了图。


def _pipeline_fixture(be):
    """001 清洗 → 002 死路 → 002b 主实验（吃 002 的产物）。002 是 dead。

    刻意让成果的上游**穿过一条 dead**：那是这一整套推导最该说话的形状——
    流程照样给得出来（dead 被剔掉、上游接过去），但「你的结果建立在一条自己
    判定走不通的路上」必须被指名说出来。
    """
    body = ("## 为什么\n要试\n\n## 做了什么\n跑了 `python train.py`\n\n"
            "## 结果\nAUC 0.91\n\n## 结论\n成了\n")
    call(be, "trace_new_step", project="alpha", title="清洗数据", status="done", body=body,
         commit="c1d2e3f",
         paths=["/blue/lab/clean | output | 去重后的训练集 | size=12884901888 sha256=aabbccdd"])
    call(be, "trace_new_step", project="alpha", parent="001", title="试了 focal loss",
         status="dead", body=body)
    call(be, "trace_new_step", project="alpha", parent="002", title="主实验 AUC 0.91",
         status="done", body=body, inputs=["002 | scores.csv"],
         code=["snapshot | /orange/lab/snap/20260809 | manifest=MANIFEST.md5 n=43"])


def test_a_project_without_a_declared_result_is_told_what_to_do_not_that_it_is_broken(be):
    """空态是**常态**。写成缺陷，人就会随手指一步当成果——拿假结论换绿色。"""
    out = call(be, "trace_pipeline", project="alpha")
    assert "不是缺陷" in out
    assert "trace_result" in out, "得说清下一步调哪个工具，不然这条提示落不了地"
    assert "错误" not in out and "警告" not in out


def test_the_pipeline_tool_description_keeps_the_two_paths_apart(be):
    """agent 最容易犯的错**不会报错**：照着开发路径那棵树去复现。

    工具描述是它唯一读得到的说明，两条路径的分工必须写死在这里。
    """
    d = next(t for t in M.TOOLS if t["name"] == "trace_pipeline")["description"]
    assert "开发路径" in d and "定稿流程" in d
    assert "trace_read" in d, "得点名另一条路径是哪个工具，不然分不清自己在看什么"
    assert "dead" in d and "复现" in d, "「照着含 dead 的树复现」这个具体后果要说出来"
    assert "Methods" in d


def test_marking_a_result_is_described_as_the_heavy_decision_it_is(be):
    """它决定整条流程长什么样、论文附录里出现哪几步。描述成一个字段就是降级。"""
    d = next(t for t in M.TOOLS if t["name"] == "trace_result")["description"]
    assert "Methods" in d and "唯一写下来的" in d
    assert "dead" in d, "把 dead 的一步定成成果会被拒，得先说"
    assert "不存在" in d, "悬空的成果让整条流程静默变空，这条也得先说"


def test_the_derived_pipeline_names_the_dead_step_it_leans_on(be):
    """流程里出现 dead ＝ 结果依赖着一条自己已经放弃的路。**必须指名**。"""
    _pipeline_fixture(be)
    out = call(be, "trace_result", project="alpha", step="003", note="主结果")
    assert "002" in out
    assert "已经放弃" in out or "dead" in out
    assert "✕ 002" in out, "被剔掉的那一步要列出来，不然 001 看起来像凭空多出来的"


def test_the_pipeline_drops_dead_steps_but_keeps_the_upstream_edge(be):
    """剔掉 002 之后 001 不能变成孤点——它的字节确实流进了成果，只是路过了一段废路。"""
    _pipeline_fixture(be)
    call(be, "trace_result", project="alpha", step="003", note="主结果")
    p = be.pipeline("alpha")["pipeline"]
    assert p["order"] == ["001", "003"]
    assert p["dead"] == ["002"]
    assert any(e["from"] == "001" and e["to"] == "003" and e["via"] == ["002"]
               for e in p["edges"]), "上游没接过去，图上就断了一口"


def test_the_methods_draft_carries_commands_code_and_checksums(be):
    """写论文时真正要抄的四样：做了什么、命令、代码在哪、产物与校验和。"""
    _pipeline_fixture(be)
    call(be, "trace_result", project="alpha", step="003", note="主结果")
    md = call(be, "trace_pipeline", project="alpha", methods=True)
    assert "python train.py" in md, "「做了什么」那一节的原文没进去，别人照着做不出来"
    assert "c1d2e3f" in md and "/orange/lab/snap/20260809" in md and "MANIFEST.md5" in md
    assert "/blue/lab/clean" in md and "aabbccdd" in md, "产物位置和校验和是溯源的另一半"
    assert "初稿" in md and "不是成品" in md, "不说清是草稿，就会被原样投出去"
    assert "试了 focal loss" in md, "被剔掉的那一步要单列——「这条路试过、没走通」论文里最缺"


def test_the_methods_draft_does_not_invent_paper_prose(be):
    """它只把记录里已有的事实排一遍。编出来的句子读着像成品，而实验没人核对过。"""
    _pipeline_fixture(be)
    call(be, "trace_result", project="alpha", step="003", note="主结果")
    md = call(be, "trace_pipeline", project="alpha", methods=True)
    for invented in ("我们提出", "本文", "实验表明", "结果显示", "综上所述"):
        assert invented not in md, f"生成器替用户编了一句论文腔：{invented}"
    # 这条承诺也要写进 methods 那个参数的描述里：agent 拿到一份「事实清单」，
    # 下一个动作十有八九是「帮你润色成 Methods」——不拦住，编出来的句子就交出去了。
    d = next(t for t in M.TOOLS if t["name"] == "trace_pipeline")["inputSchema"]
    assert "论文腔" in d["properties"]["methods"]["description"]


def test_a_missing_what_section_says_so_instead_of_going_quiet(be):
    """「记录里这一节是空的」比一段空白有用得多：它是一条待办，空白只是个洞。"""
    call(be, "trace_new_step", project="alpha", title="没写做了什么",
         status="done", body="## 为什么\n要试\n")
    call(be, "trace_result", project="alpha", step="001", note="主结果")
    md = call(be, "trace_pipeline", project="alpha", methods=True)
    assert "这一节是空的" in md


def test_an_english_record_still_gets_its_what_section_into_methods(be):
    """小节名是中英两套。认死中文的话，一份 `lang: en` 的记录会整篇变成「空的」。"""
    call(be, "trace_new_step", project="alpha", title="English record", status="done",
         lang="en", body="## Why\nbecause\n\n## What\nran `python eval.py --seed 0`\n")
    call(be, "trace_result", project="alpha", step="001", note="main result")
    md = call(be, "trace_pipeline", project="alpha", methods=True)
    assert "python eval.py --seed 0" in md
    assert "这一节是空的" not in md


def test_the_exported_figure_is_self_contained_and_scriptless(be):
    """自包含 SVG：审稿系统会把带脚本的图直接拒掉，引了外链的图在别人机器上会散架。"""
    _pipeline_fixture(be)
    call(be, "trace_result", project="alpha", step="003", note="主结果")
    svg = M.pipeline_svg(be.pipeline("alpha"))
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "<script" not in svg and "onload" not in svg
    bare = svg.replace("http://www.w3.org/2000/svg", "")
    assert "http://" not in bare and "https://" not in bare, "引了外部资源"
    assert "@import" not in svg and "<image" not in svg and "<foreignObject" not in svg


def test_the_exported_figure_does_not_rely_on_colour_alone(be):
    """黑白打印和色觉障碍下要读得出来：关系靠线型和文字，不靠颜色。"""
    import re as _re  # noqa: PLC0415

    _pipeline_fixture(be)
    call(be, "trace_result", project="alpha", step="003", note="主结果")
    svg = M.pipeline_svg(be.pipeline("alpha"))
    assert "stroke-dasharray" in svg, "「中间经过了被剔掉的步骤」得有非颜色的通道"
    assert "已剔除" in svg, "虚线还要配一句文字，光靠线型也是猜"
    assert "★成果" in svg and "◆最弱一环" in svg, "两个关键角色要有文字标记"
    # 全图只有黑白灰：任何一个 R≠G≠B 的颜色都说明有信息挂在色相上。
    for hexcolor in sorted(set(_re.findall(r"#([0-9a-fA-F]{6})", svg))):
        r, g, b = hexcolor[0:2], hexcolor[2:4], hexcolor[4:6]
        assert r == g == b, f"#{hexcolor} 不是灰阶——影印之后这条信息就没了"


def test_every_export_is_byte_for_byte_deterministic(be):
    """P3：两次生成逐字节一致，否则「重新生成」会在 diff 里制造假变更。"""
    _pipeline_fixture(be)
    call(be, "trace_result", project="alpha", step="003", note="主结果")
    p1, p2 = be.pipeline("alpha"), be.pipeline("alpha")
    for fn in (M.pipeline_svg, M.pipeline_methods, M.pipeline_page):
        assert fn(p1) == fn(p2), f"{fn.__name__} 不是纯函数"


def test_the_standalone_page_carries_only_the_final_pipeline(be):
    """发给合作者的那一页：无脚本、无外链，而且不含开发路径上那些走不通的步骤。"""
    _pipeline_fixture(be)
    call(be, "trace_result", project="alpha", step="003", note="主结果")
    page = M.pipeline_page(be.pipeline("alpha"))
    assert "<script" not in page
    assert 'src="http' not in page and 'href="http' not in page
    assert "主实验 AUC 0.91" in page


def test_the_pipeline_exception_is_passed_through_on_create_and_update(be):
    """两条门面都要透传 `pipeline`。**漏了不会报错**，只会静默丢掉——
    agent 以为自己排除了一步，而它照样出现在论文的 Methods 里。"""
    import trace_write as W  # noqa: PLC0415

    call(be, "trace_new_step", project="alpha", title="探索", status="done",
         pipeline="exclude | 探索性的，成功了但没进最终流程")
    assert W.load(be._sd("alpha"))["001"].pipeline == "exclude"
    call(be, "trace_update_step", project="alpha", step="001",
         pipeline="include | 想清楚了，它确实是流程的一环")
    assert W.load(be._sd("alpha"))["001"].pipeline == "include"


def test_updating_the_pipeline_exception_says_what_it_just_did(be):
    """`pipeline:` 除了改变一份导出之外在界面上不留痕迹。回执不说，就没人验得了。"""
    call(be, "trace_new_step", project="alpha", title="探索", status="done")
    out = call(be, "trace_update_step", project="alpha", step="001",
               pipeline="exclude | 探索性的")
    assert "不算" in out and "trace_pipeline" in out


def test_both_new_step_and_update_step_advertise_the_pipeline_field(be):
    """schema 里没有的字段，agent 根本不会去传。"""
    for tool in ("trace_new_step", "trace_update_step"):
        props = next(t for t in M.TOOLS if t["name"] == tool)["inputSchema"]["properties"]
        assert "pipeline" in props, f"{tool} 的 schema 少了 pipeline"
        d = props["pipeline"]["description"]
        assert "理由" in d, "不带理由的例外分不清是决定还是误点，这条必须写进描述"
        assert "默认" in d, "得先劝人别动它，否则会被当成常规字段用"


def test_the_instructions_tell_agents_which_path_to_reproduce_from():
    """pip 装的机器上没有任何文档，instructions 是唯一一定送达的通道。"""
    ins = M.INSTRUCTIONS
    assert "trace_pipeline" in ins and "trace_result" in ins
    assert "开发路径" in ins and "定稿流程" in ins
    assert "复现" in ins, "「复现时读哪一条」正是这两条路径分家要解决的问题"


def test_the_translation_side_ignores_result_and_pipeline_too(be):
    """译文里写一句 `pipeline: exclude` 会被读侧一个字不看地丢掉。

    后果比别的结构键重：「我明明排除了它」和「它还在 Methods 里」同时成立，
    而人只会去怀疑推导错了。
    """
    import trace_core as core  # noqa: PLC0415

    for k in ("result", "pipeline"):
        assert k in M.TR_STRUCT_KEYS and k in core.TR_STRUCT_KEYS
    with pytest.raises(M.ToolError):
        M._reject_front_matter("---\npipeline: exclude\n---\n\n## Why\na\n")


# ------------------------------------------------------------ 章节
#
# 这一组全部在防同一个坏结果：**agent 把「项目」「章节」「分叉」三样搞混**。
# 三样都合法、都不报错，而选错的后果各不相同：
#   · 该分章却开了新项目 → id 各自从 001 开始，两块记录再也拼不回一篇论文；
#   · 该分叉却分了章     → 「只能选一条」这句话丢了，读侧给两块各导一段 Methods；
#   · 该分章却分了叉     → 主实验和消融被当成互斥候选，其中一块迟早被标 dead。
# 第二个要防的坏结果是**给每一步都标一遍 chapter**：那正好毁掉沿树继承的全部好处。


def _chapter_fixture(be):
    """001 清洗 → 002 主实验★（都未分章）；003 开消融（声明 chapter）→ 004 消融汇总★。

    004 的 `input:` 同时指着 003 和主实验的 002 —— **那条跨章节的边就是「消融是
    对着主结果测的」**，它必须被标出来而不是藏起来。
    """
    body = ("## 为什么\n要试\n\n## 做了什么\n跑了 `python train.py`\n\n"
            "## 结果\nAUC 0.91\n\n## 结论\n成了\n")
    call(be, "trace_new_step", project="alpha", title="清洗数据", status="done", body=body,
         commit="c1d2e3f", paths=["/blue/lab/clean | output | 训练集 | size=12884901888"])
    call(be, "trace_new_step", project="alpha", parent="001", title="主实验 AUC 0.91",
         status="done", body=body, commit="c1d2e3f")
    call(be, "trace_new_step", project="alpha", parent="002", title="拿掉注意力模块",
         status="done", body=body, commit="c1d2e3f",
         chapter="消融实验 | 逐个拿掉模块，对着主实验的 002 比")
    call(be, "trace_new_step", project="alpha", parent="003", title="消融汇总表",
         status="done", body=body, commit="c1d2e3f",
         inputs=["003 | 逐个拿掉之后的数字", "002 | 主结果那一版权重"])
    call(be, "trace_result", project="alpha", step="002", note="主结果")
    call(be, "trace_result", project="alpha", step="004", note="图 4 的消融")


def test_the_chapter_field_reaches_disk_from_both_step_tools(be):
    """漏传的症状是**静默丢字段**：调用方填了 chapter，返回成功，磁盘上没有。"""
    import trace_write as W  # noqa: PLC0415

    call(be, "trace_new_step", project="alpha", title="开消融", chapter="消融实验 | 逐个拿掉模块")
    assert W.load(be._sd("alpha"))["001"].chapter == "消融实验"
    call(be, "trace_update_step", project="alpha", step="001", chapter="主实验")
    assert W.load(be._sd("alpha"))["001"].chapter == "主实验"
    call(be, "trace_update_step", project="alpha", step="001", chapter="")
    assert W.load(be._sd("alpha"))["001"].chapter == "", "空串＝撤销，回到沿 parent 继承"


def test_both_step_tools_advertise_chapter_and_keep_the_three_things_apart(be):
    """schema 里没有的字段 agent 不会传；而这个字段**主要的错法是选错了那一样**。"""
    for tool in ("trace_new_step", "trace_update_step"):
        props = next(t for t in M.TOOLS if t["name"] == tool)["inputSchema"]["properties"]
        assert "chapter" in props, f"{tool} 的 schema 少了 chapter —— 等于这个字段不存在"
        d = props["chapter"]["description"]
        assert "第一步" in d and "继承" in d, \
            "不说「只标在第一步、往下继承」，agent 会给二十步各标一遍，继承的好处全没了"
        assert "互不排斥" in d and "只能选一个" in d, \
            "章节和分叉的分界要逐字写出来：拿章节表达分叉不报错，只会把「只能选一条」弄丢"
        assert "不同的研究" in d, \
            "章节和项目的分界同样要说：该分章却开了新项目，两块记录再也拼不回一篇论文"


def test_updating_the_chapter_says_how_many_steps_came_along(be):
    """换章**磁盘上只动一行**，后果却是整棵子树跟着换。回执不说，人就发现不了。"""
    _chapter_fixture(be)
    out = call(be, "trace_update_step", project="alpha", step="003", chapter="消融")
    assert "消融" in out
    assert "1 步" in out, "跟着换章的那几步要报出来 —— 它们自己一个字都没写"
    assert "继承" in out


def test_reading_one_step_tells_declared_apart_from_inherited(be):
    """**归属**和**这一行写在哪**是两件事。混用会让继承来的整条子树看着像未分章。"""
    _chapter_fixture(be)
    at = call(be, "trace_read", project="alpha", step="003")
    assert "章节: 消融实验" in at and "就写在这一步" in at
    down = call(be, "trace_read", project="alpha", step="004")
    assert "章节: 消融实验" in down, "继承来的一样要显示归属，否则它看着像未分章"
    assert "继承" in down and "003" in down, "还要说清那一行写在哪 —— 改名要去改的是那一步"
    main = call(be, "trace_read", project="alpha", step="001")
    assert "章节:" not in main, "未分章的步骤不该多出一行 —— 那不是缺陷"


def test_listing_chapters_answers_can_someone_redo_the_ablation(be):
    """分章之后第一个要问的问题：消融那部分多少步、能被追到哪一级、有没有成果。"""
    _chapter_fixture(be)
    out = call(be, "trace_read", project="alpha", chapters=True)
    assert "消融实验" in out and "逐个拿掉模块" in out
    assert "2 步" in out, "步数是这份清单最基本的一列"
    assert "004" in out and "★ 成果" in out, "有没有成果 = 能不能单独导一段 Methods"
    assert "未分章" in out and "不是缺陷" in out, \
        "多数项目的主线本来就没起名字，这一组要列出来且不带缺失感"
    assert "互不排斥" in out and "分叉" in out, "清单本身也要把章节和分叉分开"


def test_the_cross_chapter_edge_is_shown_not_hidden(be):
    """消融吃主实验的产物，那条边说的正是「消融是对着主结果测的」。"""
    _chapter_fixture(be)
    out = call(be, "trace_read", project="alpha", chapters=True)
    assert "跨章节的边" in out
    assert "004 ← 002" in out, "input 那条跨章节的边要指名两头"


def test_a_project_without_any_chapter_is_told_how_not_that_it_is_broken(be):
    """没分章是**常态**。写成缺陷，人就会为了让输出干净随手分两块。"""
    call(be, "trace_new_step", project="alpha", title="根")
    out = call(be, "trace_read", project="alpha", chapters=True)
    assert "不是缺陷" in out and "错误" not in out and "警告" not in out
    assert "chapter:" in out, "得给出照抄就能用的那一行，不然这条提示落不了地"


def test_each_chapter_has_its_own_pipeline_and_its_own_result(be):
    """`result:` 指的那一步在哪一章，这条流程就属于哪一章 —— 论文里本来就是两段。"""
    _chapter_fixture(be)
    whole = be.pipeline("alpha")
    ab = be.pipeline("alpha", "消融实验")
    main = be.pipeline("alpha", M.CHAPTER_NONE)
    assert whole["pipeline"]["order"] == ["001", "002", "003", "004"]
    assert [r["step"] for r in ab["pipeline"]["results"]] == ["004"], "这一章只报它自己那个成果"
    assert [r["step"] for r in main["pipeline"]["results"]] == ["002"]
    assert main["pipeline"]["order"] == ["001", "002"], \
        "主线那一章不该把消融的步骤算进自己的 Methods"
    assert set(ab["chapter"]["external"]) == {"001", "002"}, \
        "借来的上游要标出来，不能算成本章自己做的"


def test_the_chapter_slice_is_a_subsequence_of_the_one_dag(be):
    """**切分，不是重算。** 各算一遍就会出现「屏幕上讨论的图和投出去的图不是一张」。"""
    _chapter_fixture(be)
    whole = be.pipeline("alpha")["pipeline"]["order"]
    for name in ("消融实验", M.CHAPTER_NONE):
        part = be.pipeline("alpha", name)["pipeline"]["order"]
        assert part == [s for s in whole if s in set(part)], \
            f"{name} 这一章的顺序不是总图的子序列 —— 同一步在两张图上的位置就不一样了"


def test_each_chapter_carries_its_own_level_not_the_projects(be):
    """整份流程的等级是**全项目**最弱的一步；拿它当消融的等级就是让消融替别的章背锅。

    「消融这部分别人能不能重做」是个要单独回答的问题。
    """
    _chapter_fixture(be)
    # 一步记得很差的探索（L0），只有消融那一章够得到它。
    made = call(be, "trace_new_step", project="alpha", parent="002", title="没记全的一步",
                status="done", body="## 为什么\n试试\n")
    weak = made.split("alpha/")[1].split()[0]
    call(be, "trace_update_step", project="alpha", step="004",
         add_inputs=[f"{weak} | 那一版的输出"])
    whole = be.pipeline("alpha")["pipeline"]
    ab = be.pipeline("alpha", "消融实验")["pipeline"]
    main = be.pipeline("alpha", M.CHAPTER_NONE)["pipeline"]
    assert whole["weakest"] == weak and ab["weakest"] == weak
    assert main["weakest"] != weak, f"主线那一章够不到 {weak}，不该替它背锅"
    assert main["level"] != whole["level"], "两个等级各回答各的问题"


def test_a_chapter_really_named_dash_beats_the_sentinel(be):
    """`-` 是「未分章那一组」的记号，而它**通过得了**写入侧的章节名校验。

    取舍是真名优先：一个真叫 `-` 的章节存在时，记号绝不许把它抢走 —— 那会让
    「导出的是哪一章」取决于一个巧合，而其中一份产物会进论文。
    """
    _chapter_fixture(be)
    call(be, "trace_update_step", project="alpha", step="003", chapter=M.CHAPTER_NONE)
    got = be.pipeline("alpha", M.CHAPTER_NONE)
    assert got["chapter"]["name"] == M.CHAPTER_NONE, "真名优先，记号让路"
    assert "004" in got["pipeline"]["order"], "拿到的是那个真章节，不是未分章那一组"


def test_an_unknown_chapter_name_says_which_ones_exist(be):
    """章节名是人起的中文，打错一个字是最常见的失败方式；
    而「没有这一章」和「这一章是空的」在输出上长得一模一样。"""
    _chapter_fixture(be)
    with pytest.raises(M.ToolError) as e:
        be.pipeline("alpha", "消融試驗")
    assert "消融实验" in str(e.value), "必须把有哪几章摆出来"
    assert "猜" in str(e.value), "还要说清这里不做近似匹配 —— 猜错一次，导出的就是另一章"


def test_the_methods_draft_of_one_chapter_marks_what_it_borrowed(be):
    """读 Methods 的人是一步一步读的：「这一步是我们做的还是引用的主实验」
    正是消融那一节最容易被读错的地方。"""
    _chapter_fixture(be)
    md = call(be, "trace_pipeline", project="alpha", chapter="消融实验", methods=True)
    assert "消融实验" in md.splitlines()[0], "抬头必须带章节名，否则两份草稿分不出哪份是哪份"
    assert "借自" in md and "不互斥" in md
    assert "不按章节重编号" in md, "两段草稿里的 id 能直接对照，这一句要说出来"


def test_the_whole_project_pipeline_points_at_the_per_chapter_export(be):
    """能按章节导这件事，不说就没人知道 —— 磁盘上分章只是一行 `chapter:`。"""
    _chapter_fixture(be)
    out = call(be, "trace_pipeline", project="alpha")
    assert "chapter=" in out and "消融实验" in out


def test_the_chapter_exports_stay_byte_identical(be):
    """P3：三样导出是纯函数，按章节切开之后照样逐字节确定。"""
    _chapter_fixture(be)
    a, b = be.pipeline("alpha", "消融实验"), be.pipeline("alpha", "消融实验")
    for fn in (M.pipeline_svg, M.pipeline_methods, M.pipeline_page):
        assert fn(a) == fn(b), f"{fn.__name__} 按章节切开之后不再是纯函数"


def test_the_figure_of_one_chapter_keeps_the_three_hard_constraints(be):
    """无脚本、无外链、不靠色相 —— 一条都没有因为分章而松。"""
    import re as _re  # noqa: PLC0415

    _chapter_fixture(be)
    svg = M.pipeline_svg(be.pipeline("alpha", "消融实验"))
    assert "<script" not in svg and "onload" not in svg
    bare = svg.replace("http://www.w3.org/2000/svg", "")
    assert "http://" not in bare and "https://" not in bare
    assert "消融实验" in svg and "借自" in svg, "借来的那几步在图上也要看得出来"
    for hexcolor in sorted(set(_re.findall(r"#([0-9a-fA-F]{6})", svg))):
        r, g, b = hexcolor[0:2], hexcolor[2:4], hexcolor[4:6]
        assert r == g == b, f"#{hexcolor} 不是灰阶 —— 影印之后「借自」这条信息就没了"


def test_the_standalone_page_of_one_chapter_says_it_is_only_one_chapter(be):
    """发给合作者的那一页，收信人手上没有别的上下文：
    不说这一句，他会把消融那一章读成这个课题的全部方法。"""
    _chapter_fixture(be)
    page = M.pipeline_page(be.pipeline("alpha", "消融实验"))
    assert "<script" not in page
    assert "消融实验" in page and "不互斥" in page


def test_a_chapter_without_a_result_is_not_reported_as_broken(be):
    """一章可以只是探索，成果在别的章。这不是缺陷，但得说清怎么办。"""
    _chapter_fixture(be)
    call(be, "trace_new_step", project="alpha", parent="001", title="另开一条",
         status="done", chapter="数据准备")
    out = call(be, "trace_pipeline", project="alpha", chapter="数据准备")
    assert "不是缺陷" in out and "trace_result" in out
    assert "数据准备" in out


def test_a_payload_nobody_asked_a_chapter_of_has_no_chapter_key(be):
    """现存项目必须完全无感：不问章节，这份 payload 一个键都不许多。"""
    _pipeline_fixture(be)
    call(be, "trace_result", project="alpha", step="003", note="主结果")
    assert "chapter" not in be.pipeline("alpha"), \
        "没按章节要就整个键不出现 —— 和 forest 里那两个键同一条规矩"


def test_the_chapter_filenames_are_derived_not_pasted():
    """章节名**不是路径安全的**：`主实验/数据准备` 合法、`CON` 合法、`..` 合法。

    而且写入侧刻意不折叠大小写（`Ablation` / `ablation` 是两个章节），
    于是两个不同章节能 slug 成同一个名字 —— 静默覆盖就等于少导出一章。
    """
    import trace_write as W  # noqa: PLC0415

    got = M.chapter_export_name(["主实验/数据准备", "CON", "..", "Ablation", "ablation", ""])
    for name, stem in got.items():
        assert "/" not in stem and "\\" not in stem and ".." not in stem, (name, stem)
        assert not stem.endswith("."), (name, stem)
        assert stem.split("-", 1)[-1] not in W.WIN_RESERVED, \
            f"{name} → {stem}：con.svg 在 Windows 上打开的是设备不是文件"
    assert len(set(got.values())) == len(got), "撞名的两章必须消歧，不能后一份静默盖掉前一份"


def test_the_instructions_and_the_format_summary_teach_the_three_way_split():
    """pip 装的机器上没有任何文档，这两段是唯一一定送达的通道。"""
    for text in (M.INSTRUCTIONS, M.FORMAT_ESSENTIALS):
        assert "章节" in text
        assert "互不排斥" in text and "只能选一个" in text, \
            "项目 / 章节 / 分叉三样的分界必须并排说清 —— 选错了不报错"
        assert "第一步" in text and "继承" in text, \
            "不说「只标在第一步」，agent 会给每一步各标一遍"
    assert "不按章节重编号" in M.FORMAT_ESSENTIALS and "不嵌套" in M.FORMAT_ESSENTIALS, \
        "两件刻意不做的事要写出来，否则后来人会当成漏了去补"


def test_the_translation_side_ignores_chapter_too(be):
    """译文里多写一行 `chapter:` 改的不是这一步，是它底下**整棵子树**的归属。"""
    import trace_core as core  # noqa: PLC0415

    assert "chapter" in M.TR_STRUCT_KEYS and "chapter" in core.TR_STRUCT_KEYS
    with pytest.raises(M.ToolError):
        M._reject_front_matter("---\nchapter: Ablation\n---\n\n## Why\na\n")
