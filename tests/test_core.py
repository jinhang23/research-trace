"""解析与校验的断言。重点是**残缺输入必须仍能出图**——十年后的日志一定是残缺的。"""

import json
import time
from pathlib import Path

import trace_core as core


def codes(ws):
    return sorted(w["code"] for w in ws)


# ------------------------------------------------------------ front-matter


def test_title_may_contain_colons():
    """刻意不用 YAML 的理由：`title: 试了 3:1 采样` 在 YAML 里是语法错误。"""
    meta, body, ws = core.parse_note("---\nid: 001\ntitle: 试了 3:1 采样，AUC 0.82\n---\n正文\n")
    assert meta["title"] == "试了 3:1 采样，AUC 0.82"
    assert body == "正文"
    assert ws == []


def test_bom_and_crlf_are_tolerated():
    meta, body, ws = core.parse_note("﻿---\r\nid: 001\r\ntitle: x\r\n---\r\n\r\nhello\r\n")
    assert meta["id"] == "001" and body == "hello"
    assert ws == []


def test_quoted_values_are_unwrapped():
    meta, _, _ = core.parse_note('---\nid: 001\ntitle: "带引号: 的标题"\n---\n')
    assert meta["title"] == "带引号: 的标题"


def test_missing_front_matter_keeps_the_text_as_body():
    meta, body, ws = core.parse_note("就是一段没有 front-matter 的正文")
    assert meta == {} and body.startswith("就是一段")
    assert codes(ws) == ["no_front_matter"]


def test_unclosed_front_matter_does_not_eat_the_note():
    _, body, ws = core.parse_note("---\nid: 001\ntitle: x\n\n正文在这里")
    assert "正文在这里" in body
    assert codes(ws) == ["unclosed_front_matter"]


def test_missing_status_defaults_to_wip():
    step, ws = core.build_step("001_x", {"id": "001", "title": "x"}, "")
    assert step.status == "wip" and ws == []


def test_unknown_status_falls_back_with_a_warning():
    step, ws = core.build_step("001_x", {"id": "001", "title": "x", "status": "success"}, "")
    assert step.status == "wip"
    assert codes(ws) == ["bad_status"]


def test_front_matter_id_wins_over_directory_name():
    step, ws = core.build_step("007_x", {"id": "009", "title": "x"}, "")
    assert step.id == "009"
    assert codes(ws) == ["id_mismatch"]


# ------------------------------------------------------------ validate


def S(sid, parent=None, body=""):
    return core.Step(id=sid, parent=parent, title=sid, body=body, dirname=f"{sid}_x")


def test_dangling_parent_degrades_to_root_and_still_renders():
    by_id, ws = core.validate([S("001"), S("005", "999")])
    assert by_id["005"].parent is None
    assert codes(ws) == ["dangling_parent"]
    order = core.compute_order(by_id, core.build_children(by_id))
    assert order == ["001", "005"], "构建必须继续，不能拒绝工作"


def test_cycle_is_reported_and_broken_so_the_build_continues():
    by_id, ws = core.validate([S("001", "003"), S("002", "001"), S("003", "002")])
    assert codes(ws) == ["cycle"]
    assert "001 → 002 → 003" in ws[0]["message"] or "→" in ws[0]["message"]
    order = core.compute_order(by_id, core.build_children(by_id))
    assert len(order) == 3, "环被断开后三个节点都要出现在图上"


def test_duplicate_id_is_kept_visible_rather_than_dropped():
    a, b = S("003"), S("003")
    b.dirname = "003_other"
    by_id, ws = core.validate([a, b])
    assert codes(ws) == ["duplicate_id"]
    assert len(by_id) == 2 and "003~dup2" in by_id


def test_backlinks_replace_the_need_for_multi_parent():
    by_id, _ = core.validate([S("001"), S("003"), S("007", "001", body="综合了 [[003]] 的结论")])
    back = core.compute_backlinks(by_id)
    assert back["003"] == ["007"]
    assert back["001"] == []


