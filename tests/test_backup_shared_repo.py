"""备份仓库不一定只有我们一个写入者。

把 --backup-repo 指到项目自己的代码仓（记录和代码同仓）是文档支持的用法。
那样别的机器随时会往同一个分支推东西。原来这里只 commit 不 fetch，
所以第一次被推到前面之后 push 就永远是 non-fast-forward —— 而且是每轮都失败，
因为没有任何环节会去拉。
"""
import shutil
import subprocess

import pytest

from research_trace.backup import sync_git_backup
from research_trace.storage import Store, ValidationError

from tests.test_backup import _git, _init_repo, _repo_with_remote, populated_store

GIT = shutil.which("git")
requires_git = pytest.mark.skipif(GIT is None, reason="git is required")


def other_writer_pushes(tmp_path, bare, filename="README.md", body="code\n"):
    """模拟另一台机器往同一个分支推代码。"""
    work = tmp_path / f"other-{filename}"
    subprocess.run([GIT, "clone", "-q", str(bare), str(work)], check=True)
    for key, value in (("user.email", "o@example.com"), ("user.name", "other"),
                       ("commit.gpgsign", "false")):
        _git(work, "config", key, value)
    (work / filename).write_text(body, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", f"code: {filename}")
    _git(work, "push", "-q", "origin", "HEAD:main")
    return _git(work, "rev-parse", "HEAD").stdout.strip()


@requires_git
def test_backup_survives_someone_else_pushing_code_to_the_same_branch(tmp_path):
    store, project = populated_store(tmp_path / "source")
    repo, bare = _repo_with_remote(tmp_path)

    first = sync_git_backup(store, repo)
    assert first["pushed"] is True

    # 另一台机器推了代码 —— 从此远端领先于我们
    their_commit = other_writer_pushes(tmp_path, bare)

    store.record_node(project["id"], idempotency_key="n3", title="新一步")
    second = sync_git_backup(store, repo)

    assert second["pushed"] is True, "被别人推到前面之后依然要能推上去"
    assert second["rebased_onto"] == 1

    log = _git(repo, "log", "--format=%H", "origin/main").stdout.split()
    assert their_commit in log, "别人的提交不能被我们顶掉"
    assert (repo / "README.md").is_file(), "别人的文件要还在工作区里"


@requires_git
def test_repeated_rounds_keep_working_after_interleaved_code_pushes(tmp_path):
    """一次成功不算数：交替推很多轮都不能退化。"""
    store, project = populated_store(tmp_path / "source")
    repo, bare = _repo_with_remote(tmp_path)
    sync_git_backup(store, repo)

    for round_number in range(3):
        other_writer_pushes(tmp_path, bare, f"file{round_number}.py", f"# {round_number}\n")
        store.record_node(project["id"], idempotency_key=f"r{round_number}", title=f"第 {round_number} 轮")
        report = sync_git_backup(store, repo)
        assert report["pushed"] is True, f"第 {round_number} 轮推不上去"
        assert report["unpushed_commits"] == 0


@requires_git
def test_an_unreachable_remote_does_not_lose_the_local_commit(tmp_path):
    """远端不可达时内容已经落盘，下一轮再推 —— 但不能把本地 commit 弄丢。"""
    store, _project = populated_store(tmp_path / "source")
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", str(tmp_path / "nope.git"))

    with pytest.raises(Exception):
        sync_git_backup(store, repo)          # push 会失败
    assert _git(repo, "log", "--oneline").stdout.strip(), "本地 commit 必须还在"


@requires_git
def test_a_real_conflict_aborts_instead_of_mangling_someone_elses_work(tmp_path):
    """别人改了我们下一轮要重写的那个文件：宁可这一轮不备份，也不能把他的提交搅乱。"""
    store, _project = populated_store(tmp_path / "source")
    repo, bare = _repo_with_remote(tmp_path)
    sync_git_backup(store, repo)

    work = tmp_path / "other"
    subprocess.run([GIT, "clone", "-q", str(bare), str(work)], check=True)
    for key, value in (("user.email", "o@example.com"), ("user.name", "other"),
                       ("commit.gpgsign", "false")):
        _git(work, "config", key, value)
    # 必须挑一个我们下一轮**一定会重写**的文件，否则 rebase 干净应用，造不出冲突。
    victim = sorted((work / "research-trace-backup").rglob("nodes.*.jsonl"))[0]
    victim.write_text("别人手改了这个文件\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "手改备份")
    _git(work, "push", "-q", "origin", "HEAD:main")

    store.record_node(_project["id"], idempotency_key="conflict", title="再记一步")
    with pytest.raises(ValidationError, match="diverged"):
        sync_git_backup(store, repo)
    assert _git(repo, "status", "--porcelain=v1").returncode == 0
    assert not (repo / ".git" / "rebase-merge").exists(), "rebase 必须被 abort 掉"
