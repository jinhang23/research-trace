"""双语支持的内核断言。

一句话概括这一整个文件在防什么：**双真相源不许回来**。上一代系统
（ai-training-logbook）就死在父子关系同时存在于两个地方——写了一处漏一处，
页面上永远有一半是错的。加了 note.en.md 之后，「结构写两份」这件事第一次
在物理上变得可能，所以下面每一条结构键都被单独钉了一遍。

其余的断言围绕另一条：**「还没翻译」是派生状态**（文件不存在），
以及**评级问的是「追不追得到」，不是「翻译全不全」**。
"""

import json
import time
from pathlib import Path

import pytest

import trace_core as core


def codes(ws):
    return sorted(w["code"] for w in ws)


def content(tr):
    """一份译文里**从文件读到的**部分。

    `digest` 是算出来的（sha256 of the raw bytes，给 expect 用），不是文件里的键，
    所以「结构键有没有渗进来」这类断言要把它排除掉——否则每加一个派生字段，
    一批本来该稳的测试就会集体变红，而它们防的根本不是这件事。
    """
    return {k: v for k, v in tr.items() if k != "digest"}


ZH = "---\nid: 001\nparent: \nstatus: done\ntitle: 加入标题字段\ncommit: c1d2e3f\n---\n" \
     "## 为什么\n基线的 TF-IDF 丢掉词序\n\n## 做了什么\n跑了 train.py\n\n## 结论\n成立\n"
EN = "---\ntitle: Add title field\n---\n" \
     "## Why\nThe TF-IDF baseline discards word order.\n\n## What\nRan train.py\n\n" \
     "## Conclusion\nConfirmed.\n"


def mkstep(root: Path, dirname="001_x", note=ZH, tr=None) -> Path:
    """造一步。tr 是 {语言码: 文本}，写成 note.<lang>.md。"""
    d = root / "steps" / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "note.md").write_text(note, encoding="utf-8", newline="\n")
    for lang, text in (tr or {}).items():
        (d / f"note.{lang}.md").write_text(text, encoding="utf-8", newline="\n")
    return root / "steps"


def only(steps_dir: Path) -> dict:
    return core.compile_forest(steps_dir)["steps"][0]


# ------------------------------------------------------------ 词表与文件名规则


def test_the_section_table_is_the_single_closed_vocabulary():
    """封闭词表是「翻译文件也能被解析和评级」的前提。
    改了这张表就是改了契约（web/i18n.js 和写入侧都照着它），所以钉一遍。"""
    assert core.SECTION_NAMES["why"] == {"zh": "为什么", "en": "Why"}
    assert core.SECTION_NAMES["conclusion"] == {"zh": "结论", "en": "Conclusion"}
    assert core.SECTIONS == ("为什么", "做了什么", "结果", "结论", "下一步"), \
        "中文骨架是 FORMAT.md 第 2 节逐字对着的那一份，不能因为加了英文就变"
    assert core.SECTION_KEY_BY_NAME["Why"] == "why"
    assert core.INSIGHT_NAMES["fails"] == {"zh": "无效", "en": "Doesn't work"}
    assert core.DELETED_NAME == {"zh": "已删除", "en": "Deleted"}
    assert core.TR_ONLY_KEYS == ("title",) and core.PROJECT_TR_ONLY_KEYS == ("name",)


@pytest.mark.parametrize("name,lang", [
    ("note.en.md", "en"),
    ("note.ja.md", "ja"),
    ("note.zh-Hant.md", "zh-Hant"),
])
def test_translation_filenames_are_recognised(name, lang):
    m = core.TR_RE.match(name)
    assert m and m.group(1) == lang, "语言码原样取自文件名，zh-Hant 的大小写有意义"


@pytest.mark.parametrize("name", ["note.md", "notes.en.md", "note.en.txt",
                                  "note..md", "note.en.zh.md", "loss.png"])
def test_note_md_itself_is_never_mistaken_for_a_translation(name):
    """note.md 若被 TR_RE 认成翻译，附件清单会把正文排掉、扫描会把它当译文——
    整份记录的正文原地消失。这条边界不许松。"""
    assert core.TR_RE.match(name) is None


# ------------------------------------------------------------ 扫描


def test_a_translation_is_attached_to_the_step(tmp_path: Path):
    s = only(mkstep(tmp_path, tr={"en": EN}))
    assert s["title"] == "加入标题字段", "note.md 永远是主语言的来源"
    assert s["tr"]["en"]["title"] == "Add title field"
    assert s["tr"]["en"]["body"].startswith("## Why")


