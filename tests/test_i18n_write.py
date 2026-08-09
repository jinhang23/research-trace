"""补翻译这条写入路径的断言。

三件事是这一组测试真正在防的：

1. **双真相源**。上一代系统（ai-training-logbook）死在「父子关系存在两处」上。
   翻译文件里只要能出现一个结构键，同一个错误就被请回来了——所以这里逐个钉死：
   写进去的 front-matter 只可能有 title / name 一个键，正文里的换行注入不进去。
2. **语言码是路径**。它是调用方给的字符串，直接拼进文件名，所以它同时是
   safe_relpath 那一类的安全问题：分隔符、`..`、盘符、尾随点、大小写别名、
   Windows 设备名、`md` 这种会造出 note.md.md 的边角，一个都不能漏。
3. **译文不许挡住原文**。写译文碰不到 note.md 一个字节；删译文回退到原文，
   不丢任何事实。
"""

from pathlib import Path

import pytest

import trace_core as core
import trace_write as W


@pytest.fixture()
def proj(tmp_path: Path):
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    return tmp_path, core.steps_dir_of(tmp_path, "课题")


def note_of(steps_dir: Path, step) -> Path:
    return steps_dir / step.dirname / core.NOTE_NAME


def tr_of(steps_dir: Path, step, lang: str) -> Path:
    return steps_dir / step.dirname / f"note.{lang}.md"


# ------------------------------------------------------------ 语言码：合法的

@pytest.mark.parametrize("given,want", [
    ("en", "en"),
    ("ja", "ja"),
    ("zh-Hant", "zh-Hant"),
    ("EN", "en"),                 # 大小写归一化，不是拒绝：见 norm_lang 的说明
    ("eN", "en"),
    ("zh-hant", "zh-Hant"),       # 四字母的文字段按 BCP-47 惯例首字母大写
    ("ZH-HANT", "zh-Hant"),
    ("zh-tw", "zh-TW"),           # 两字母的地区段大写
    (" en ", "en"),               # 前后空白只是噪声：剥掉之后仍然只有一个规范名
    ("x" * 35, "x" * 35),
])
def test_a_language_code_is_normalised_to_exactly_one_canonical_form(given, want):
    """一种语言 ⇒ 恰好一个文件名。

    放任大小写自由的话，同一次调用在两个平台上结果不同：NTFS / APFS 上
    `note.EN.md` 就是 `note.en.md`（一次静默的别名覆盖，返回的 lang 和真被改写的
    文件对不上），Linux 上却分裂成 tr["EN"] 和 tr["en"] 两条记录。
    """
    assert W.norm_lang(given) == want


def test_every_accepted_language_code_round_trips_through_the_readers_pattern():
    """写侧造出来的文件名，读侧必须认得，而且认出来的语言码要和写侧说的一致。

    两边各写一个正则就迟早会分家，那时的表现是：文件明明在，界面说没有翻译。
    """
    for given in ("en", "EN", "ja", "zh-hant", "zh-TW", "x" * 35):
        lang = W.norm_lang(given)
        m = core.TR_RE.match(f"note.{lang}.md")
        assert m and m.group(1) == lang
        mp = core.PROJECT_TR_RE.match(f"project.{lang}.md")
        assert mp and mp.group(1) == lang


# ------------------------------------------------------------ 语言码：非法的

@pytest.mark.parametrize("bad", [
    "", "   ", None,                       # 空
    "e/n", "en/", "/en", "e\\n", "..\\en",  # 路径分隔符
    "..", ".", "../en", ".en",             # 上级目录 / 点开头
    "C:", "C:/en", "c:en",                 # 盘符与 NTFS 备用数据流的冒号
    "en.", "en..", "en. ", "en .",         # 尾随点：Windows 会剥掉，en. 和 en 是同一个文件
    "e n", "e\tn", "en\n",                 # 内部空白
    "en\x00", "en\x1f",                    # 控制字符与 NUL
    "1en", "-en", "_en",                   # 必须字母开头
    "en_US", "en.US", "en:US",             # 分隔符只允许连字符
    "ｅｎ", "中文", "en\u200b",              # 非 ASCII（含零宽字符）
    "en-", "zh--Hant", "-",                # 空子段
    "x" * 36,                              # 超长
])
def test_an_illegal_language_code_is_refused_before_anything_touches_the_disk(bad):
    """这个字符串直接进文件名，所以它同时是一道路径安全闸门。"""
    with pytest.raises(W.WriteError):
        W.norm_lang(bad)


