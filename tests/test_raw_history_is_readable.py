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


PLUMBING = [
    '{"type":"user","message":{"role":"user","content":"[research-trace-recorder] 处理这一批"}}',
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text",'
    '"text":"I\'ll process research-trace batch 1787208372 per the recorder protocol."}]}}',
    '{"type":"user","message":{"role":"user","content":'
    '"Stop hook feedback: Research Trace has durably queued a recorder batch."}}',
]


def test_traces_own_dispatch_turns_are_hidden():
    """记录动作本身不是研究材料。

    hook 那一侧已经不再采集 Recorder 名下的行，但那只对新数据生效；库里存着的旧数据
    仍然带着这些回合，显示出来就是「助手：I'll process research-trace batch …」这种
    对读者毫无意义的东西。
    """
    assert turns("\n".join(PLUMBING)) == []


def test_real_work_next_to_plumbing_still_shows():
    """只挡调度那几条，同一段里真正的工作内容不能跟着消失。"""
    mixed = PLUMBING + [LINES[0]]
    result = turns("\n".join(mixed))
    assert len(result) == 1
    assert "8 Å" in result[0]["text"]


def test_only_our_own_markers_are_matched():
    """只匹配本系统自己产生的字符串，不去猜别人的措辞。"""
    from research_trace.storage import Store
    assert Store._is_plumbing("[research-trace-batch 123]") is True
    assert Store._is_plumbing("我们讨论一下 recorder 的协议") is False
    assert Store._is_plumbing("batch size 设成 32") is False
