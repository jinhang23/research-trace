"""子章节（`chapter:`）的**写入侧**断言。

一个项目内部可以分主实验 / 消融实验：各有各的探索路径、各有各的定稿流程、
各有各的可溯源等级。落盘的只有一行，写在**开启那条线的那一步**上：

    chapter: 消融实验 | 逐个拿掉模块，对着主实验的 023 比

其余全是派生的——一个步骤没写 `chapter:` 就继承它 parent 的章节（判据在 core），
「这一章有谁」是扫出来现算的，磁盘上永远不存成员清单。

所以这个文件钉的是三类东西：

  * **一条线只写一次**——继承是这个设计的全部收益，批量展开会当场毁掉它；
  * **章节名的身份**——章节靠同名成立，任何「看着一样、比起来不一样」的差别
    都会把一章静悄悄劈成两半，而人在界面上看不出来；
  * **没人声明 chapter 的项目完全无感**——现存项目全是这个状态。
"""

from pathlib import Path

import pytest

import trace_core as core
import trace_write as W


@pytest.fixture()
def proj(tmp_path: Path):
    """一个真项目：001 主实验起点 → 002 接着做。"""
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    steps = W.resolve_project(tmp_path, "课题")
    W.create_step(steps, title="清洗数据")
    W.create_step(steps, parent="001", title="训练")
    return tmp_path, steps


def step_text(steps: Path, sid: str) -> str:
    return (steps / W.load(steps)[sid].dirname / core.NOTE_NAME).read_text(encoding="utf-8")


def chapter_lines(steps: Path, sid: str) -> list[str]:
    return [l for l in step_text(steps, sid).split("\n") if l.startswith("chapter:")]


# ------------------------------------------------------------ 落盘的那一行


def test_a_chapter_is_declared_on_the_step_that_opens_the_line(proj):
    """G4：删掉全部程序之后 `grep -rn "^chapter:" projects/` 仍然要能答出
    「消融实验是从哪一步开始的、它是什么」。所以它是 front-matter 里一行完整的
    人话，不是一份需要程序才解得开的结构。"""
    _root, steps = proj
    s, _ = W.create_step(steps, parent="002", title="拿掉注意力",
                         chapter="消融实验 | 逐个拿掉模块，对着主实验的 023 比")
    assert chapter_lines(steps, s.id) == ["chapter: 消融实验 | 逐个拿掉模块，对着主实验的 023 比"]


def test_a_chapter_can_be_declared_afterwards_because_you_realize_late(proj):
    """「这条线其实属于消融」是回头才想清楚的，所以 update_step 必须收得下它。
    改写不追加第二行——同一步两行 chapter 谁也说不清该信哪一句。"""
    _root, steps = proj
    W.update_step(steps, "002", {"chapter": "消融实验"})
    W.update_step(steps, "002", {"chapter": "消融实验 | 逐个拿掉模块"})
    assert chapter_lines(steps, "002") == ["chapter: 消融实验 | 逐个拿掉模块"]


def test_writing_an_empty_chapter_revokes_it_and_leaves_no_empty_line(proj):
    """标错了要能改回来（撤销 = 回到跟着 parent 继承）。撤销之后**不留一行空的**：
    一行没有名字的 chapter 读侧看不见，人却以为自己声明过了。"""
    _root, steps = proj
    W.update_step(steps, "002", {"chapter": "消融实验 | 说明"})
    W.update_step(steps, "002", {"chapter": ""})
    assert "chapter" not in step_text(steps, "002")


def test_a_chapter_note_without_a_name_is_refused_instead_of_being_swallowed(proj):
    """只写说明不写名字，最坏的处理是悄悄当成「撤销」——那句说明是人写的字。"""
    _root, steps = proj
    with pytest.raises(W.WriteError, match="没有取值"):
        W.update_step(steps, "002", {"chapter": "| 我以为这样就够了"})
    assert "chapter" not in step_text(steps, "002")


def test_a_chapter_needs_no_note_unlike_pipeline(proj):
    """和 `pipeline:` 的分歧就在这一条：pipeline 除了改变一份导出之外不留任何痕迹，
    所以理由必填；而一个章节是**看得见**的（整条子树归了它，导出里多出一节），
    名字本身已经说清它是什么。为一句可有可无的说明抬高门槛，收上来的只会是
    「消融实验 | 消融实验」这种仪式性文字。"""
    _root, steps = proj
    W.update_step(steps, "002", {"chapter": "消融实验"})
    assert chapter_lines(steps, "002") == ["chapter: 消融实验"]


