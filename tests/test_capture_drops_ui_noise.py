"""采集里那些「存了两遍」和「不是研究材料」的部分。

实测一份 130 MB 的真实采集里，`file-history-snapshot` 一项就占 31.7 MB（24%）——
它是 Claude Code 自己的文件快照，全额进了 outbox、投递带宽和 GitHub 备份，
却没有任何溯源价值。这一组过滤在同一份数据上丢掉 25.7%。

注意这**不影响提示缓存**：缓存是 API 那一侧按 prompt 前缀算的，跟这里抄多少字节无关。
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("TRACE_HOOK_NO_SPAWN", "1")
ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("rt_hook_noise", ROOT / "scripts" / "trace_hook.py")
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)


def compact(value):
    return (json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


@pytest.mark.parametrize("noise_type", [
    "file-history-snapshot", "queue-operation", "bridge-session", "custom-title", "mode",
])
def test_runtime_state_lines_are_dropped(noise_type):
    line = compact({"type": noise_type, "sessionId": "s1", "payload": "x" * 100})
    assert H._scrub_line(line) is None


@pytest.mark.parametrize("kept_type", ["user", "assistant", "system", "attachment"])
def test_real_content_is_kept(kept_type):
    line = compact({"type": kept_type, "uuid": "u1",
                    "message": {"role": "user", "content": "真正的内容"}})
    assert H._scrub_line(line) is not None


def test_the_word_mode_in_prose_does_not_drop_a_real_message():
    """裸类型名太常见 —— 只匹配完整的 `"type":"mode"` 形态。"""
    line = compact({"type": "assistant", "uuid": "u2", "message": {
        "role": "assistant", "content": [{"type": "text", "text": "把 mode 改成 normal 试试"}]}})
    assert H._scrub_line(line) is not None


def test_a_spaced_out_json_form_is_kept_rather_than_guessed_at():
    """匹配不上时故意选「保留」：多存点噪声可以接受，误删研究内容不行。"""
    line = b'{"type": "file-history-snapshot", "messageId": "m1"}\n'
    assert H._scrub_line(line) is not None


def test_noise_filtering_runs_before_the_thinking_scrub_and_costs_nothing_extra():
    """噪声行整行丢掉，不必再走 JSON 解析去找 thinking。"""
    line = compact({"type": "file-history-snapshot", "snapshot": {"thinking": "x"}})
    assert H._scrub_line(line) is None


def test_the_duplicated_image_base64_is_replaced_by_a_placeholder():
    """读图片时 toolUseResult.file.base64 和 message.content 里的图片块是同一份字节。

    实测 50 行、26.2 MB，100% 都能在同一行的 message.content 里找到副本，占整份采集的 20%。
    不直接删而是留占位：「这里曾经有一张多大的图、sha256 是什么」本身也是溯源信息。
    """
    payload = "QUJD" * 500
    line = compact({
        "type": "user", "uuid": "u1",
        "message": {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "data": payload}}]},
        "toolUseResult": {"type": "image", "file": {
            "filePath": "/tmp/shot.png", "base64": payload, "numLines": 0}},
    })
    out = H._scrub_line(line)
    assert out is not None
    value = json.loads(out)
    file_value = value["toolUseResult"]["file"]
    assert file_value["base64"] is None
    note = file_value["research_trace_base64_omitted"]
    assert note["bytes"] == len(payload)
    assert len(note["sha256"]) == 64
    # message.content 里那一份必须原样保留 —— 图本身不能丢
    assert value["message"]["content"][0]["source"]["data"] == payload
    assert len(out) < len(line)


def test_the_structured_envelope_around_it_is_kept():
    """只剥 base64，不动 toolUseResult 的其它字段。

    structuredPatch / filePath / numLines 是「这次编辑改了什么」的证据 ——
    几百 KB 但有溯源价值，不该跟着一起扔。
    """
    line = compact({
        "type": "user", "uuid": "u2", "message": {"role": "user", "content": []},
        "toolUseResult": {"filePath": "/a.py", "structuredPatch": [{"lines": ["-x", "+y"]}],
                          "file": {"base64": "QUJD" * 400, "numLines": 12}},
    })
    value = json.loads(H._scrub_line(line))
    assert value["toolUseResult"]["filePath"] == "/a.py"
    assert value["toolUseResult"]["structuredPatch"] == [{"lines": ["-x", "+y"]}]
    assert value["toolUseResult"]["file"]["numLines"] == 12


def test_a_line_without_that_field_is_returned_untouched():
    """快路径：没有 base64 的行不该被重新序列化（那会白白改动字节）。"""
    line = compact({"type": "assistant", "uuid": "u3",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}})
    assert H._scrub_line(line) == line
