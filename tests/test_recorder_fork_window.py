"""Independent Recorder conversations rotate without using parent forks."""

from research_trace import recorder as R


def test_recorder_conversation_rotates_after_a_bounded_window(tmp_path):
    worker = R.RecorderWorker(tmp_path / "data", "http://trace")
    session, resume = worker._session("project-1", "sonnet")
    assert resume is False
    key = next(iter(worker.state["projects"]))
    worker.state["projects"][key]["turns"] = R.SESSION_TURNS
    rotated, resume = worker._session("project-1", "sonnet")
    assert resume is False and rotated != session


def test_each_project_has_a_separate_recorder_session(tmp_path):
    worker = R.RecorderWorker(tmp_path / "data", "http://trace")
    first, _ = worker._session("project-1", "sonnet")
    second, _ = worker._session("project-2", "sonnet")
    assert first != second