@pytest.mark.parametrize("bad", ["md", "MD", "Md"])
def test_md_is_not_a_language(bad):
    """lang=md 会造出 note.md.md：看着像 note.md 的备份，实际会被 TR_RE 读成
    一种叫「md」的语言。只可能是把扩展名当成了语言码。"""
    with pytest.raises(W.WriteError, match="md"):
        W.norm_lang(bad)


@pytest.mark.parametrize("bad", ["con", "NUL", "aux", "com1", "LPT9", "prn"])
def test_windows_device_names_are_refused_as_language_codes(bad):
    """按现在的命名（note.<lang>.md）设备名其实碰不着——Windows 只看第一个点之前
    那一段。拒掉是因为语言码是调用方完全控制的字符串，命名方案一变它就是活的漏洞，
    而代价只是几个没人会用的 ISO 639-3 冷门码。"""
    with pytest.raises(W.WriteError, match="设备名"):
        W.norm_lang(bad)


def test_an_illegal_language_writes_nothing(proj):
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    before = sorted(p.name for p in (d / s.dirname).iterdir())
    for bad in ("../evil", "en.", "md"):
        with pytest.raises(W.WriteError):
            W.write_translation(d, s.id, bad, title="t", body="b")
    assert sorted(p.name for p in (d / s.dirname).iterdir()) == before


# ------------------------------------------------------------ 写译文

def test_a_translation_is_a_separate_file_and_the_note_is_untouched(proj):
    _root, d = proj
    s, _ = W.create_step(d, title="加入标题字段", body="## 为什么\n基线的 TF-IDF 丢掉词序。")
    before = note_of(d, s).read_bytes()

    info = W.write_translation(d, s.id, "en", title="Add title field",
                               body="## Why\nThe TF-IDF baseline discards word order.")

    assert info["lang"] == "en" and info["path"] == "note.en.md"
    assert note_of(d, s).read_bytes() == before, "补翻译永远碰不到原文"
    assert tr_of(d, s, "en").read_text(encoding="utf-8") == (
        "---\ntitle: Add title field\n---\n\n"
        "## Why\nThe TF-IDF baseline discards word order.\n")


def test_the_reader_picks_the_translation_up_as_derived_state(proj):
    """「还没翻译」是文件不存在，「翻译了」是文件存在——两边都不存状态。"""
    _root, d = proj
    s, _ = W.create_step(d, title="加入标题字段")
    assert core.compile_forest(d)["steps"][0]["tr"] == {}

    W.write_translation(d, s.id, "en", title="Add title field", body="## Why\nbecause.")
    tr = core.compile_forest(d)["steps"][0]["tr"]
    assert tr["en"]["title"] == "Add title field"
    assert "because." in tr["en"]["body"]


def test_a_translation_file_can_only_ever_carry_one_key(proj):
    """翻译文件里能出现结构键 = 双真相源被请回来。这里堵的是注入那条路：
    title 里塞一个换行，front-matter 就凭空多出一行 `id:`。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    W.write_translation(d, s.id, "en",
                        title="Add title field\nid: 999\nparent: 007\nstatus: dead",
                        body="## Why\nbecause.")
    text = tr_of(d, s, "en").read_text(encoding="utf-8")
    meta, _body, _w = core.parse_note(text)
    assert set(meta) <= set(core.TR_ONLY_KEYS), f"翻译文件里冒出了结构键: {sorted(meta)}"
    assert core.compile_forest(d)["steps"][0]["status"] == "wip", "note.md 永远赢"


def test_a_translation_body_that_looks_like_front_matter_stays_in_the_body(proj):
    """正文里写一段 `---\\nid: 999\\n---` 也不能变成这份文件的 front-matter。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    W.write_translation(d, s.id, "en", title="T", body="---\nid: 999\n---\n\n## Why\nx")
    meta, _body, _w = core.parse_note(tr_of(d, s, "en").read_text(encoding="utf-8"))
    assert set(meta) == {"title"} and meta["title"] == "T"