def test_not_translated_is_a_derived_state_not_a_stored_one(tmp_path: Path):
    """「还没翻译」= 文件不存在。和 children / files 一样，绝不写进 note.md。"""
    steps = mkstep(tmp_path, tr={"en": EN})
    assert set(only(steps)["tr"]) == {"en"}
    (steps / "001_x" / "note.en.md").unlink()
    assert only(steps)["tr"] == {}, "删掉文件就是没翻译，note.md 里不该留下任何痕迹"
    meta, _, _ = core.parse_note((steps / "001_x" / "note.md").read_text(encoding="utf-8"))
    assert "tr" not in meta and "translations" not in meta, \
        "note.md 里没有任何一处登记「有哪些语言」——有的话就是第二份真相"


def test_a_translation_file_is_never_scanned_as_a_step(tmp_path: Path):
    """翻译文件在步骤目录**里面**，不是独立目录——它不该变出第二个步骤。"""
    steps = mkstep(tmp_path, tr={"en": EN})
    (steps / "note.en.md").write_text(EN, encoding="utf-8")   # 直接丢在 steps/ 下
    (steps / "002_only_translation").mkdir()
    (steps / "002_only_translation" / "note.en.md").write_text(EN, encoding="utf-8")
    f = core.compile_forest(steps)
    assert [s["id"] for s in f["steps"]] == ["001"], \
        "没有 note.md 的目录照旧静默跳过；孤零零的译文不构成一步"


def test_translations_do_not_show_up_as_attachments(tmp_path: Path):
    """漏排除的话 note.en.md 会出现在附件区，被当成可下载、可删的文件——
    点一下删除就等于悄悄删了英文版正文。"""
    steps = mkstep(tmp_path, tr={"en": EN})
    d = steps / "001_x"
    (d / "loss.png").write_bytes(b"x")
    (d / "sub").mkdir()
    (d / "sub" / "note.en.md").write_text("nested", encoding="utf-8")
    assert [x["path"] for x in only(steps)["files"]] == ["loss.png", "sub/note.en.md"], \
        "只排本层：嵌套目录里的同名文件是别人的附件"


def test_a_translation_that_is_not_utf8_is_reported_at_the_file(tmp_path: Path):
    steps = mkstep(tmp_path)
    (steps / "001_x" / "note.en.md").write_bytes("---\ntitle: x\n---\n中文\n".encode("gbk"))
    bad = [w for w in core.compile_forest(steps)["warnings"] if w["code"] == "not_utf8"]
    assert len(bad) == 1 and bad[0]["where"] == "001_x/note.en.md", \
        "警告要指到具体哪个文件，不然人不知道该转码哪一份"


# ------------------------------------------------- 结构键的防线（本文件的核心）


@pytest.mark.parametrize("key,value", [
    ("id", "999"),
    ("parent", "007"),
    ("status", "dead"),
    ("date", "2020-01-01"),
    ("commit", "deadbeef"),
    ("author", "agent:evil"),
    ("tags", "hijacked"),
    ("path", "/blue/wrong | 假的"),
    ("repro", "verified | 2026-01-01 | x | 假的"),
    ("key", "hijacked-key"),
])
def test_a_structural_key_in_a_translation_is_warned_and_ignored(tmp_path: Path, key, value):
    """每一个结构键单独钉一遍：**note.md 永远赢，翻译文件里的那份读都不读**。

    允许它生效就是把双真相源请回来：`parent` 写在两个文件里，改一处漏一处，
    树就有一半是错的，而两份值平时看着都对，只有改了其中一份才炸。
    """
    steps = mkstep(tmp_path, tr={
        "en": f"---\ntitle: Add title field\n{key}: {value}\n---\n## Why\nx\n"})
    f = core.compile_forest(steps)
    ws = [w for w in f["warnings"] if w["code"] == "translation_structural_key"]
    assert len(ws) == 1, f"`{key}:` 出现在翻译文件里必须报一条"
    assert ws[0]["level"] == "warn", "这不是结构错误（树照样能建），是「你写的这行没用」"
    assert ws[0]["where"] == "001_x/note.en.md"
    assert key in ws[0]["message"] and "note.md" in ws[0]["message"], "要说清哪个键、谁说了算"

    s = f["steps"][0]
    assert s["id"] == "001" and s["parent"] is None and s["status"] == "done"
    assert s["commit"] == "c1d2e3f" and s["author"] == "" and s["tags"] == []
    assert s["date"] == "" and s["key"] == "" and s["paths"] == [] and s["repro"] == []
    # digest 是这份译文自己的摘要（给 expect 用），从内容算出来，不是文件里的键，
    # 所以不参与「结构键有没有渗进来」这条断言。
    assert content(s["tr"]["en"]) == {"title": "Add title field", "body": "## Why\nx"}, \
        "翻译文件只留下 title 和正文，结构键一个都不许渗进来"