def test_chapter_accepts_the_structured_form_too(proj):
    """网页 / MCP 把整份步骤 PATCH 回来时给的是 {name, note}，不是拼好的一行。"""
    _root, steps = proj
    W.update_step(steps, "002", {"chapter": {"name": "消融实验", "note": "对着 023 比"}})
    assert chapter_lines(steps, "002") == ["chapter: 消融实验 | 对着 023 比"]


# ------------------------------------------------------------ 名字就是身份


def test_trailing_whitespace_does_not_create_a_second_chapter(proj):
    """章节不靠登记成立（没有章节表，P1 不许有），它靠**同名**成立。
    `主实验 ` 和 `主实验` 在屏幕上一模一样，留着那个空格就是两个章节，
    而人永远看不出为什么消融被劈成了两半。粘贴带出来的尾随空格是最常见的一种。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "  主实验 "})
    W.update_step(steps, "002", {"chapter": "主实验"})
    assert chapter_lines(steps, "001") == chapter_lines(steps, "002") == ["chapter: 主实验"]


def test_inner_whitespace_runs_are_collapsed_for_the_same_reason(proj):
    """中间的双空格、tab、NBSP 同理：看不出差别的差别不该分出两个章节。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "Main  Experiment"})
    assert chapter_lines(steps, "001") == ["chapter: Main Experiment"]


def test_the_name_is_normalized_to_nfc_like_filenames_are(proj):
    """`é` 可以是一个码位，也可以是 e + 组合符——macOS 的文件名默认给后者，
    两种写法逐像素相同。fs_key 为附件名踩过同一个坑，章节名是同一类东西。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "Résumé"})       # 组合符
    W.update_step(steps, "002", {"chapter": "Résumé"})         # 预组合
    assert chapter_lines(steps, "001") == chapter_lines(steps, "002")


def test_case_differences_are_kept_because_the_name_is_display_text(proj):
    """**不**折叠大小写：中文没有大小写，而英文章节名里的大小写是人有意写的
    （`RNA 口袋` 折成 `rna 口袋` 就成了另一句话）。于是 `Ablation` 和 `ablation`
    是两个章节——那正是「一个章节里只有一个步骤」那条诊断要捞的笔误，
    交给读侧点名，比写入侧替人猜哪个才是本意好。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "Ablation"})
    W.update_step(steps, "002", {"chapter": "ablation"})
    assert chapter_lines(steps, "001") == ["chapter: Ablation"]
    assert chapter_lines(steps, "002") == ["chapter: ablation"]


def test_a_pipe_in_the_name_is_refused_because_it_would_become_the_description(proj):
    """字符串写法里第一根竖线就是分段符，所以只有结构化写法能把竖线塞进名字。
    放行的话，写进去的是名字、读回来的是「名字 + 说明」——一次静默的语义改写。"""
    _root, steps = proj
    with pytest.raises(W.WriteError, match="竖线"):
        W.update_step(steps, "001", {"chapter": {"name": "消融|实验"}})
    assert "chapter" not in step_text(steps, "001")


def test_an_invisible_control_character_in_the_name_is_refused(proj):
    """它没有任何视觉表现，却参与相等比较：两个看起来一模一样的名字变成两个章节。"""
    _root, steps = proj
    with pytest.raises(W.WriteError, match="控制字符"):
        W.update_step(steps, "001", {"chapter": "消融\x07实验"})


def test_an_over_long_name_is_refused_and_points_at_the_description_field(proj):
    """章节名是个**标签**——它要出现在分组标题、按章导出的产物名和节点卡片上。
    超长的多半是把说明整段写进了名字里，所以报错要直接说清那半句该挪到哪儿去，
    而不是只说一句「太长了」。"""
    _root, steps = proj
    with pytest.raises(W.WriteError, match="竖线右边"):
        W.update_step(steps, "001", {"chapter": "消" * (W.MAX_CHAPTER + 1)})
    assert "chapter" not in step_text(steps, "001")
    ok = "消" * W.MAX_CHAPTER
    W.update_step(steps, "001", {"chapter": ok})
    assert chapter_lines(steps, "001") == [f"chapter: {ok}"]