def test_an_empty_translation_is_refused_instead_of_creating_a_blank_page(proj):
    """空的 note.en.md 会让界面认为「已经有英文版了」，于是不再回退到原文——
    读者看到的是一片空白。撤掉某个语言版本是 drop_translation 的事。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    with pytest.raises(W.WriteError, match="drop_translation"):
        W.write_translation(d, s.id, "en", title="  ", body="\n\n")
    assert not tr_of(d, s, "en").exists()


def test_a_title_only_or_body_only_translation_is_fine(proj):
    """标题先翻、正文回头再补是常见节奏，不该被逼着一次写完。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    W.write_translation(d, s.id, "en", title="Only a title")
    W.write_translation(d, s.id, "ja", body="## なぜ\n理由。")
    tr = core.compile_forest(d)["steps"][0]["tr"]
    assert tr["en"]["title"] == "Only a title" and tr["en"]["body"] == ""
    assert tr["ja"]["title"] == "" and "理由。" in tr["ja"]["body"]


def test_writing_a_translation_in_the_notes_own_language_is_refused(proj):
    """note.md 声明 `lang: en` 之后再写一份 en 译文，两份英文正文会各说各话，
    而读侧判「小节写了没有」是 note.md 或任一译文里有它——同一个事实两处存储。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    note = note_of(d, s)
    note.write_text(note.read_text(encoding="utf-8").replace(
        "status:", "lang: en\nstatus:", 1), encoding="utf-8")

    with pytest.raises(W.WriteError, match="lang"):
        W.write_translation(d, s.id, "en", title="T", body="b")
    W.write_translation(d, s.id, "zh", title="标题", body="## 为什么\n因为。")  # 别的语言照旧


def test_a_hand_written_lang_survives_an_update(proj):
    """render_note 是全量重写 front-matter 的。漏掉 lang 就等于每次在网页上点一下
    done 都把用户手写的那一行悄悄删掉，而译文靠它判断「这个语言不用翻」。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    note = note_of(d, s)
    note.write_text(note.read_text(encoding="utf-8").replace(
        "status:", "lang: en\nstatus:", 1), encoding="utf-8")

    W.update_step(d, s.id, {"status": "done"})
    assert "lang: en" in note.read_text(encoding="utf-8")
    assert W.load(d)[s.id].lang == "en"


def test_a_missing_step_is_a_404_and_a_duplicate_id_label_is_a_conflict(proj):
    import shutil

    _root, d = proj
    with pytest.raises(W.NotFound):
        W.write_translation(d, "999", "en", title="T")

    s, _ = W.create_step(d, title="原始")
    dup = d / "001_从备份恢复的"
    dup.mkdir()
    shutil.copyfile(note_of(d, s), dup / core.NOTE_NAME)
    assert "001~dup2" in W.load(d), "前提：core 确实会贴这个临时标签"

    with pytest.raises(W.Conflict, match="临时标签"):
        W.write_translation(d, "001~dup2", "en", title="T")
    assert not (dup / "note.en.md").exists()


# ------------------------------------------------------------ 删译文

def test_dropping_a_translation_falls_back_to_the_original(proj):
    _root, d = proj
    s, _ = W.create_step(d, title="加入标题字段", body="## 为什么\n因为。")
    W.write_translation(d, s.id, "en", title="Add title field", body="## Why\nbecause.")
    before = note_of(d, s).read_bytes()

    out = W.drop_translation(d, s.id, "EN")          # 归一化之后指的是同一个文件
    assert out["removed"] is True and out["lang"] == "en"
    assert not tr_of(d, s, "en").exists()
    assert note_of(d, s).read_bytes() == before, "原文一个字节都不该动"
    assert core.compile_forest(d)["steps"][0]["tr"] == {}


def test_dropping_a_translation_that_never_existed_is_a_404(proj):
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    with pytest.raises(W.NotFound):
        W.drop_translation(d, s.id, "en")


# ------------------------------------------------------------ 项目译文

def test_a_project_translation_only_carries_name(proj):
    root, _d = proj
    W.write_project_translation(root, "课题", "en", name="My topic",
                                body="## Ideas\n- start small")
    text = (core.project_dir(root, "课题") / "project.en.md").read_text(encoding="utf-8")
    meta, body, _w = core.parse_note(text)
    assert set(meta) <= set(core.PROJECT_TR_ONLY_KEYS)
    assert meta["name"] == "My topic" and "start small" in body

    p = next(x for x in core.scan_projects(root) if x.slug == "课题")
    assert p.name == "课题", "显示名以 project.md 为准，译文改不动它"
    assert p.tr["en"]["name"] == "My topic"