def test_a_structural_key_cannot_sneak_in_through_the_project_translation(tmp_path: Path):
    """项目译文同理：只准 name。"""
    d = tmp_path / "projects" / "p1"
    d.mkdir(parents=True)
    (d / "project.md").write_text("---\nname: 我的课题\n---\n\n## 有效\n- 去重有用\n",
                                  encoding="utf-8")
    (d / "project.en.md").write_text(
        "---\nname: My project\nid: 999\nstatus: dead\n---\n\n## Works\n- Dedup helps\n",
        encoding="utf-8")
    p = core.scan_projects(tmp_path)[0]
    assert p.name == "我的课题"
    assert content(p.tr["en"]) == {"name": "My project", "body": "## Works\n- Dedup helps"}
    data, ws = core.parse_translation((d / "project.en.md").read_text(encoding="utf-8"),
                                      core.PROJECT_TR_ONLY_KEYS, "p1/project.en.md",
                                      core.PROJECT_NOTE)
    assert codes(ws) == ["translation_structural_key"] * 2
    assert "project.md" in ws[0]["message"], "项目译文的提示要指向 project.md，不是 note.md"
    assert data == {"name": "My project", "body": "## Works\n- Dedup helps"}


def test_an_unknown_key_in_a_translation_is_just_dropped(tmp_path: Path):
    """非结构键（比如项目才有的 name 写进了步骤译文）不值得报警——
    它既不制造双真相源，也不影响任何派生结果，安静丢掉即可。"""
    steps = mkstep(tmp_path, tr={"en": "---\ntitle: T\nname: 乱写的\n---\n## Why\nx\n"})
    f = core.compile_forest(steps)
    assert f["warnings"] == []
    assert content(f["steps"][0]["tr"]["en"]) == {"title": "T", "body": "## Why\nx"}


# ------------------------------------------------------------ 语言声明


def test_lang_is_declared_never_guessed(tmp_path: Path):
    """没声明就如实说「原文」。字符集探测会把引用了中文论文标题的英文笔记判成中文，
    界面于是对读者撒谎，而读者没有任何办法发现——错的元信息比没有元信息坏。"""
    english_looking = ("---\nid: 001\ntitle: Add title field\n---\n"
                       "## Why\nThe TF-IDF baseline discards word order.\n")
    assert only(mkstep(tmp_path, note=english_looking))["lang"] == ""

    declared = "---\nid: 001\nlang: en\ntitle: Add title field\n---\n## Why\nx\n"
    assert only(mkstep(tmp_path, note=declared))["lang"] == "en"


def test_a_project_can_declare_its_default_language(tmp_path: Path):
    d = tmp_path / "projects" / "p1"
    d.mkdir(parents=True)
    (d / "project.md").write_text("---\nname: P\nlang: zh\n---\n", encoding="utf-8")
    assert core.scan_projects(tmp_path)[0].lang == "zh"
    (d / "project.md").write_text("---\nname: P\n---\n", encoding="utf-8")
    assert core.scan_projects(tmp_path)[0].lang == ""


# ------------------------------------------------------------ 评级与 lint


def test_english_headings_are_parsed_like_chinese_ones(tmp_path: Path):
    """英文写的 note.md（lang: en）必须一样能评级。硬编码中文标题会把它整篇
    判成「什么都没写」，L0——一条写全了的记录被报成不可溯源。"""
    note = ("---\nid: 001\nlang: en\nstatus: done\ntitle: t\ncommit: c\n"
            "path: /blue/x | data\n---\n" + EN.split("---\n", 2)[2])
    s = only(mkstep(tmp_path, note=note))
    assert s["trace"]["self"] == "L2"
    assert core.lint_body(core.Step(id="001", status="done", body=note, dirname="001_x")) == []


