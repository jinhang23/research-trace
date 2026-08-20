"""Recorder 自己的回合不能再喂回采集。

事件层早就挡住了（`_is_trace_orchestration` 对 recorder 的事件返回 True），
但 transcript 层是照单全收的 —— 而 fork 的回合就写在同一个 transcript 文件里。
于是这个环自己转起来：

    recorder 跑完 → transcript 变长 → 新 chunk → `events or chunks` 成立
         ↑                                                        ↓
      派一个 fork  ←──────  Stop hook 要求派 fork  ←──────────────┘

每转一圈烧掉一次完整 fork（实测约 70 万 token），产出恒为 0 nodes。
"""
import importlib.util
import json
import os
from pathlib import Path

os.environ.setdefault("TRACE_HOOK_NO_SPAWN", "1")
ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("rt_hook_loop", ROOT / "scripts" / "trace_hook.py")
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)

from tests.test_trace_hook import PROTOCOL, bind, event, session_root  # noqa: E402


def line(agent_id=None, text="内容", sidechain=False):
    value = {"type": "assistant", "uuid": f"u-{text}-{agent_id}", "isSidechain": sidechain,
             "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    if agent_id:
        value["agentId"] = agent_id
    return (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")


def test_a_recorder_line_is_dropped_and_others_survive():
    ids = frozenset({"rec-1"})
    assert H._scrub_line(line("rec-1", "recorder 自己的心跳", True), ids) is None
    assert H._scrub_line(line("other-agent", "别的子 agent 在干活", True), ids) is not None
    assert H._scrub_line(line(None, "主 agent"), ids) is not None


def test_without_known_recorder_ids_nothing_is_dropped():
    assert H._scrub_line(line("rec-1", "x", True), frozenset()) is not None


def test_a_line_merely_mentioning_the_id_is_kept():
    """主 agent 的正文里提到那个 id（比如派发指令）不该被当成 recorder 的回合。"""
    ids = frozenset({"rec-1"})
    mention = line(None, "Use SendMessage once with to='rec-1'")
    assert H._scrub_line(mention, ids) is not None


def test_recorder_ids_accumulate_and_are_capped():
    state = {}
    for i in range(30):
        H._remember_recorder_id(state, f"agent-{i}")
    assert len(state["recorder_ids"]) == 20
    assert state["recorder_ids"][-1] == "agent-29"
    H._remember_recorder_id(state, "agent-29")
    assert state["recorder_ids"].count("agent-29") == 1


def test_the_recorder_own_turns_never_become_a_transcript_chunk(tmp_path):
    """端到端：recorder 干活时往同一个 transcript 文件追加内容，不该产生新的 chunk。

    判据故意是 chunk 而不是批次数：`Stop` 事件本身就会正当地开一个新批次，
    拿批次数当判据会把「环断没断」和「这一轮有没有正常内容」混在一起。
    环的燃料是 chunk —— `_ensure_batch` 的条件是 `events or chunks`。
    """
    project = bind(tmp_path, "loop")
    data = tmp_path / "data"
    transcript = tmp_path / "session.jsonl"
    transcript.write_bytes(line(None, "用户干了点正事"))

    def fire(name, **extra):
        return H.handle(event(name, project, transcript_path=str(transcript), **extra),
                        data, PROTOCOL)

    fire("UserPromptSubmit", prompt="做事")
    fire("Stop")
    fire("PreToolUse", tool_name="Agent", tool_input={"prompt": f"{H.RECORDER_MARKER} 干活"})
    fire("SubagentStart", agent_id="rec-1")

    root = session_root(data)
    chunks = lambda: len(list((root / "transcripts" / "pending").glob("*.jsonl")))
    before = chunks()

    with transcript.open("ab") as stream:                 # recorder 自己的回合
        for i in range(3):
            stream.write(line("rec-1", f"recorder 第 {i} 步", True))
    fire("SubagentStop", agent_id="rec-1")
    assert chunks() == before, "recorder 自己的回合不该变成 chunk —— 那正是那个环的燃料"

    with transcript.open("ab") as stream:                 # 真正的工作内容照常采集
        stream.write(line(None, "用户又干了点正事"))
    fire("Stop")
    assert chunks() > before, "真内容还是要采的，别把整条采集链一起掐了"