def test_a_project_translation_cannot_wipe_its_own_deletion_log(proj):
    """中文版护得住、英文版护不住是说不过去的：目录一删，那一行是仅存的证据。
    英文版里的 `## Deleted` 是人手工翻过去的，同样不能被一份旧快照整段盖掉。"""
    root, d = proj
    W.write_project_translation(root, "课题", "en", name="My topic",
                                body="## Ideas\n- start small")
    en = core.project_dir(root, "课题") / "project.en.md"
    en.write_text(en.read_text(encoding="utf-8")
                  + "\n## Deleted\n- `009` a test step —— pasted a token by mistake\n",
                  encoding="utf-8")

    W.write_project_translation(root, "课题", "en", name="My topic",
                                body="## Ideas\n- try the big model\n\n"
                                     "## Deleted\n- nothing was ever deleted")

    text = en.read_text(encoding="utf-8")
    assert "pasted a token by mistake" in text, "磁盘上的删除记录必须活下来"
    assert "nothing was ever deleted" not in text, "提交文本里同名的小节一律丢弃"
    assert "try the big model" in text and "start small" not in text
    assert text.count("## Deleted") == 1


def test_a_language_without_a_closed_vocabulary_is_written_verbatim(proj):
    """INSIGHT_NAMES 只封了 zh/en。日文的小节名我们不认识，此时**不能**按 en 那套
    去合并——一条也认不出来的话，提交的正文会被整份丢掉，变成一次静默的空写。"""
    root, _d = proj
    W.write_project_translation(root, "课题", "ja", name="研究テーマ",
                                body="## アイデア\n- まず小さく試す")
    text = (core.project_dir(root, "课题") / "project.ja.md").read_text(encoding="utf-8")
    assert "まず小さく試す" in text


def test_a_project_translation_needs_the_project_to_exist(proj):
    root, _d = proj
    with pytest.raises(W.NotFound):
        W.write_project_translation(root, "不存在的", "en", name="x")


def test_the_deletion_log_follows_the_language_of_the_file_it_lives_in(proj):
    """一份 `lang: en` 的 project.md 里同时冒出 `## Deleted` 和 `## 已删除` 的话，
    G4 那句「grep 得到为什么删的」就得看运气搜对哪一个词。"""
    root, d = proj
    note = core.project_dir(root, "课题") / core.PROJECT_NOTE
    note.write_text("---\nname: 课题\nlang: en\n---\n\n## Works\n- dedup helps\n",
                    encoding="utf-8")
    s, _ = W.create_step(d, title="a test step")
    W.delete_step(d, s.id, "pasted a token by mistake")

    text = note.read_text(encoding="utf-8")
    assert "## Deleted" in text and "## 已删除" not in text
    assert "pasted a token by mistake" in text
    assert "lang: en" in text, "改洞察不能把这份笔记声明的语言吃掉"


def test_editing_insights_keeps_the_project_language_declaration(proj):
    root, _d = proj
    note = core.project_dir(root, "课题") / core.PROJECT_NOTE
    note.write_text("---\nname: 课题\nlang: en\n---\n\n", encoding="utf-8")
    W.update_project(root, "课题", add=("works", "dedup helps"))
    W.rename_project(root, "课题", "Renamed")
    assert "lang: en" in note.read_text(encoding="utf-8")


def test_the_english_insight_headings_are_the_ones_the_reader_uses(proj):
    """合并规则按语言查表。表要是和 core 的对不上，`## Works` 会被当成非洞察小节
    保留下来，于是「整体替换洞察」在英文版上变成「只增不减」。"""
    assert W.insight_headings("en") == frozenset(
        v["en"] for v in core.INSIGHT_NAMES.values())
    assert W.insight_headings("zh") == frozenset(W.INSIGHT_SECTIONS.values())
    assert W.insight_headings("ja") is None, "没有封闭词表的语言必须明确说不知道"


def test_the_zh_insight_names_have_a_single_source_of_truth():
    """web/app.js、MCP 内联标准、FORMAT.md 的对照表都钉着 INSIGHT_SECTIONS，
    而翻译要用 core 的 INSIGHT_NAMES。两边各抄一份，改名那天就会对不上。"""
    assert W.INSIGHT_SECTIONS == {k: v["zh"] for k, v in core.INSIGHT_NAMES.items()}