def test_a_slash_in_the_name_is_allowed_because_it_is_not_a_path_segment(proj):
    """`主实验/数据准备` 是**显示时按 `/` 分组**的写法（语义上章节仍然是一层）。
    所以名字不能按路径段来限制字符——反过来说，任何按章生成文件的导出都必须
    自己把名字过一遍文件名派生（`/`、`..`、`CON`、超长都可能出现在这里），
    绝不能拿它直接拼路径。见报告里的接缝清单。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "主实验/数据准备"})
    assert chapter_lines(steps, "001") == ["chapter: 主实验/数据准备"]


# ------------------------------------------------------------ 一条线只写一次


def test_marking_a_whole_line_takes_exactly_one_write(proj):
    """用户的动作是「这 20 步是消融」，而**沿树继承**已经把它变成了一次调用：
    只标那条线的头一步。所以这里没有批量入口——批量写会在每一步落一行 chapter:，
    那正好毁掉继承的全部好处（改一次章节名要改 20 个文件，移走一支还会带着一行
    过期的声明）。这条测试就是那个决定本身。"""
    _root, steps = proj
    head, _ = W.create_step(steps, parent="002", title="消融的头一步", chapter="消融实验")
    prev = head.id
    for i in range(19):
        s, _ = W.create_step(steps, parent=prev, title=f"拿掉第 {i} 个模块")
        prev = s.id
    declared = [sid for sid in W.load(steps) if "chapter:" in step_text(steps, sid)]
    assert declared == [head.id], "整条线上只该有一行 chapter:，其余靠继承"


def test_a_child_does_not_get_a_chapter_line_of_its_own(proj):
    """新建子步骤会继承父步骤的**路径**（那是要改的草稿），但绝不抄章节：
    抄下来的是一份会过期的拷贝——父节点改了章节名，20 个孩子还挂在旧名下。"""
    _root, steps = proj
    W.update_step(steps, "002", {"chapter": "消融实验"})
    s, _ = W.create_step(steps, parent="002", title="接着做")
    assert "chapter" not in step_text(steps, s.id)


def test_no_bulk_chapter_entry_point_exists(proj):
    """成组标候选（mark_alternatives）有批量入口是因为「一组」这件事只有整组一起
    看才成立；章节正相反——它的成员是**继承**出来的，批量写等于把派生展开成数据。
    这条钉的是「没有」：哪天有人加回来，先来读上面那一条。"""
    assert not any(n for n in dir(W) if "chapter" in n and n.startswith(("mark_", "set_")))


# ------------------------------------------------------------ 别的写入路径不许弄丢它


def test_the_chapter_line_survives_an_edit_that_never_mentions_it(proj):
    """render_note 是**全量重写** front-matter 的。漏掉一个键，就等于每次在网页上
    点一下 done 都把整条子树的归属悄悄删掉一次。"""
    _root, steps = proj
    W.update_step(steps, "002", {"chapter": "消融实验 | 对着 023 比"})
    W.update_step(steps, "002", {"status": "done", "body": "## 结论\n就这样"})
    assert chapter_lines(steps, "002") == ["chapter: 消融实验 | 对着 023 比"]


def test_the_chapter_line_survives_a_move(proj):
    """move_step 走的是另一条代码路径，同样全量重写 front-matter。
    移动改变的是**继承来的**归属，自己声明的那一行一个字都不该动。"""
    _root, steps = proj
    W.update_step(steps, "002", {"chapter": "消融实验"})
    W.move_step(steps, "002", None, reason="它其实不接着 001 想")
    assert chapter_lines(steps, "002") == ["chapter: 消融实验"]


def test_the_chapter_line_survives_a_path_check(proj):
    """record_path_check 只该动机器字段，人写的判断一个字都不能碰。"""
    _root, steps = proj
    W.update_step(steps, "001", {"paths": ["/blue/x | output | 权重"], "chapter": "主实验"})
    W.record_path_check(steps, "001", "/blue/x", exists=True, date="2026-08-09")
    text = step_text(steps, "001")
    assert "chapter: 主实验" in text and "checked=2026-08-09" in text


def test_a_hand_written_name_already_on_disk_is_not_rewritten_by_an_unrelated_edit(proj):
    """和悬空 parent、笔误的 branch / pipeline 是同一条原则：写入侧的校验只管
    这次**新传进来**的值。照校验过的值回写，等于在人还没来得及改之前先把那一行
    删掉——而删掉的是整条子树的归属，触发它只需要改一个不相干的标题。"""
    _root, steps = proj
    d = steps / W.load(steps)["001"].dirname / core.NOTE_NAME
    long_name = "消" * (W.MAX_CHAPTER + 5)
    d.write_text(d.read_text(encoding="utf-8").replace(
        "status:", f"chapter: {long_name}\nstatus:", 1), encoding="utf-8")
    W.update_step(steps, "001", {"title": "改个标题"})
    assert chapter_lines(steps, "001") == [f"chapter: {long_name}"]


# ------------------------------------------------------------ 没人分章的项目完全无感


def test_chapter_is_not_written_when_nobody_declares_it(proj):
    """现存项目全是这个状态。把「未分章」写成一个字段值就是把派生存成数据，
    而且每一条已有记录都会在下一次编辑时多出一行什么都没说的 diff。"""
    _root, steps = proj
    W.update_step(steps, "001", {"status": "done"})
    W.create_step(steps, parent="001", title="再来一步")
    assert "chapter" not in step_text(steps, "001")


def test_a_move_in_a_project_without_chapters_says_nothing_about_chapters(proj):
    """`None` 的意思是「这次移动和章节完全无关」。给一句「未分章 → 未分章」，
    等于让每个没分章的项目在每次移动之后都多读一条毫无信息的提示。"""
    _root, steps = proj
    assert W.move_step(steps, "002", None, reason="它其实是独立的一条线")["chapter"] is None


# ------------------------------------------------------------ 移动会换章：要说一声


def test_moving_a_line_into_another_chapter_reports_the_change(proj):
    """章节的变化和候选组的变化是同一类事，但更隐蔽：候选少了一个在树上看得见，
    而换章**磁盘上一个字节都没变**——整条子树集体转到消融，diff 里只有一行 moved:。
    用户吃过的亏正是「移动之后树形和创建顺序对不上」，所以这次移动的直接后果要
    当场说出来（只说，不拦、不改数据：把一条线挪进另一章正是移动最常见的用意）。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "主实验"})
    abl, _ = W.create_step(steps, parent="001", title="消融的头一步", chapter="消融实验")
    kid, _ = W.create_step(steps, parent="002", title="跟着主实验做的一步")

    out = W.move_step(steps, kid.id, abl.id, reason="它其实是在做消融")
    assert out["chapter"] == {"from": "主实验", "to": "消融实验",
                              "changed": True, "steps": [kid.id]}


