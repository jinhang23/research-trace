import io
import json
import stat
import zipfile

import pytest

from research_trace.entire_evidence import (
    EntireRepository, EvidenceError, extract_code_archive, visible_transcript,
)


def test_transcript_keeps_visible_context_but_not_hidden_blocks():
    row = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "This idea has not been tried."},
        {"type": "thinking", "thinking": "SYNTHETIC_PRIVATE_SENTINEL"},
        {"type": "tool_use", "input": {"reasoning_content": "SYNTHETIC_PRIVATE_SENTINEL", "path": "train.py"}},
    ]}}
    output = visible_transcript(json.dumps(row))
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in output
    assert "This idea has not been tried." in output
    assert "train.py" in output


def test_partial_transcript_is_not_silently_imported():
    with pytest.raises(EvidenceError, match="complete JSONL"):
        visible_transcript('{"type":"user"}\n{"type":')


def archive_of(name, content=b"SCALE = 1\n", mode=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo(name)
        if mode is not None:
            info.external_attr = mode << 16
        archive.writestr(info, content)
    return output.getvalue()


@pytest.mark.parametrize("name", ["../outside.py", "/outside.py", "C:/outside.py", "..\\outside.py"])
def test_snapshot_does_not_escape_new_directory(tmp_path, name):
    target = tmp_path / "snapshot"
    with pytest.raises(EvidenceError):
        extract_code_archive(archive_of(name), target)
    assert not target.exists()


def test_snapshot_rejects_external_symlink(tmp_path):
    with pytest.raises(EvidenceError):
        extract_code_archive(archive_of("shared.py", b"../mutable.py", stat.S_IFLNK | 0o777), tmp_path / "snapshot")


def test_snapshot_is_separate_and_never_overwrites_existing_code(tmp_path):
    target = tmp_path / "snapshot"
    assert extract_code_archive(archive_of("common/normalize.py"), target) == ["common/normalize.py"]
    with pytest.raises(EvidenceError, match="must be new"):
        extract_code_archive(archive_of("common/normalize.py", b"SCALE = 2\n"), target)
    assert (target / "common/normalize.py").read_bytes() == b"SCALE = 1\n"


@pytest.mark.parametrize("revision", ["HEAD", "main", "--help", "abc", "../other"])
def test_collection_requires_immutable_commit(tmp_path, revision):
    with pytest.raises(EvidenceError, match="immutable"):
        EntireRepository(tmp_path, tmp_path / "not-executed").collect(revision)