def test_image_without_a_caption_is_flagged():
    """图注是图对文本读者（人的记忆、以及 agent）唯一的信息来源。"""
    assert codes(core.lint_body(S("001", body="![](loss.png)"))) == ["figure_without_caption"]
    assert core.lint_body(S("001", body='![](loss.png "第 12 轮起过拟合")')) == []
    assert core.lint_body(S("001", body="![loss 曲线](loss.png)")) == []


def test_lint_ignores_text_without_images():
    assert core.lint_body(S("001", body="## 结论\n没有图，只有字。")) == []


# --------------------------------------------- 内容层缺陷（G4：删掉程序还得读得懂）


FULL_BODY = "## 为什么\n试试新采样\n\n## 做了什么\n跑了 train.py\n\n## 结论\n不成立\n"


def _step(status, body="", dirname="001_x"):
    return core.Step(id="001", status=status, title="t", body=body, dirname=dirname)


def test_dead_without_a_conclusion_is_warned_not_silently_accepted():
    """G4 的执法点：只写 `status: dead` 不写为什么放弃，半年后 grep 什么都答不出来。

    以前 traceability() 早就把「没写结论」算进 missing 了，但那条路只有 agent 逐步读
    MCP 详情时才走到；check 和网页读的是 warnings，于是网页上点一下 dead 按钮全流程零提示。
    """
    ws = core.lint_body(_step("dead"))
    assert set(codes(ws)) == {"missing_why", "missing_what", "missing_conclusion"}
    assert all(w["level"] == "warn" for w in ws), (
        "内容质量问题不是结构错误——树照样能建，级别必须是 warn，error 留给"
        "「构建被迫改动数据」（重复 id、环）"
    )
    assert all(w["where"] == "001_x" for w in ws), "要能指到是哪一步"
    assert any("dead" in w["message"] and "结论" in w["message"] for w in ws), "提示要说清缺什么"


def test_done_without_why_or_what_is_warned():
    assert codes(core.lint_body(_step("done"))) == ["missing_conclusion", "missing_what", "missing_why"]
    assert core.lint_body(_step("done", FULL_BODY)) == []
    assert core.lint_body(_step("dead", FULL_BODY)) == []


def test_a_wip_step_is_not_nagged_about_unfinished_sections():
    """刻意的取舍：wip 是"还在写"。

    对着刚建出来的空模板报三条警告，只会把 check 的警告数变成常年噪音，
    人从此不看警告——那反而让真正的缺口（dead 没写结论）更难被发现。
    收尾状态（done/dead）才是记录的最终形态，此刻定 G4 的生死。
    """
    assert core.lint_body(_step("wip")) == []
    assert core.lint_body(_step("wip", "## 为什么\n")) == []


def test_content_warnings_reach_the_forest_so_check_and_the_web_can_see_them(tmp_path: Path):
    d = tmp_path / "steps" / "001_放弃了对比学习"
    d.mkdir(parents=True)
    (d / "note.md").write_text("---\nid: 001\nstatus: dead\ntitle: 放弃了对比学习\n---\n",
                               encoding="utf-8")
    f = core.compile_forest(tmp_path / "steps")
    assert "missing_conclusion" in codes(f["warnings"]), "check 和网页顶栏读的都是 warnings"


# --------------------------------------------- 编码


def test_a_note_that_is_not_utf8_is_reported_loudly(tmp_path: Path):
    """GBK / UTF-16 存的中文以前是静默变 �：页面乱码、warnings 一条没有，
    而下一次保存就把 � 落盘，原始字节永久丢失。必须吵出来。"""
    d = tmp_path / "steps" / "001_中文"
    d.mkdir(parents=True)
    (d / "note.md").write_bytes("---\nid: 001\ntitle: 中文标题\n---\n为什么这么做\n".encode("gbk"))
    f = core.compile_forest(tmp_path / "steps")
    bad = [w for w in f["warnings"] if w["code"] == "not_utf8"]
    assert len(bad) == 1, "非 UTF-8 必须产生警告，不能静默替换"
    assert bad[0]["level"] == "error", "级别要够高：这是会丢原文的事故，不是风格问题"
    assert bad[0]["where"] == "001_中文"
    assert "UTF-8" in bad[0]["message"]
    assert [s["id"] for s in f["steps"]] == ["001"], "报警归报警，构建不许中断"