def test_a_move_inside_one_chapter_reports_that_nothing_changed(proj):
    """同章内部的移动也要给出两头的章节名——「你移的这一步还在主实验里」
    和「它换章了」是两个不同的结论，读的人不该靠返回值里没有这一项去猜。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "主实验"})
    a, _ = W.create_step(steps, parent="001", title="a")
    b, _ = W.create_step(steps, parent="001", title="b")
    out = W.move_step(steps, b.id, a.id, reason="b 其实接着 a 想")
    assert out["chapter"] == {"from": "主实验", "to": "主实验", "changed": False, "steps": []}


def test_a_step_that_declares_its_own_chapter_does_not_change_chapter_when_moved(proj):
    """自己声明过的那一步不受继承影响，移到哪儿都还在自己那一章。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "主实验"})
    abl, _ = W.create_step(steps, parent="001", title="消融的头一步", chapter="消融实验")
    out = W.move_step(steps, abl.id, None, reason="消融是独立的一条根")
    assert out["chapter"] == {"from": "消融实验", "to": "消融实验",
                              "changed": False, "steps": []}


def test_the_whole_subtree_that_inherits_is_reported_not_just_the_moved_step(proj):
    """移动带走的是整棵子树，换章换的也是整棵子树——「你移的是一步」和
    「你把 9 步从主实验搬进了消融」是两个决定。子树里自己声明过章节的那些不算数。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "主实验"})
    abl, _ = W.create_step(steps, parent="001", title="消融的头一步", chapter="消融实验")
    head, _ = W.create_step(steps, parent="002", title="一条线的头")
    kid, _ = W.create_step(steps, parent=head.id, title="跟着继承的")
    own, _ = W.create_step(steps, parent=head.id, title="自己另开一章", chapter="预实验")

    out = W.move_step(steps, head.id, abl.id, reason="这条线其实在做消融")
    assert out["chapter"]["steps"] == sorted([head.id, kid.id], key=core.id_key)
    assert own.id not in out["chapter"]["steps"], "自己声明过章节的那一步不跟着换"


# ------------------------------------------------------------ 接缝


def test_the_writer_and_the_reader_agree_on_the_chapter_line(proj):
    """写侧拼出来的一行，读侧必须原样读得回来。读写各写一份解析，
    「写得进去、读不回来」只会在某个边角上悄悄发生，而磁盘上那一行看着完全正常。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "消融实验 | 逐个拿掉模块，对着主实验的 023 比"})
    raw = chapter_lines(steps, "001")[0].split(": ", 1)[1]
    got = W.parse_chapter(raw)
    assert got == {"name": "消融实验", "note": "逐个拿掉模块，对着主实验的 023 比"}
    assert W.format_chapter(got) == raw


