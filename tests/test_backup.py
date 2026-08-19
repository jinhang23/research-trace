from __future__ import annotations

import base64
import shutil
import subprocess

import pytest

from research_trace.backup import (
    export_backup,
    restore_backup,
    rewrite_backup_history,
    sync_git_backup,
    verify_backup,
)
from research_trace.storage import Store, ValidationError

GIT = shutil.which("git")
requires_git = pytest.mark.skipif(GIT is None, reason="git is required for backup repo tests")

# 备份最坏的失败模式：文本里带这些字符时 export/verify 都说没问题，restore 却炸。
# str.splitlines() 会在它们处断行，而 verify 是按 b"\n" 数行的。
SEPARATORS = "line one\u2028line two\u2029paragraph\u0085next"


def _git(repo, *args, check=True):
    return subprocess.run(
        [GIT, "-C", str(repo), *args], check=check, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _init_repo(path):
    subprocess.run([GIT, "init", "-q", "-b", "main", str(path)], check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "trace test"),
                       ("commit.gpgsign", "false")):
        _git(path, "config", key, value)
    return path


def _repo_with_remote(tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run([GIT, "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", str(bare))
    return repo, bare


def populated_store(path):
    store = Store(path)
    project = store.create_project("RNA project", overview="Current understanding")
    chapter = store.create_chapter(project["id"], "Data understanding")
    first = store.record_node(
        project["id"], idempotency_key="n1", chapter_id=chapter["id"],
        title="Inspect counts", body="Found a batch-shaped pattern.",
    )
    store.record_node(
        project["id"], idempotency_key="n2", chapter_id=chapter["id"],
        parent_id=first["id"], title="Check confounding",
    )
    store.attach(
        project["id"], target_type="node", target_id=first["id"], name="pca.png",
        data_base64=base64.b64encode(b"image bytes").decode("ascii"), mime_type="image/png",
    )
    store.ingest(
        batch_id="b1", project_id=project["id"],
        session={"id": "s1", "source": "claude-code"}, agents=[],
        events=[{"event_id": "e1", "event_type": "Stop", "payload": {"message": "done"}}],
        transcript_chunks=[{"chunk_id": "t1", "content": '{"message":"raw history"}\n'}],
    )
    return store, project


def test_backup_is_deterministic_verified_and_restores_an_empty_store(tmp_path):
    source, project = populated_store(tmp_path / "source")
    target = tmp_path / "backup"
    first_manifest = export_backup(source, target)
    first_bytes = {
        str(path.relative_to(target)): path.read_bytes()
        for path in target.rglob("*") if path.is_file()
    }
    second_manifest = export_backup(source, target)
    second_bytes = {
        str(path.relative_to(target)): path.read_bytes()
        for path in target.rglob("*") if path.is_file()
    }
    assert first_manifest == second_manifest
    assert first_bytes == second_bytes
    verify_backup(target)

    restored = Store(tmp_path / "restored")
    result = restore_backup(target, restored)
    assert result["restored"] is True
    assert restored.health()["counts"] == source.health()["counts"]
    detail = restored.get_project(project["id"])
    assert [node["title"] for node in detail["nodes"]] == ["Inspect counts", "Check confounding"]
    assert detail["nodes"][1]["parent_id"] == detail["nodes"][0]["id"]
    assert restored.search("raw history", project_id=project["id"])[0]["scope"] == "transcript"


def test_backup_verification_rejects_tampering_and_restore_rejects_nonempty_store(tmp_path):
    source, _project = populated_store(tmp_path / "source")
    target = tmp_path / "backup"
    export_backup(source, target)
    projects = target / "tables" / "projects.jsonl"
    projects.write_bytes(projects.read_bytes() + b"tamper")
    with pytest.raises(ValidationError, match="checksum"):
        verify_backup(target)

    export_backup(source, target)
    occupied = Store(tmp_path / "occupied")
    occupied.create_project("Already here")
    with pytest.raises(ValidationError, match="empty"):
        restore_backup(target, occupied)


def test_restore_survives_line_and_paragraph_separator_characters(tmp_path):
    store = Store(tmp_path / "source")
    project = store.create_project("Project", overview="overview " + SEPARATORS)
    store.record_node(
        project["id"], idempotency_key="n1", title="Weird text", body=SEPARATORS
    )
    store.ingest(
        batch_id="b1", project_id=project["id"], session={"id": "s1"}, agents=[],
        events=[{"event_id": "e1", "event_type": "Stop", "payload": {"text": SEPARATORS}}],
        transcript_chunks=[{"chunk_id": "c1", "content": SEPARATORS + "\n"}],
    )
    target = tmp_path / "backup"
    export_backup(store, target)
    verify_backup(target)

    restored = Store(tmp_path / "restored")
    restore_backup(target, restored)
    detail = restored.get_project(project["id"])
    assert detail["nodes"][0]["body"] == SEPARATORS
    assert detail["overview"] == "overview " + SEPARATORS
    assert restored.health()["counts"] == store.health()["counts"]


def test_transcript_table_export_carries_no_plaintext_copy(tmp_path):
    store = Store(tmp_path / "source")
    project = store.create_project("Project")
    content = ("readable transcript line with plenty of repetition\n" * 200)
    store.ingest(
        batch_id="b1", project_id=project["id"], session={"id": "s1"}, agents=[],
        events=[], transcript_chunks=[{"chunk_id": "c1", "content": content}],
    )
    target = tmp_path / "backup"
    export_backup(store, target)

    table = (target / "tables" / "transcript_chunks.jsonl").read_bytes()
    assert b"readable transcript line" not in table, "压缩的 chunk 不能再附一份明文全文"
    assert len(table) < len(content.encode("utf-8")) // 10

    restored = Store(tmp_path / "restored")
    restore_backup(target, restored)
    hits = restored.search("readable transcript line", project_id=project["id"])
    assert [hit["scope"] for hit in hits] == ["transcript"]


@requires_git
def test_backup_tree_survives_a_clone_with_core_autocrlf_true(tmp_path):
    store, project = populated_store(tmp_path / "source")
    repo = _init_repo(tmp_path / "repo")
    target = repo / "research-trace-backup"
    export_backup(store, target)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "backup")

    clone = tmp_path / "clone"
    subprocess.run(
        [GIT, "-c", "core.autocrlf=true", "clone", "-q", str(repo), str(clone)], check=True
    )
    cloned = clone / "research-trace-backup"
    assert b"-text" in (cloned / ".gitattributes").read_bytes()
    assert (cloned / "tables" / "nodes.jsonl").read_bytes() == (
        target / "tables" / "nodes.jsonl"
    ).read_bytes(), "Windows 默认 autocrlf=true 不得改写备份字节"

    verify_backup(cloned)
    restored = Store(tmp_path / "restored")
    restore_backup(cloned, restored)
    assert restored.get_project(project["id"])["nodes"][0]["title"] == "Inspect counts"


@requires_git
def test_a_commit_left_unpushed_by_a_failed_push_is_retried_next_round(tmp_path):
    store, project = populated_store(tmp_path / "source")
    repo, bare = _repo_with_remote(tmp_path)
    assert sync_git_backup(store, repo)["pushed"] is True

    _git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    store.record_node(project["id"], idempotency_key="n3", title="After the outage")
    with pytest.raises(subprocess.CalledProcessError):
        sync_git_backup(store, repo)
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != _git(bare, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "remote", "set-url", "origin", str(bare))
    result = sync_git_backup(store, repo)  # 这一轮没有任何新数据
    assert result["changed"] is False
    assert result["pushed"] is True
    assert result["unpushed_commits"] == 1
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == _git(bare, "rev-parse", "HEAD").stdout.strip()

    quiet = sync_git_backup(store, repo)
    assert (quiet["changed"], quiet["pushed"]) == (False, False)


@requires_git
def test_emergency_purge_can_rewrite_the_backup_repository(tmp_path):
    secret = "sk-live-DO-NOT-KEEP-THIS"
    store = Store(tmp_path / "source")
    project = store.create_project("Project")
    store.record_node(project["id"], idempotency_key="n1", title="Leak", body=secret)
    repo, bare = _repo_with_remote(tmp_path)
    sync_git_backup(store, repo)
    assert secret.encode() in (repo / "research-trace-backup" / "tables" / "nodes.jsonl").read_bytes()

    store.purge(actor_id="admin", reason="token leaked into a node", project_ids=[project["id"]])
    with pytest.raises(ValidationError, match="confirm"):
        rewrite_backup_history(store, repo, reason="token leaked into a node")

    result = rewrite_backup_history(
        store, repo, confirm=True, reason="token leaked into a node"
    )
    assert result["rewritten"] is True
    assert result["purge_generation"] == 1
    assert _git(bare, "rev-list", "--count", "HEAD").stdout.strip() == "1", "远端只剩一个根 commit"
    assert secret.encode() not in (repo / "research-trace-backup" / "tables" / "nodes.jsonl").read_bytes()
    verify_backup(repo / "research-trace-backup")
