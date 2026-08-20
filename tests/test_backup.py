from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess

import pytest

from research_trace import backup as backup_module
from research_trace.backup import (
    FORMAT_VERSION,
    _run_git,
    export_backup,
    restore_backup,
    rewrite_backup_history,
    sync_git_backup,
    verify_backup,
)
from research_trace.storage import SCHEMA_VERSION, Store, ValidationError

GIT = shutil.which("git")
requires_git = pytest.mark.skipif(GIT is None, reason="git is required for backup repo tests")

# 备份最坏的失败模式：文本里带这些字符时 export/verify 都说没问题，restore 却炸。
# str.splitlines() 会在它们处断行，而 verify 是按 b"\n" 数行的。
SEPARATORS = "line one line two paragraphnext"


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


def tree_bytes(root):
    """整棵备份树的字节。分卷之后没有一个固定的"那个 jsonl"路径了。"""
    return b"".join(path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file())


def one_file(root, name):
    matches = sorted(root.rglob(name))
    assert matches, f"{name} not found under {root}"
    return matches[0]


def project_nodes(store, project):
    """按排序契约取回节点。

    排序是 (occurred_at, id)，而 populated_store 的两个节点几乎总是落在同一毫秒里，
    id 又是随机的 —— 所以「第 0 个就是先建的那个」不成立，每次跑都可能换个顺序。
    要断言的是「恢复出来的库和源库一致」，不是硬编码某个创建顺序。
    """
    return store.get_project(project["id"])["nodes"]


def node_by_title(store, project, title):
    return next(node for node in project_nodes(store, project) if node["title"] == title)


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


