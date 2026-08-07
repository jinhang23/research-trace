"""多项目布局的断言。项目之间必须完全隔离，包括 id 命名空间。"""

from pathlib import Path

import pytest

import trace_core as core
import trace_write as W


def test_legacy_steps_dir_is_migrated_once(tmp_path: Path):
    """旧的单项目布局要一次性迁走，之后只剩一条代码路径（双路径正是上一代出 bug 的形状）。"""
    legacy = tmp_path / "steps" / "001_a"
    legacy.mkdir(parents=True)
    (legacy / "note.md").write_text("---\nid: 001\ntitle: a\n---\n", encoding="utf-8")

    assert core.ensure_layout(tmp_path) == "default"
    assert not (tmp_path / "steps").exists()
    moved = core.steps_dir_of(tmp_path, "default") / "001_a" / "note.md"
    assert moved.is_file()

    # 幂等：再跑一次什么也不做
    assert core.ensure_layout(tmp_path) is None
    assert moved.is_file()


def test_ensure_layout_on_empty_root_just_creates_projects(tmp_path: Path):
    assert core.ensure_layout(tmp_path) is None
    assert core.projects_root(tmp_path).is_dir()
    assert core.scan_projects(tmp_path) == []


def test_project_name_and_slug(tmp_path: Path):
    core.ensure_layout(tmp_path)
    p = W.create_project(tmp_path, "SMART Affinity v2")
    assert p.slug == "smart-affinity-v2"
    assert p.name == "SMART Affinity v2"
    assert core.scan_projects(tmp_path)[0].name == "SMART Affinity v2"


def test_duplicate_project_names_get_distinct_slugs(tmp_path: Path):
    core.ensure_layout(tmp_path)
    a = W.create_project(tmp_path, "分析")
    b = W.create_project(tmp_path, "分析")
    assert a.slug != b.slug and b.slug == a.slug + "-2"


def test_rename_keeps_the_slug(tmp_path: Path):
    """slug 就是 URL 的一部分，改了会让所有已发出的链接失效。"""
    core.ensure_layout(tmp_path)
    p = W.create_project(tmp_path, "旧名字")
    q = W.rename_project(tmp_path, p.slug, "新名字")
    assert q.slug == p.slug
    assert core.scan_projects(tmp_path)[0].name == "新名字"


def test_id_namespaces_are_per_project(tmp_path: Path):
    core.ensure_layout(tmp_path)
    a = W.create_project(tmp_path, "alpha")
    b = W.create_project(tmp_path, "beta")
    sa, sb = core.steps_dir_of(tmp_path, a.slug), core.steps_dir_of(tmp_path, b.slug)

    s1, _ = W.create_step(sa, title="alpha 第一步")
    s2, _ = W.create_step(sa, parent="001", title="alpha 第二步")
    s3, _ = W.create_step(sb, title="beta 第一步")
    assert (s1.id, s2.id) == ("001", "002")
    assert s3.id == "001", "每个项目的 id 各自从 001 开始，互不影响"

    assert sorted(W.load(sa)) == ["001", "002"]
    assert sorted(W.load(sb)) == ["001"]


def test_steps_cannot_reference_a_parent_in_another_project(tmp_path: Path):
    core.ensure_layout(tmp_path)
    a = W.create_project(tmp_path, "alpha")
    b = W.create_project(tmp_path, "beta")
    W.create_step(core.steps_dir_of(tmp_path, a.slug), title="alpha 第一步")
    with pytest.raises(W.NotFound):
        W.create_step(core.steps_dir_of(tmp_path, b.slug), parent="001", title="想跨项目挂父")


def test_missing_project_note_falls_back_to_the_directory_name(tmp_path: Path):
    core.ensure_layout(tmp_path)
    (core.projects_root(tmp_path) / "手工建的" / "steps").mkdir(parents=True)
    ps = core.scan_projects(tmp_path)
    assert [p.slug for p in ps] == ["手工建的"]
    assert ps[0].name == "手工建的", "没有 project.md 也要能出现——目录就是项目"


@pytest.mark.parametrize("bad", ["", "  ", "../escape", "a/b", "a\\b", ".hidden"])
def test_project_path_traversal_is_blocked(tmp_path: Path, bad):
    core.ensure_layout(tmp_path)
    with pytest.raises(W.WriteError):
        W.resolve_project(tmp_path, bad)


def test_resolve_project_rejects_unknown_project(tmp_path: Path):
    core.ensure_layout(tmp_path)
    with pytest.raises(W.NotFound):
        W.resolve_project(tmp_path, "nope")
