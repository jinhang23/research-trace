"""The independent Recorder cannot feed its own work back into a bound project."""

from research_trace import recorder as R


def test_recorder_workspace_is_separate_from_the_research_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    worker = R.RecorderWorker(tmp_path / "plugin-data", "http://trace")
    assert worker.workspace != project
    assert not worker.workspace.is_relative_to(project)


def test_recorder_prompt_forbids_investigation_and_tools():
    text = " ".join(R.SYSTEM_PROMPT.lower().split())
    assert "do not run commands" in text
    assert "use tools" in text
    assert "observe only the evidence packet" in text