def export_previous_format_tree(store, target):
    """按 FORMAT_VERSION 2 的布局写一份备份：一棵全量树 + 根 manifest.json。

    这正是升级之前的 export_backup 写出来的形状。它存在的唯一目的，是让"几年前的
    备份今天还能 verify / restore"变成一条会失败的测试，而不是一句承诺。
    """
    root = target
    root.mkdir(parents=True, exist_ok=True)
    expected, counts = set(), {}
    with backup_module._reader(store) as db:
        for table in backup_module.TABLES:
            order = backup_module._primary_order(db, table)
            payload = b""
            rows = 0
            for original in db.execute(f"SELECT * FROM {table} ORDER BY {order}"):
                row = dict(original)
                if table == "transcript_chunks":
                    raw = row.pop("compressed_content")
                    row.pop("search_text", None)
                    rel = f"transcripts/{row['chunk_id']}.zlib"
                    (root / rel).parent.mkdir(parents=True, exist_ok=True)
                    (root / rel).write_bytes(bytes(raw))
                    expected.add(rel)
                    row["compressed_file"] = rel
                if table == "attachments" and row.get("object_path"):
                    rel = "objects/" + str(row["object_path"]).replace(chr(92), "/")
                    (root / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(store.objects_dir / str(row["object_path"]), root / rel)
                    expected.add(rel)
                payload += (backup_module._json(row) + "\n").encode("utf-8")
                rows += 1
            rel = f"tables/{table}.jsonl"
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(payload)
            expected.add(rel)
            counts[table] = rows
    (root / ".gitattributes").write_bytes(
        b"# legacy\n* -text -ident -filter\ntranscripts/*.zlib binary\nobjects/** binary\n"
    )
    expected.add(".gitattributes")
    files = {
        rel: {
            "sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest(),
            "size": (root / rel).stat().st_size,
        }
        for rel in sorted(expected)
    }
    manifest = {
        "format": "research-trace-backup",
        "format_version": 2,
        "schema_version": SCHEMA_VERSION,
        "purge_generation": store.purge_generation(),
        "tables": counts,
        "excluded_ephemeral_tables": ["web_sessions", "device_authorizations"],
        "files": files,
    }
    (root / "manifest.json").write_bytes((backup_module._json(manifest) + "\n").encode("utf-8"))
    return manifest


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
    assert ([(node["id"], node["title"], node["parent_id"]) for node in project_nodes(restored, project)]
            == [(node["id"], node["title"], node["parent_id"]) for node in project_nodes(source, project)])
    child = node_by_title(restored, project, "Check confounding")
    assert child["parent_id"] == node_by_title(restored, project, "Inspect counts")["id"]
    assert restored.search("raw history", project_id=project["id"])[0]["scope"] == "transcript"


def test_backup_verification_rejects_tampering_and_restore_rejects_nonempty_store(tmp_path):
    source, _project = populated_store(tmp_path / "source")
    target = tmp_path / "backup"
    export_backup(source, target)
    projects = one_file(target, "projects.0001.jsonl")
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

    table = one_file(target, "transcript_chunks.0001.jsonl").read_bytes()
    assert b"readable transcript line" not in table, "压缩的 chunk 不能再附一份明文全文"
    assert len(table) < len(content.encode("utf-8")) // 10

    restored = Store(tmp_path / "restored")
    restore_backup(target, restored)
    hits = restored.search("readable transcript line", project_id=project["id"])
    assert [hit["scope"] for hit in hits] == ["transcript"]


# --------------------------------------------------------------------------- 分卷


def _backdate(store, table, stamp):
    with store.transaction() as db:
        db.execute(f"UPDATE {table} SET created_at=?", (stamp,))


def test_export_splits_volumes_by_year_and_then_by_capacity(tmp_path):
    store, project = populated_store(tmp_path / "source")
    store.ingest(
        batch_id="b2", project_id=project["id"], session={"id": "s1"}, agents=[],
        events=[
            {"event_id": f"e{n}", "event_type": "PostToolUse",
             "payload": {"text": "x" * 400, "n": n}}
            for n in range(2, 40)
        ],
        transcript_chunks=[],
    )
    _backdate(store, "events", "2024-03-04T05:06:07Z")
    _backdate(store, "transcript_chunks", "2025-01-02T03:04:05Z")

    target = tmp_path / "backup"
    index = export_backup(store, target, part_bytes=4096)
    volumes = {entry["volume"]: entry for entry in index["volumes"]}
    assert index["format_version"] == FORMAT_VERSION
    assert {"base", "2024", "2025"} <= set(volumes), volumes.keys()
    assert volumes["2024"]["tables"]["events"] == 39
    assert volumes["2025"]["tables"]["transcript_chunks"] == 1
    # 年内再按容量切：一年的 events 撑不进一个 4 KiB 的分片
    parts = json.loads((target / volumes["2024"]["manifest"]).read_text("utf-8"))["table_files"]
    assert len(parts["events"]) > 1, parts
    assert all(
        (target / "volumes" / "2024" / rel).stat().st_size <= 4096 + 512 for rel in parts["events"]
    )
    verify_backup(target)

    restored = Store(tmp_path / "restored")
    restore_backup(target, restored)
    assert restored.health()["counts"] == store.health()["counts"]


def test_verify_checks_one_volume_and_notices_a_volume_that_vanished(tmp_path):
    store, _project = populated_store(tmp_path / "source")
    _backdate(store, "events", "2024-03-04T05:06:07Z")
    target = tmp_path / "backup"
    export_backup(store, target)

    assert verify_backup(target, volume="2024")["volume"] == "2024"
    shutil.rmtree(target / "volumes" / "2024")
    with pytest.raises(ValidationError, match="volumes do not match the index"):
        verify_backup(target)


def test_restore_merges_volumes_in_any_order(tmp_path):
    store, project = populated_store(tmp_path / "source")
    _backdate(store, "events", "2024-03-04T05:06:07Z")
    _backdate(store, "transcript_chunks", "2025-01-02T03:04:05Z")
    target = tmp_path / "backup"
    export_backup(store, target)

    # 卷之间不能有隐含的先后依赖：把索引里的顺序倒过来，恢复结果必须一模一样。
    index_path = target / "index.json"
    index = json.loads(index_path.read_text("utf-8"))
    index["volumes"] = list(reversed(index["volumes"]))
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8", newline="")

    restored = Store(tmp_path / "restored")
    result = restore_backup(target, restored)
    assert set(result["volumes"]) == {entry["volume"] for entry in index["volumes"]}
    assert restored.health()["counts"] == store.health()["counts"]
    assert ([(node["id"], node["parent_id"]) for node in project_nodes(restored, project)]
            == [(node["id"], node["parent_id"]) for node in project_nodes(store, project)])
    assert node_by_title(restored, project, "Check confounding")["parent_id"] is not None


def test_a_backup_written_in_the_previous_format_still_verifies_and_restores(tmp_path):
    """备份的全部意义是「几年后还能读回来」。旧格式的树必须原样可用。"""
    store, project = populated_store(tmp_path / "source")
    legacy = tmp_path / "legacy-backup"
    manifest = export_previous_format_tree(store, legacy)
    assert manifest["format_version"] == 2 != FORMAT_VERSION

    assert verify_backup(legacy)["format_version"] == 2
    restored = Store(tmp_path / "restored")
    result = restore_backup(legacy, restored)
    assert result["restored"] is True and result["volumes"] == []
    assert restored.health()["counts"] == store.health()["counts"]
    assert restored.search("raw history", project_id=project["id"])[0]["scope"] == "transcript"

    # 把旧树原地升级成分卷格式后，根上的旧 manifest 不能留下来自相矛盾。
    export_backup(store, legacy)
    assert not (legacy / "manifest.json").exists()
    assert not (legacy / "tables").exists()
    verify_backup(legacy)


# ------------------------------------------------------------------- 容量告警


def test_capacity_report_rides_the_backup_result_and_warns_before_the_limits(tmp_path, monkeypatch):
    store, _project = populated_store(tmp_path / "source")
    target = tmp_path / "backup"
    index = export_backup(store, target)
    assert index["capacity"]["level"] == "ok"
    assert index["capacity"]["limits"]["file_critical"] > index["capacity"]["limits"]["file_warn"]
    assert index["capacity"]["export_bytes"] > 0
    # 仓库尺寸随每次 push 变，写进 index 会让每一轮都产生一个"内容没变"的 commit
    assert "repository_bytes" not in index["capacity"]

    monkeypatch.setenv("TRACE_BACKUP_FILE_WARN_BYTES", "64")
    warned = export_backup(store, target)["capacity"]
    assert warned["level"] == "warn"
    assert warned["warnings"] and str(warned["largest_file_bytes"]) in warned["warnings"][0]


def test_capacity_counts_the_volume_manifests_it_writes(tmp_path, monkeypatch):
    """卷的 manifest 不在它自己的 files 表里（没法给自己算校验和），但它是导出树里
    真实存在的一个文件，而且每个文件一条记录——一个有几十万附件对象的卷，manifest
    本身就能超过 GitHub 100 MiB 的硬拒线。漏掉它等于在最该报警的那一格里报正常。"""
    store, _project = populated_store(tmp_path / "source")
    target = tmp_path / "backup"
    index = export_backup(store, target)
    on_disk = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
    # index.json 自己不计（它内含 export_bytes，自我引用），其余每个字节都要算上
    assert index["capacity"]["export_bytes"] == on_disk - (target / "index.json").stat().st_size

    # manifest 是卷里最大的文件时，largest_file 必须指向它而不是跳过它
    entry = next(item for item in index["volumes"] if item["volume"] == "base")
    assert entry["largest_file"] == entry["manifest"]
    monkeypatch.setenv("TRACE_BACKUP_FILE_WARN_BYTES", "16")
    assert export_backup(store, target)["capacity"]["level"] == "warn"
    store.close()


@requires_git
def test_sync_reports_repository_capacity_through_the_backup_result(tmp_path, monkeypatch):
    store, _project = populated_store(tmp_path / "source")
    repo, _bare = _repo_with_remote(tmp_path)
    monkeypatch.setenv("TRACE_BACKUP_REPO_WARN_BYTES", "1024")
    result = sync_git_backup(store, repo)
    capacity = result["capacity"]
    assert capacity["repository_bytes"] > 0
    assert capacity["level"] == "warn"
    assert any("repository" in line for line in capacity["warnings"])


# --------------------------------------------------------- 审计里报过的四条复核


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
    assert one_file(cloned, "nodes.0001.jsonl").read_bytes() == one_file(
        target, "nodes.0001.jsonl"
    ).read_bytes(), "Windows 默认 autocrlf=true 不得改写备份字节"

    verify_backup(cloned)
    restored = Store(tmp_path / "restored")
    restore_backup(cloned, restored)
    assert ([node["title"] for node in project_nodes(restored, project)]
            == [node["title"] for node in project_nodes(store, project)])


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
    assert result["retried_commits"] == 1
    # 补推成功之后健康卡片不能还写着"远端落后"，那和真的落后长得一模一样
    assert result["unpushed_commits"] == 0
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == _git(bare, "rev-parse", "HEAD").stdout.strip()

    quiet = sync_git_backup(store, repo)
    assert (quiet["changed"], quiet["pushed"], quiet["unpushed_commits"]) == (False, False, 0)


def test_one_missing_attachment_object_does_not_abort_the_whole_export(tmp_path):
    store, project = populated_store(tmp_path / "source")
    node_id = node_by_title(store, project, "Check confounding")["id"]
    kept = store.attach(
        project["id"], target_type="node", target_id=node_id, name="keep.txt",
        data_base64=base64.b64encode(b"still here").decode("ascii"), mime_type="text/plain",
    )
    lost_object = store._db.execute(
        "SELECT object_path FROM attachments WHERE name='pca.png'"
    ).fetchone()[0]
    (store.objects_dir / str(lost_object)).unlink()

    target = tmp_path / "backup"
    index = export_backup(store, target)
    # 一个已经丢了的字节不该让还在的几年历史一起进不了备份
    assert len(index["missing_objects"]) == 1
    assert index["tables"]["attachments"] == 2
    verify_backup(target)

    restored = Store(tmp_path / "restored")
    result = restore_backup(target, restored)
    assert len(result["missing_objects"]) == 1
    assert restored.health()["counts"]["attachments"] == 2
    path, _mime, _name = restored.attachment_content(kept["id"])
    assert path.read_bytes() == b"still here"


@requires_git
def test_a_gitignore_that_swallows_backup_files_fails_instead_of_reporting_success(tmp_path):
    store, _project = populated_store(tmp_path / "source")
    repo, _bare = _repo_with_remote(tmp_path)
    (repo / ".gitignore").write_bytes(b"*.jsonl\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")

    with pytest.raises(ValidationError, match="gitignore"):
        sync_git_backup(store, repo)


@requires_git
def test_a_token_in_the_remote_url_never_reaches_the_error_message(tmp_path):
    repo, _bare = _repo_with_remote(tmp_path)
    url = "https://x-access-token:ghp_SUPERSECRET123@github.com/team/backup.git"
    with pytest.raises(subprocess.CalledProcessError) as raised:
        _run_git(repo, "remote", "add", "origin", url)  # origin 已存在，必失败
    message = f"{type(raised.value).__name__}: {raised.value}"  # server 就是这么拼 backup.error 的
    assert "ghp_SUPERSECRET123" not in message
    assert "x-access-token" not in message
    assert "***@github.com" in message


@requires_git
def test_emergency_purge_can_rewrite_the_backup_repository(tmp_path):
    secret = "sk-live-DO-NOT-KEEP-THIS"
    store = Store(tmp_path / "source")
    project = store.create_project("Project")
    store.record_node(project["id"], idempotency_key="n1", title="Leak", body=secret)
    repo, bare = _repo_with_remote(tmp_path)
    sync_git_backup(store, repo)
    assert secret.encode() in tree_bytes(repo / "research-trace-backup")

    store.purge(actor_id="admin", reason="token leaked into a node", project_ids=[project["id"]])
    with pytest.raises(ValidationError, match="confirm"):
        rewrite_backup_history(store, repo, reason="token leaked into a node")

    result = rewrite_backup_history(
        store, repo, confirm=True, reason="token leaked into a node"
    )
    assert result["rewritten"] is True
    assert result["purge_generation"] == 1
    assert _git(bare, "rev-list", "--count", "HEAD").stdout.strip() == "1", "远端只剩一个根 commit"
    assert secret.encode() not in tree_bytes(repo / "research-trace-backup")
    verify_backup(repo / "research-trace-backup")