def test_a_section_written_in_any_one_language_counts_as_written(tmp_path: Path):
    """L0–L4 问的是「这个结果追不追得到」，不是「翻译全不全」。
    中文版写了结论、英文版还没翻，结论并没有丢。"""
    zh_no_conclusion = ("---\nid: 001\nstatus: done\ntitle: t\ncommit: c\n"
                        "path: /blue/x | data\n---\n## 为什么\nx\n\n## 做了什么\ny\n")
    steps = mkstep(tmp_path, note=zh_no_conclusion)
    f = core.compile_forest(steps)
    assert "missing_conclusion" in codes(f["warnings"]) and f["steps"][0]["trace"]["self"] == "L0"

    (steps / "001_x" / "note.en.md").write_text(
        "---\ntitle: T\n---\n## Conclusion\nConfirmed.\n", encoding="utf-8")
    f = core.compile_forest(steps)
    assert f["warnings"] == [], "补上英文版的结论之后，「没写结论」就是假警报"
    assert f["steps"][0]["trace"]["self"] == "L2"
    assert f["steps"][0]["trace"]["missing"] == []


def test_a_section_missing_in_every_language_is_still_reported(tmp_path: Path):
    """反过来必须仍然报——放宽成「任一语言」不等于放弃这条检查。"""
    zh = "---\nid: 001\nstatus: dead\ntitle: t\n---\n## 为什么\nx\n"
    steps = mkstep(tmp_path, note=zh, tr={"en": "---\ntitle: T\n---\n## Why\nx\n"})
    ws = codes(core.compile_forest(steps)["warnings"])
    assert ws == ["missing_conclusion", "missing_what"], \
        "两份都没写「做了什么」和「结论」，dead 却答不出为什么放弃 —— 正是 G4 要拦的"


def test_a_caption_is_judged_per_file_because_readers_read_one_file(tmp_path: Path):
    """和小节相反：图注逐个文件独立判。

    小节问的是「这个判断有没有被记下来」——记在中文版里它就存在，英文读者看不懂
    只是语言问题。图注问的是「这张图对**正在读这一份文件的读者**说了什么」——
    英文版只有一个光秃秃的 ![](loss.png)，读英文的人（和只被喂英文版的 agent）
    拿到的就是零信息。同一张图在两份文件里是两次独立的信息传递。
    """
    zh = ('---\nid: 001\nstatus: done\ntitle: t\ncommit: c\npath: /blue/x | d\n---\n'
          '## 为什么\nx\n\n## 做了什么\ny\n\n## 结果\n![](loss.png "第 12 轮起过拟合")\n\n'
          '## 结论\n成立\n')
    en_no_caption = "---\ntitle: T\n---\n## Result\n![](loss.png)\n"
    steps = mkstep(tmp_path, note=zh, tr={"en": en_no_caption})
    f = core.compile_forest(steps)
    figs = [w for w in f["warnings"] if w["code"] == "figure_without_caption"]
    assert len(figs) == 1 and figs[0]["where"] == "001_x/note.en.md"
    assert "note.en.md" in figs[0]["message"], "要说清是哪一份文件里的图缺图注"
    assert f["steps"][0]["trace"]["self"] == "L0", \
        "对读英文版的人来说那张图确实什么都没说，等级要如实反映"

    (steps / "001_x" / "note.en.md").write_text(
        '---\ntitle: T\n---\n## Result\n![](loss.png "Overfits after epoch 12")\n',
        encoding="utf-8")
    assert core.compile_forest(steps)["warnings"] == []


def test_a_caption_only_in_the_translation_still_leaves_the_chinese_reader_blind(tmp_path: Path):
    """对称的一半：中文版漏图注，中文读者一样是零信息。"""
    zh = "---\nid: 001\nstatus: wip\ntitle: t\n---\n## 结果\n![](loss.png)\n"
    steps = mkstep(tmp_path, note=zh,
                   tr={"en": '---\ntitle: T\n---\n## Result\n![](loss.png "Overfits")\n'})
    figs = [w for w in core.compile_forest(steps)["warnings"]
            if w["code"] == "figure_without_caption"]
    assert len(figs) == 1 and figs[0]["where"] == "001_x"


# ------------------------------------------------------------ 指纹 / 确定性 / 性能