def test_the_declared_chapter_reads_back_through_the_normal_scan(proj):
    """端到端：写入侧落的那一行，扫描出来就是 Step 上的那个名字。
    上一条比的是字符串，这一条比的是**整条读入路径**。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "主实验 | 论文图 2、图 3 那条线"})
    s = W.load(steps)["001"]
    assert (s.chapter, s.chapter_note) == ("主实验", "论文图 2、图 3 那条线")


def test_a_chapter_never_leaks_into_a_translation_file(proj):
    """`chapter:` 是结构键，而且是最能悄悄分家的一个：它沿树继承，译文里多写一份，
    中文页面和英文页面会把同一棵子树分进两个不同的章节，两边看着都像对的。
    翻译那条写入路径从函数形状上就写不出第二个键（_render_translation 只收一个）。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "主实验"})
    W.write_translation(steps, "001", "en", title="Clean the data", body="## Why\nx\n")
    tr = (steps / W.load(steps)["001"].dirname / "note.en.md").read_text(encoding="utf-8")
    assert "chapter" not in tr
    assert "chapter: 主实验" in step_text(steps, "001"), "写译文一个字节都不该碰原文"


def test_the_chapter_parser_is_cores_not_a_second_copy():
    """解析只有一份。写入侧再抄一遍，某一天分段规则改了两边就会不一致——
    表现是「写得进去、读侧当没写」，而磁盘上那一行看着完全正常。
    （core 还没长出这两个名字时本模块自带一份兜底，好让它自成一体；
    core 一旦有了，这条断言就要求写入侧用的必须是 core 那一份。）"""
    if hasattr(core, "parse_chapter"):
        assert W.parse_chapter is core.parse_chapter
        assert W.format_chapter is core.format_chapter


def test_a_pipe_inside_the_chapter_note_survives_the_round_trip(proj):
    """竖线右边整段都是人写的字（和 `result:` 的说明同一条）。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": {"name": "消融实验", "note": "对着 023 比 | 见图 4"}})
    assert W.parse_chapter(chapter_lines(steps, "001")[0].split(": ", 1)[1])["note"] \
        == "对着 023 比 | 见图 4"


def test_two_declarations_of_the_same_chapter_are_not_refused_by_the_writer(proj):
    """同一个章节名在两处各带了一句**不一样**的说明，是一条**诊断**（读侧点名），
    不是一次拒绝。两个人各写各的、或者一次笔误都可能造成它，而拒绝写入只会逼人
    把说明删掉了事——那样连诊断都没得报了。写入侧挡的是它自己看得清的错
    （竖线、控制字符、超长），跨步骤的一致性归读侧。"""
    _root, steps = proj
    W.update_step(steps, "001", {"chapter": "消融实验 | 逐个拿掉模块"})
    W.update_step(steps, "002", {"chapter": "消融实验 | 换个说法"})
    assert chapter_lines(steps, "002") == ["chapter: 消融实验 | 换个说法"]


def test_chapter_ids_are_not_renumbered_per_chapter(proj):
    """章节**不重编号**：id 是分配顺序，不是章节内序号。开一章不会让下一步从 001
    重新开始——`[[007]]` 和论文脚注要在整个项目里唯一，那是只追加的地基。"""
    _root, steps = proj
    s, _ = W.create_step(steps, parent="002", title="消融的头一步", chapter="消融实验")
    assert s.id == "003"