def test_utf16_note_is_reported_too(tmp_path: Path):
    """PowerShell 的 `>` / Out-File 默认就是 UTF-16LE，比 GBK 更常见也更致命
    （front-matter 整个失效）。"""
    d = tmp_path / "steps" / "001_x"
    d.mkdir(parents=True)
    (d / "note.md").write_bytes("---\nid: 001\ntitle: x\n---\n正文\n".encode("utf-16-le"))
    f = core.compile_forest(tmp_path / "steps")
    assert "not_utf8" in codes(f["warnings"])


def test_clean_utf8_stays_silent(tmp_path: Path):
    d = tmp_path / "steps" / "001_x"
    d.mkdir(parents=True)
    (d / "note.md").write_text("---\nid: 001\ntitle: 中文\n---\n正文\n", encoding="utf-8")
    assert core.compile_forest(tmp_path / "steps")["warnings"] == []


def test_lineage_walks_to_the_root():
    by_id, _ = core.validate([S("001"), S("002", "001"), S("003", "002")])
    assert core.lineage(by_id, "003") == ["001", "002", "003"]


# ------------------------------------------------------------ compile


def test_compile_is_deterministic(tmp_path: Path):
    d = tmp_path / "steps"
    (d / "001_a").mkdir(parents=True)
    (d / "001_a" / "note.md").write_text("---\nid: 001\ntitle: a\n---\n为什么\n", encoding="utf-8")
    (d / "002_b").mkdir(parents=True)
    (d / "002_b" / "note.md").write_text("---\nid: 002\nparent: 001\ntitle: b\n---\n", encoding="utf-8")
    assert core.compile_forest(d) == core.compile_forest(d)


def test_directory_without_a_note_is_skipped_silently(tmp_path: Path):
    d = tmp_path / "steps"
    (d / "001_a").mkdir(parents=True)
    (d / "001_a" / "note.md").write_text("---\nid: 001\ntitle: a\n---\n", encoding="utf-8")
    (d / "scratch").mkdir()
    (d / "scratch" / "tmp.txt").write_text("x", encoding="utf-8")
    f = core.compile_forest(d)
    assert [s["id"] for s in f["steps"]] == ["001"]
    assert f["warnings"] == []


def test_files_are_derived_and_exclude_the_note(tmp_path: Path):
    d = tmp_path / "steps" / "001_a"
    d.mkdir(parents=True)
    (d / "note.md").write_text("---\nid: 001\ntitle: a\n---\n", encoding="utf-8")
    (d / "train.log").write_text("loss", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "note.md").write_text("nested", encoding="utf-8")
    f = core.compile_forest(tmp_path / "steps")
    assert [x["path"] for x in f["steps"][0]["files"]] == ["sub/note.md", "train.log"]


def test_duplicate_ids_keep_their_own_attachments(tmp_path: Path):
    """防的是：附件清单按 id 建键，两个同 id 目录互相覆盖，整组附件被记到别人名下。

    以前 001 的清单里列的是 001_y 的 second.txt（点开 404），而它自己的 first.txt
    和 001~dup2 的附件在网页、静态导出、MCP 里全部消失。
    键必须是目录名：id 会被 validate 改（→ 001~dup2），目录名不会。
    重复 id 是规格书写明必须处理的边界情况（git merge 残留、从备份恢复一步）。
    """
    d = tmp_path / "steps"
    for name, attach in (("001_x", "first.txt"), ("001_y", "second.txt")):
        (d / name).mkdir(parents=True)
        (d / name / "note.md").write_text("---\nid: 001\ntitle: t\n---\n", encoding="utf-8")
        (d / name / attach).write_text("x", encoding="utf-8")
    f = core.compile_forest(d)

    got = {s["dirname"]: [x["path"] for x in s["files"]] for s in f["steps"]}
    assert got == {"001_x": ["first.txt"], "001_y": ["second.txt"]}, "各归各的"
    assert "duplicate_id" in codes(f["warnings"]), "重复 id 本身仍然要报"


