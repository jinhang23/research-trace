"""原始历史要能读，而不是把 JSON 倒出来。

「对话原文」是原始历史里最该被读到的一块，此前它显示的是 `search_text[:1000]` ——
一段按字符硬截断的 JSONL，连一个完整的 JSON 行都不保证有。解析放在服务端做，
因为客户端拿到的就是那段截断后的 blob，怎么都解析不出来。
"""
import pytest

pytest.importorskip("fastapi")

from research_trace.storage import Store

LINES = [
    '{"type":"user","message":{"role":"user","content":"把 8 Å 口袋切出来"}}',
    '{"type":"assistant","message":{"role":"assistant","content":['
    '{"type":"text","text":"先看清洗后的条目数。"},{"type":"tool_use","name":"Bash"}]}}',
    '{"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"6767"}]}}',
    '{"type":"assistant","isSidechain":true,"message":{"role":"assistant","content":['
    '{"type":"text","text":"子 agent 在干活"}]}}',
]


def turns(text, **kw):
    return Store._transcript_turns(text, **kw)


def test_a_human_message_reads_as_a_human_message():
    first = turns("\n".join(LINES))[0]
    assert first["who"] == "你"
    assert "8 Å" in first["text"]


def test_tool_output_is_not_labelled_as_something_you_said():
    """Claude Code 里工具输出是 user 角色 —— 不区分的话整页都是「你说：（工具输出）」。"""
    labels = [turn["who"] for turn in turns("\n".join(LINES))]
    assert labels[:3] == ["你", "助手", "工具"]


def test_a_tool_call_shows_which_tool():
    assert "Bash" in turns("\n".join(LINES))[1]["text"]


def test_subagent_turns_are_marked_so_the_main_thread_stays_readable():
    assert turns("\n".join(LINES))[3]["sidechain"] is True
    assert turns("\n".join(LINES))[0]["sidechain"] is False


def test_a_truncated_half_line_does_not_break_the_rest():
    """preview 是按字符截断的，最后一行经常是半条 JSON。"""
    text = "\n".join(LINES) + '\n{"type":"user","message":{"role":"user","content":"半条'
    assert len(turns(text)) >= 3


def test_noise_only_content_yields_nothing_rather_than_raw_json():
    noise = '{"type":"queue-operation","operation":"enqueue"}\n{"type":"mode","mode":"normal"}'
    assert turns(noise) == []


def test_the_turn_count_is_bounded():
    many = "\n".join(LINES * 20)
    assert len(turns(many, limit=4)) == 4
