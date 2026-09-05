"""Independent Recorder calls are stateless: no parent fork, no resumed conversation.

The earlier design reused one Claude session per project for 12 turns and rotated it.
Every turn already carries the complete packet, so resuming only re-sent every earlier
packet as context (12 turns of ~20k tokens overflowed a 200k window) while the prompt
cache keys on the fixed system prompt + schema prefix, which is identical across
independent calls anyway.
"""

import json
import subprocess

from research_trace import recorder as R


def _result(output):
    return json.dumps({"type": "result", "structured_output": output}) + "\n"


def _run(command, **options):
    if command[1:3] == ["auth", "status"]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"}),
            stderr="",
        )
    return subprocess.CompletedProcess(
        command,
        0,
        stdout=_result({"status": "skip", "records": [], "curations": []}),
        stderr="",
    )


def test_every_call_is_the_same_stateless_command(monkeypatch, tmp_path):
    for key in R.PAID_CREDENTIAL_ENV:
        monkeypatch.delenv(key, raising=False)
    commands = []
    monkeypatch.setattr(R.subprocess, "run", lambda command, **options: commands.append(command) or _run(command))
    worker = R.RecorderWorker(tmp_path / "data", "http://trace")
    for _ in range(3):
        worker._invoke("packet", {"model": "sonnet"}, "project-1")
    model_calls = [command for command in commands if command[1:3] != ["auth", "status"]]
    assert len(model_calls) == 3
    assert model_calls[0] == model_calls[1] == model_calls[2]
    assert "--no-session-persistence" in model_calls[0]
    assert not {"--resume", "--session-id"} & set(model_calls[0])


def test_projects_are_counted_separately_but_share_nothing(monkeypatch, tmp_path):
    for key in R.PAID_CREDENTIAL_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(R.subprocess, "run", _run)
    worker = R.RecorderWorker(tmp_path / "data", "http://trace")
    worker._invoke("packet", {"model": "sonnet"}, "project-1")
    worker._invoke("packet", {"model": "sonnet"}, "project-1")
    worker._invoke("packet", {"model": "haiku"}, "project-2")
    entries = sorted(worker.state["projects"].values(), key=lambda item: item["calls"], reverse=True)
    assert [(item["calls"], item["model"]) for item in entries] == [(2, "sonnet"), (1, "haiku")]
    assert not any("session_id" in item for item in entries), "there is no session to carry over"