def test_every_step_carries_the_digest_of_its_note_bytes(tmp_path: Path):
    """乐观并发控制的锚点：公式必须和 trace_write.digest_of 逐字一致
    （note.md 原始字节的 sha256 前 12 位），否则 expect 永远对不上，409 变成永久误报。"""
    import hashlib

    d = tmp_path / "steps" / "001_a"
    d.mkdir(parents=True)
    raw = "---\nid: 001\ntitle: 中文\n---\n正文\n".encode("utf-8")
    (d / "note.md").write_bytes(raw)
    f = core.compile_forest(tmp_path / "steps")
    assert f["steps"][0]["digest"] == hashlib.sha256(raw).hexdigest()[:12]


# ------------------------------------------------------------ signature


def test_signature_covers_the_project_note(tmp_path: Path):
    """洞察写在 project.md 里（它在 steps/ 的上一级）。指纹漏掉它 → version 不涨
    → SSE 不推 → 另一台机器上的洞察面板一直是旧的，只能等下一次步骤级改动捎带刷新。"""
    proj = tmp_path / "projects" / "p1"
    steps = proj / "steps"
    (steps / "001_a").mkdir(parents=True)
    (steps / "001_a" / "note.md").write_text("---\nid: 001\ntitle: a\n---\n", encoding="utf-8")
    (proj / "project.md").write_text("---\nname: P\n---\n\n## 有效\n", encoding="utf-8")

    before = core.signature(steps)
    (proj / "project.md").write_text(
        "---\nname: P\n---\n\n## 有效\n- 回译在这个数据集上一直没用\n", encoding="utf-8")
    assert core.signature(steps) != before, "改洞察必须让指纹变"


def test_signature_still_reacts_to_steps(tmp_path: Path):
    proj = tmp_path / "projects" / "p1"
    steps = proj / "steps"
    (steps / "001_a").mkdir(parents=True)
    (steps / "001_a" / "note.md").write_text("---\nid: 001\ntitle: a\n---\n", encoding="utf-8")
    before = core.signature(steps)
    (steps / "001_a" / "loss.png").write_bytes(b"x")
    assert core.signature(steps) != before


def test_signature_of_a_brand_new_project_without_steps_dir(tmp_path: Path):
    """新建项目后 steps/ 可能还不存在，但 project.md 已经在了——此时写洞察也要涨版本。"""
    proj = tmp_path / "projects" / "p1"
    proj.mkdir(parents=True)
    steps = proj / "steps"
    assert core.signature(steps) == "empty"
    (proj / "project.md").write_text("---\nname: P\n---\n", encoding="utf-8")
    sig = core.signature(steps)
    assert sig != "empty"
    (proj / "project.md").write_text("---\nname: P\n---\n\n## 坑\n- 别用 fp16\n", encoding="utf-8")
    assert core.signature(steps) != sig


# ------------------------------------------------------------ 可溯源性（批量 vs 单个）


def _mixed_forest():
    """一棵各级混杂的树：有写全的、有只有标题的、有 dead 没结论的。"""
    full = "## 为什么\n试试\n\n## 做了什么\n跑了\n\n## 结论\n成立\n"
    steps = [
        core.Step(id="001", status="done", body=full, commit="abc", dirname="001_x",
                  paths=[{"location": "/blue/a", "note": "", "kind": "hpc"}]),
        core.Step(id="002", parent="001", status="done", body=full, dirname="002_x"),
        core.Step(id="003", parent="002", status="dead", body="", dirname="003_x"),
        core.Step(id="004", parent="003", status="done", body=full, commit="d", dirname="004_x",
                  paths=[{"location": "/blue/b", "note": "", "kind": "hpc"}]),
        core.Step(id="005", parent="001", status="wip", body=full, dirname="005_x"),
        core.Step(id="006", status="done", body=full, dirname="006_x"),
    ]
    by_id, _ = core.validate(steps)
    children = core.build_children(by_id)
    return by_id, core.compute_order(by_id, children)