def test_signature_covers_step_translations(tmp_path: Path):
    """补了翻译不涨 version → SSE 不推 → 网页切到英文还是老样子，
    人会以为工具没写进去，于是再写一遍。"""
    steps = mkstep(tmp_path / "projects" / "p1")
    before = core.signature(steps)
    (steps / "001_x" / "note.en.md").write_text(EN, encoding="utf-8")
    after = core.signature(steps)
    assert after != before
    (steps / "001_x" / "note.en.md").write_text(EN + "\n改了一个字\n", encoding="utf-8")
    assert core.signature(steps) != after, "改译文同样要涨"


def test_signature_covers_project_translations(tmp_path: Path):
    """project.<lang>.md 在 steps/ 的上一级，os.walk 扫不到它，必须单独带上。"""
    proj = tmp_path / "projects" / "p1"
    steps = mkstep(proj)
    (proj / "project.md").write_text("---\nname: P\n---\n", encoding="utf-8")
    before = core.signature(steps)
    (proj / "project.en.md").write_text("---\nname: My project\n---\n", encoding="utf-8")
    assert core.signature(steps) != before


def test_a_project_with_only_a_translation_is_not_empty(tmp_path: Path):
    proj = tmp_path / "projects" / "p1"
    proj.mkdir(parents=True)
    assert core.signature(proj / "steps") == "empty"
    (proj / "project.en.md").write_text("---\nname: My project\n---\n", encoding="utf-8")
    assert core.signature(proj / "steps") != "empty"


def test_compile_output_is_byte_identical_with_translations(tmp_path: Path):
    """P3：视图是文件系统的纯函数。翻译的扫描顺序由文件系统决定，
    不排序的话两台机器上的静态导出会不一样。"""
    steps = mkstep(tmp_path, tr={
        "en": EN,
        "ja": "---\ntitle: タイトル\n---\n## Why\nx\n",
        "zh-Hant": "---\ntitle: 加入標題欄位\n---\n## Why\nx\n",
    })
    a = json.dumps(core.compile_forest(steps), ensure_ascii=False)
    b = json.dumps(core.compile_forest(steps), ensure_ascii=False)
    assert a == b
    assert list(core.compile_forest(steps)["steps"][0]["tr"]) == ["en", "ja", "zh-Hant"], \
        "按语言码升序，和扫描顺序无关"


def _chain_with_translations(root: Path, n: int) -> Path:
    body = ("## 为什么\n" + "为了验证采样比例。" * 12 + "\n\n## 做了什么\n"
            + "跑了 train.py。" * 12 + "\n\n## 结果\nAUC 0.82\n\n## 结论\n成立\n")
    en = ("## Why\n" + "To check the sampling ratio. " * 12 + "\n\n## What\n"
          + "Ran train.py. " * 12 + "\n\n## Result\nAUC 0.82\n\n## Conclusion\nConfirmed\n")
    d = root / "steps"
    d.mkdir(parents=True)
    for i in range(1, n + 1):
        sd = d / f"{i:03d}_x"
        sd.mkdir()
        (sd / "note.md").write_text(
            f"---\nid: {i:03d}\nparent: {'' if i == 1 else f'{i-1:03d}'}\n"
            f"status: done\ntitle: t{i:03d}\ncommit: abc\n---\n{body}",
            encoding="utf-8", newline="\n")
        (sd / "note.en.md").write_text(f"---\ntitle: t{i:03d}\n---\n{en}",
                                       encoding="utf-8", newline="\n")
    return d


def test_scanning_translations_does_not_blow_up_the_compile_budget(tmp_path: Path):
    """双语把每一步的正文解析和磁盘读取都翻了一倍，代价必须仍然是**线性**的。

    阈值 3 秒的来历：单语 1000 步优化后实测 0.65 秒，双语实测约 1.1 秒
    （多一次 scandir + 多读 1000 个文件 + 多切一份小节表），给慢盘和杀软留两倍余量。
    真正要拦的是 n²（一旦回来就是十几秒），3 秒既拦得住又不会偶发红。
    """
    d = _chain_with_translations(tmp_path, 1000)
    core.compile_forest(d)                      # 预热：只想量算法，不量冷文件系统
    t0 = time.perf_counter()
    f = core.compile_forest(d)
    elapsed = time.perf_counter() - t0
    assert len(f["steps"]) == 1000 and f["steps"][0]["tr"]["en"]["title"] == "t001"
    assert elapsed < 3.0, f"1000 步双语编译用了 {elapsed:.2f}s"
