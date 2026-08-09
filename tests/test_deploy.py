"""部署形态的断言：代码仓公开、数据仓私有。

这是实际上线要用的形态，而绝大多数开发都发生在 data_dir="." 下，
所以它特别容易在不知不觉中被改坏——尤其是「同步到底打在哪个仓」这一条：
搞反了就等于把科研笔记推到公开的代码仓上，而且不会有任何报错。
"""

import json
from pathlib import Path

import pytest

import trace_cli as cli
import trace_core as core
import trace_server as srv


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch):
    """把 trace_cli 的"代码仓根"指到临时目录，避免动到真的 config.json。"""
    code = tmp_path / "code"
    code.mkdir()
    monkeypatch.setattr(cli, "ROOT", code)
    monkeypatch.setattr(cli, "CONFIG_PATH", code / "config.json")
    monkeypatch.setattr(srv, "ROOT", code)
    monkeypatch.setattr(srv, "CONFIG_PATH", code / "config.json")
    return code


def args_for_init(**kw):
    return type("A", (), {"title": "t", "project": "第一个课题", "data_dir": ".",
                          "force": False, "git": False, "no_git": True, **kw})()


def init(sandbox, **kw):
    assert cli.cmd_init(args_for_init(**kw)) == 0
    return json.loads((sandbox / "config.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------ init --data-dir


def test_init_creates_the_first_project_in_the_data_repo(sandbox, tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    cfg = init(sandbox, data_dir=str(data))
    assert cfg["data_dir"] == str(data)
    assert [p.name for p in (data / "projects").iterdir()] == ["第一个课题"]
    assert not (sandbox / "projects").exists(), "代码仓里不该出现 projects/"


def test_init_without_data_dir_keeps_everything_together(sandbox):
    init(sandbox)
    assert (sandbox / "projects" / "第一个课题").is_dir()


def test_init_generates_a_token_so_nobody_has_to_invent_one(sandbox):
    cfg = init(sandbox)
    assert len(cfg["token"]) >= 32 and len(cfg["space"]) >= 16
    assert cfg["token"] != init(sandbox, force=True)["token"], "每次都要是新的随机值"


def test_init_refuses_to_clobber_an_existing_config(sandbox):
    init(sandbox)
    assert cli.cmd_init(args_for_init(project="p")) == 1


# ------------------------------------------------------------ 同步打在哪个仓


def test_git_sync_targets_the_data_repo_not_the_code_repo(sandbox, tmp_path: Path, monkeypatch):
    """搞反了就是把科研笔记推到公开的代码仓上，而且悄无声息。"""
    data = tmp_path / "data"
    (data / "projects").mkdir(parents=True)
    (sandbox / "config.json").write_text(json.dumps({
        "space": "s", "token": "t", "data_dir": str(data),
        "git": {"enabled": True, "remote": "origin", "branch": "main", "debounce": 45},
    }), encoding="utf-8")
    (sandbox / "web").mkdir(exist_ok=True)

    seen = {}
    real = srv.GitSync

    def spy(root, **kw):
        seen["root"] = Path(root)
        return real(root, **kw)

    monkeypatch.setattr(srv, "GitSync", spy)
    srv.create_app(srv.load_config(sandbox / "config.json"))
    assert seen["root"] == data.resolve(), f"同步目标应当是数据仓，实际是 {seen['root']}"


def test_data_dir_is_resolved_relative_to_the_code_repo(sandbox, tmp_path: Path):
    """deploy 文档里写的是 ../trace-data 这种相对路径。"""
    sibling = sandbox.parent / "trace-data"
    sibling.mkdir()
    cfg = init(sandbox, data_dir="../trace-data")
    assert (sibling / "projects" / "第一个课题").is_dir()
    assert cli.data_root(cfg) == sibling.resolve()


# ------------------------------------------------------------ 隔离


def test_the_code_repo_stays_free_of_step_files(sandbox, tmp_path: Path):
    import trace_write as W

    data = tmp_path / "data"
    data.mkdir()
    init(sandbox, data_dir=str(data))
    W.create_step(core.steps_dir_of(data, "第一个课题"), title="一步")
    assert list(sandbox.rglob("note.md")) == []
    assert len(list(data.rglob("note.md"))) == 1


def test_config_with_a_secret_lives_in_the_code_repo_and_is_ignored(sandbox, tmp_path: Path):
    """令牌在 config.json 里，而 config.json 在 .gitignore 里 —— 数据仓里不该有它。"""
    data = tmp_path / "data"
    data.mkdir()
    init(sandbox, data_dir=str(data))
    assert (sandbox / "config.json").is_file()
    assert not (data / "config.json").exists()
    ignored = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert "config.json" in ignored


# ------------------------------------------------------------ 默认值本身必须是安全的
# 这一组守的是"照 README 的 30 秒上手跑一遍，会不会把未发表的科研笔记推进公开代码仓"。
# 危险的不是某一行代码，是**默认值**：真正的 push 发生在第一次建步骤 45 秒之后，
# 不在任何人看着的时候，而且成功时一个字都不打印。


def test_init_does_not_turn_on_git_sync_unless_asked(sandbox):
    """默认必须是关。开着的话，README 的 30 秒上手就是一条静默的泄露路径。"""
    cfg = init(sandbox, no_git=False)          # 两个开关都不给 = 用户没表态
    assert cfg["git"]["enabled"] is False


def test_init_refuses_to_enable_git_when_data_lives_in_the_code_repo(sandbox, tmp_path: Path):
    """`git add -A && git push` 打在代码仓上 = 把私有笔记推到公网。这是禁止，不是提醒。"""
    (sandbox / ".git").mkdir()
    assert cli.cmd_init(args_for_init(data_dir=".", git=True, no_git=False)) == 2
    assert not (sandbox / "config.json").exists(), "被拒绝时不该留下半份配置"


def test_init_refuses_git_even_for_a_subdirectory_of_the_code_repo(sandbox):
    """`--data-dir ./data` 看着像"分开了"，其实还在同一个 git 工作区里。"""
    (sandbox / ".git").mkdir()
    (sandbox / "data").mkdir()
    assert cli.cmd_init(args_for_init(data_dir="data", git=True, no_git=False)) == 2


def test_init_enables_git_only_for_a_separate_repo(sandbox, tmp_path: Path):
    """闸门不能误伤正常用法：数据仓是另一个 git 仓库时，--git 必须真的能开。"""
    (sandbox / ".git").mkdir()
    data = tmp_path / "data"
    (data / ".git").mkdir(parents=True)
    cfg = init(sandbox, data_dir=str(data), git=True, no_git=False)
    assert cfg["git"]["enabled"] is True


def test_the_default_data_dir_is_outside_the_code_repo(sandbox, tmp_path: Path):
    """默认值决定了绝大多数人的实际形态。默认落在代码仓里 = 默认违反"代码公开、数据私有"。"""
    assert cli.main(["init"]) == 0
    cfg = json.loads((sandbox / "config.json").read_text(encoding="utf-8"))
    assert cfg["data_dir"] != ".", "默认不能把数据仓放在代码仓里"
    assert cli.data_root(cfg) != sandbox
    assert not (sandbox / "projects").exists()
    assert list((cli.data_root(cfg) / "projects").iterdir()), "第一个项目要建在数据仓里"


def test_no_git_still_works_so_old_scripts_do_not_break(sandbox):
    """`--no-git` 现在是默认行为，但 deploy 文档和老脚本里还写着它，不能报错。"""
    assert cli.main(["init", "--data-dir", ".", "--no-git"]) == 0


# ------------------------------------------------------------ 数据仓路径填错


def test_data_root_speaks_up_when_the_target_is_not_a_data_repo(sandbox, tmp_path: Path, capsys):
    """填错一个字符 → 凭空造出一棵空树 → 全程静默，是这套系统最贵的一个失败。"""
    wrong = tmp_path / "写岔了"
    wrong.mkdir()
    (wrong / "论文.docx").write_text("x", encoding="utf-8")
    (tmp_path / "写对了" / "projects").mkdir(parents=True)

    cli.data_root({"data_dir": str(wrong)})
    out = capsys.readouterr().out
    assert "projects" in out and "填错" in out
    assert "写对了" in out, "同一层里有真数据仓时要点名，让人一眼看出指错了"


def test_data_root_stays_quiet_on_a_real_data_repo(sandbox, tmp_path: Path, capsys):
    """别搞得太聪明：正常的数据仓一个字都不该多说，否则警告很快就没人看。"""
    data = tmp_path / "data"
    (data / "projects").mkdir(parents=True)
    cli.data_root({"data_dir": str(data)})
    assert capsys.readouterr().out == ""


def test_a_path_whose_parent_is_missing_is_treated_as_a_typo(sandbox, tmp_path: Path):
    """"目录不存在会自动建" 只对下一级成立；连上级都没有，那是路径写错了，不是没初始化。"""
    with pytest.raises(Exception):
        cli.data_root({"data_dir": str(tmp_path / "没有这层" / "也没有这层" / "仓")})


# ------------------------------------------------------------ check 要真的执法
# FORMAT.md 第 10 节写死了「check 和网页会给出等级」。在这之前 check 只打印
# 步数/轨道/树尺寸/警告数，那句话的两个主语都不成立。


def check_args(**kw):
    return type("A", (), {"project": None, "strict": False, **kw})()


@pytest.fixture()
def graded(sandbox, tmp_path: Path, monkeypatch):
    """一棵有意造得等级参差的树：根是 L0，孙子自己写得全但被根拖住。"""
    import trace_write as W

    data = tmp_path / "data"
    data.mkdir()
    init(sandbox, data_dir=str(data))
    monkeypatch.setattr(cli, "load_config", lambda: {"data_dir": str(data)})
    sd = core.steps_dir_of(data, "第一个课题")
    W.create_step(sd, title="基线", status="done", body="## 为什么\n看基线\n## 做了什么\n跑\n")
    W.create_step(sd, parent="001", title="加标题字段", status="done", commit="abc",
                  paths=["/blue/x | 训练集"],
                  body="## 为什么\n假设标题有用\n## 做了什么\n跑\n## 结论\n有用\n")
    W.create_step(sd, parent="002", title="回译增强", status="dead",
                  body="## 为什么\n试试\n## 做了什么\n跑\n")
    return data


def test_check_prints_the_traceability_levels(graded, capsys):
    assert cli.cmd_check(check_args()) == 0
    out = capsys.readouterr().out
    assert "可溯源性" in out
    assert "L0" in out and "L2" in out, "自身等级的分布要看得见"
    assert "整链" in out, "链级才回答「这个结论追不追得到底」，不能只报自身"


def test_check_names_the_weakest_link_and_what_it_is_missing(graded, capsys):
    """FORMAT.md 明写「补记录要从最弱的那一环补起」——不点名就没法照做。"""
    cli.cmd_check(check_args())
    out = capsys.readouterr().out
    assert "最弱一环 001" in out, "被拖住的是根 001，不是最新那一步"
    assert "卡住 3 条链" in out
    assert "没记 commit" in out and "没记产物位置" in out, "missing 要列成可操作的清单"


def test_check_warns_about_a_dead_step_without_a_conclusion(graded, capsys):
    """G4 的执法点：标了 dead 却没写为什么放弃，删掉程序后 grep 就答不出来了。"""
    cli.cmd_check(check_args())
    out = capsys.readouterr().out
    assert "dead" in out and "结论" in out


# ------------------------------------------------------------ 翻译（trace tr）
# 「还没翻译」是文件不存在这个派生事实，没有任何地方存着待办表。所以必须有一条
# 命令能现算出「还欠哪些」——否则「延迟翻译」这条路上，人隔几天回来就无从下手。


def tr_args(**kw):
    return type("A", (), {"project": None, "lang": "en", "step": None, "project_note": False,
                          "file": None, "title": None, "drop": False, **kw})()


def test_tr_lists_the_steps_that_still_have_no_translation(graded, capsys):
    assert cli.cmd_tr(tr_args()) == 0
    out = capsys.readouterr().out
    assert "还缺 3 份" in out
    assert "001" in out and "基线" in out, "光给 id 的话还得再查一遍才知道要翻什么"
    assert "project.en.md" in out


def test_tr_writes_a_whole_translated_file_including_its_title(graded, tmp_path: Path, capsys):
    """人和 agent 手上真正存在的东西是一份写好的 note.en.md。逼他们先手工把
    front-matter 剥掉只会剥错，所以这里整份收下，只采用 title。"""
    src = tmp_path / "note.en.md"
    src.write_text("---\ntitle: Baseline\n---\n\n## Why\nEstablish the baseline.\n", encoding="utf-8")
    assert cli.cmd_tr(tr_args(step="001", file=str(src))) == 0
    capsys.readouterr()

    sd = core.steps_dir_of(graded, "第一个课题")
    made = next(sd.glob("001_*")) / "note.en.md"
    text = made.read_text(encoding="utf-8")
    assert "title: Baseline" in text and "## Why" in text
    assert "基线" in (next(sd.glob("001_*")) / "note.md").read_text(encoding="utf-8"), "原文没动"

    assert cli.cmd_tr(tr_args()) == 0
    assert "还缺 2 份" in capsys.readouterr().out


def test_tr_says_out_loud_which_structural_keys_it_threw_away(graded, tmp_path: Path, capsys):
    """静默丢弃等于让人以为 parent 写进去生效了。译文里的结构键读都不读，
    但必须吵一声——这是「note.md 永远赢」唯一能被人看见的地方。"""
    src = tmp_path / "note.en.md"
    src.write_text("---\ntitle: Baseline\nparent: 007\nstatus: done\n---\n\n## Why\nx\n",
                   encoding="utf-8")
    cli.cmd_tr(tr_args(step="001", file=str(src)))
    out = capsys.readouterr().out
    assert "parent" in out and "双真相源" in out
    made = next(core.steps_dir_of(graded, "第一个课题").glob("001_*")) / "note.en.md"
    assert "parent" not in made.read_text(encoding="utf-8"), "被吵过的键更不许落盘"


def test_tr_translates_the_project_note_too(graded, tmp_path: Path, capsys):
    src = tmp_path / "project.en.md"
    src.write_text("---\nname: My topic\n---\n\n## Works\n- dedup helps\n", encoding="utf-8")
    assert cli.cmd_tr(tr_args(project_note=True, file=str(src))) == 0
    capsys.readouterr()
    text = (core.project_dir(graded, "第一个课题") / "project.en.md").read_text(encoding="utf-8")
    assert "name: My topic" in text and "## Works" in text


def test_tr_drops_a_language_without_touching_the_original(graded, tmp_path: Path, capsys):
    src = tmp_path / "note.en.md"
    src.write_text("---\ntitle: Baseline\n---\n\n## Why\nx\n", encoding="utf-8")
    cli.cmd_tr(tr_args(step="001", file=str(src)))
    assert cli.cmd_tr(tr_args(step="001", drop=True)) == 0
    capsys.readouterr()
    d = next(core.steps_dir_of(graded, "第一个课题").glob("001_*"))
    assert not (d / "note.en.md").exists()
    assert (d / "note.md").is_file()


def test_tr_refuses_a_non_utf8_translation_instead_of_writing_mojibake(graded, tmp_path: Path):
    """定死按 UTF-8 回写会把原文替换成一串 �，那是不可逆的损失。和 note.md 一个规矩。"""
    import trace_write as W

    src = tmp_path / "note.en.md"
    src.write_bytes("---\ntitle: 标题\n---\n\n## Why\n正文\n".encode("utf-16"))
    with pytest.raises(W.WriteError, match="UTF-8"):
        cli.cmd_tr(tr_args(step="001", file=str(src)))


def test_tr_needs_to_be_told_where_the_translation_goes(graded, tmp_path: Path):
    """--file 而不说翻的是哪一步，最贴心的猜法（默认翻项目笔记）恰好是最坏的：
    一份步骤译文会被静默写成项目笔记。"""
    import trace_write as W

    src = tmp_path / "x.md"
    src.write_text("## Why\nx\n", encoding="utf-8")
    with pytest.raises(W.WriteError, match="--step"):
        cli.cmd_tr(tr_args(file=str(src)))


def test_check_still_says_nothing_about_missing_translations(graded, capsys):
    """用户明确没选「缺翻译报警告」这一项，这条钉的就是那个决定。
    L0–L4 问的是「这个结果追不追得到」，不是「翻译全不全」——只写了中文的记录
    一样是可溯源的，为它挂一条黄字只会让真警告一起被忽略。"""
    assert cli.cmd_check(check_args()) == 0
    assert cli.cmd_check(check_args(strict=True)) == 1      # 内容层缺陷仍然拦得住
    out = capsys.readouterr().out
    assert "翻译" not in out and "note.en.md" not in out


def test_the_static_export_ships_the_i18n_table(sandbox, tmp_path: Path):
    """漏了 i18n.js 就是白屏：index.html 里的 <script> 是写死的，
    window.i18n 缺席时 app.js 第一次调 t() 就抛，而 file:// 下没有日志可看。"""
    assert "i18n.js" in cli.STATIC_ASSETS
    assert (cli.WEB / "i18n.js").is_file(), "STATIC_ASSETS 里写了一个不存在的文件"


def test_check_stays_green_by_default_but_strict_fails(graded, capsys):
    """默认不因内容缺陷失败（wip 天天红一片只会训练大家忽略警告），--strict 才拦。"""
    assert cli.cmd_check(check_args()) == 0
    assert cli.cmd_check(check_args(strict=True)) == 1
    assert "--strict" in capsys.readouterr().out