def test_batch_traces_match_the_one_at_a_time_definition():
    """compute_traces 是 chain_traceability 的批量版本，为了性能改成了自顶向下递推。
    它必须逐字段等于原来那个「取整条链上最弱一环，平局取更靠近根的」的定义。"""
    by_id, order = _mixed_forest()
    fast = core.compute_traces(by_id, order)
    slow = {sid: core.chain_traceability(by_id, sid) for sid in order}
    assert fast == slow


def test_chain_level_is_the_weakest_ancestor_and_ties_go_to_the_root_side():
    by_id, order = _mixed_forest()
    t = core.compute_traces(by_id, order)
    assert t["004"]["self"] == "L2"
    assert t["004"]["chain"] == "L0", "003 是 dead 且什么都没写，整条链追不到底"
    assert t["004"]["weakest"] == "003"
    assert [e["id"] for e in t["004"]["lineage"]] == ["001", "002", "003", "004"]
    assert t["006"]["weakest"] == "006", "自己就是根时最弱的只能是自己"


# ------------------------------------------------------------ 性能


def _chain_project(root: Path, n: int) -> Path:
    """n 步的深链，每步正文接近真实长度。深链是"科研溯源"的目标形态，
    也正是 O(n²) 会爆的形状（宽树的祖先链只有 2 层，放大不出来）。"""
    body = ("## 为什么\n" + "为了验证采样比例。" * 12 + "\n\n## 做了什么\n"
            + "跑了 train.py。" * 12 + "\n\n## 结果\nAUC 0.82\n\n## 结论\n成立\n")
    d = root / "steps"
    d.mkdir(parents=True)
    for i in range(1, n + 1):
        sd = d / f"{i:03d}_x"
        sd.mkdir()
        (sd / "note.md").write_text(
            f"---\nid: {i:03d}\nparent: {'' if i == 1 else f'{i-1:03d}'}\n"
            f"status: done\ntitle: t{i:03d}\ncommit: abc\n---\n{body}",
            encoding="utf-8", newline="\n")
    return d


def test_compile_forest_of_1000_steps_stays_under_two_seconds(tmp_path: Path):
    """性能回归闸门。

    修之前：compile_forest 对每个节点都调 chain_traceability，而后者把整条祖先链的
    traceability 重算一遍 —— 1000 步深链上是 500500 次正文解析，实测 17 秒，
    期间 FastAPI 的事件循环整个卡住（写一步就重算一次）。
    修之后同一台机器 0.8 秒，且 cProfile 里的大头变成了读 1000 个文件的磁盘 I/O（线性）。

    阈值 2 秒的来历：优化后实测 0.8 秒，给慢盘和杀软扫描留 2 倍余量；而一旦 n²
    回归，耗时会立刻回到十几秒的量级，2 秒既拦得住又不会偶发红。
    """
    d = _chain_project(tmp_path, 1000)
    core.compile_forest(d)                      # 预热：只想量算法，不想量冷文件系统
    t0 = time.perf_counter()
    f = core.compile_forest(d)
    elapsed = time.perf_counter() - t0
    assert len(f["steps"]) == 1000
    assert elapsed < 2.0, f"1000 步编译用了 {elapsed:.2f}s —— 二次复杂度回来了"


def test_compile_output_is_byte_identical_across_runs(tmp_path: Path):
    """P3：视图是文件系统的纯函数，随时删掉重建必须逐字节一样。
    优化只许改快慢，不许改产物——批量算 trace 时各条 lineage 共享条目对象，
    这条断言同时钉住"共享没有把任何一处内容改掉"。"""
    d = _chain_project(tmp_path, 30)
    a = json.dumps(core.compile_forest(d), ensure_ascii=False, sort_keys=False)
    b = json.dumps(core.compile_forest(d), ensure_ascii=False, sort_keys=False)
    assert a == b
