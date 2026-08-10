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


# ------------------------------------------------------------ check 的三档
# 新加的三条诊断（小节下面只有子标题、表格/代码块没配说明）都是 warn 级，
# 但它们**一条都不影响 L0–L4**。混在一起打，人会以为「表格没写说明」和
# 「dead 没写结论」一样严重，然后开始整体忽略这一段——那正是警告失效的方式。


def test_check_separates_hints_from_things_that_cost_you_a_level(graded, capsys):
    import trace_write as W

    sd = core.steps_dir_of(graded, "第一个课题")
    W.create_step(sd, title="带表格的", status="done",
                  body="## 为什么\n试试\n## 做了什么\n跑\n"
                       "## 结果\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
                       "## 结论\n有用\n")
    assert cli.cmd_check(check_args()) == 0
    out = capsys.readouterr().out
    assert "写法提示" in out and "不影响 L0–L4" in out
    assert "表格" in out.split("写法提示")[1], "提示要归到提示那一档里，不混进 ⚠"


def test_hints_alone_do_not_make_strict_fail(sandbox, tmp_path: Path, monkeypatch, capsys):
    """--strict 是给 CI 用的闸门。用「这张表没配一句说明」拦住一次合并，
    只会让人加 --no-verify —— 然后真正的缺陷也一起漏过去了。"""
    import trace_write as W

    data = tmp_path / "hints"
    data.mkdir()
    init(sandbox, data_dir=str(data))
    monkeypatch.setattr(cli, "load_config", lambda: {"data_dir": str(data)})
    sd = core.steps_dir_of(data, "第一个课题")
    W.create_step(sd, title="全都写了", status="done", commit="abc",
                  paths=["/blue/x | output | 训练集"],
                  body="## 为什么\n试试\n## 做了什么\n\n```bash\npython train.py\n```\n"
                       "## 结果\n0.94\n## 结论\n有用\n## 下一步\n继续\n")
    assert cli.cmd_check(check_args(strict=True)) == 0
    assert "写法提示" in capsys.readouterr().out


# ------------------------------------------------------------ mv（移动）


def mv_args(**kw):
    return type("A", (), {"project": None, "parent": None, "by": "human", "date": "", **kw})()


def test_mv_moves_the_step_and_writes_why(graded, capsys):
    assert cli.cmd_mv(mv_args(id="003", parent="001", reason="003 的输入来自 001，不是 002")) == 0
    out = capsys.readouterr().out
    assert "moved:" in out and "003 的输入来自 001" in out
    import trace_write as W
    sd = core.steps_dir_of(graded, "第一个课题")
    assert W.load(sd)["003"].parent == "001"


def test_mv_without_a_reason_is_impossible(graded):
    """原因不是可选项：移完这棵树就和创建顺序对不上了，
    「为什么 003 挂在 001 下面」只有那句话答得了。"""
    with pytest.raises(SystemExit):        # argparse 的 required=True
        cli.main(["mv", "003", "--parent", "001"])


def test_mv_says_how_many_steps_came_along(graded, capsys):
    cli.cmd_mv(mv_args(id="002", parent=None, reason="002 那一支其实是独立的一条线"))
    assert "后代" in capsys.readouterr().out, "移的是一步还是一支，是两个决定"


# ------------------------------------------------------------ paths --check


def paths_args(**kw):
    return type("A", (), {"project": None, "kind": None, "missing": False,
                          "check": False, "count": False, **kw})()


def test_paths_check_writes_back_only_what_this_machine_can_see(sandbox, tmp_path: Path,
                                                                monkeypatch, capsys):
    """**够不着 ≠ 不存在。** /blue/… 多半挂在超算上，在这台机器上跑一遍不该把
    一份好好的记录盖上「已确认不存在」。"""
    import trace_write as W

    data = tmp_path / "d"
    data.mkdir()
    init(sandbox, data_dir=str(data))
    monkeypatch.setattr(cli, "load_config", lambda: {"data_dir": str(data)})
    live = tmp_path / "还在.pt"
    live.write_bytes(b"x" * 5)
    gone = tmp_path / "没了"
    sd = core.steps_dir_of(data, "第一个课题")
    W.create_step(sd, title="a", paths=[f"{live} | output | 权重", f"{gone} | output | 删掉的",
                                        "/blue/没挂上的/x | input | 超算上的",
                                        "s3://bucket/k | output | 远端"])
    assert cli.cmd_paths(paths_args(check=True)) == 0
    rows = {p["location"]: p for p in W.load(sd)["001"].paths}
    assert rows[str(live)]["state"] == "present" and rows[str(live)]["size"] == 5
    assert rows[str(gone)]["state"] == "missing"
    assert rows["/blue/没挂上的/x"]["state"] == "" and rows["s3://bucket/k"]["state"] == ""
    out = capsys.readouterr().out
    assert "够不着" in out and "已确认不存在" in out
    assert str(gone) in out


def test_new_can_write_inputs_and_a_code_snapshot(sandbox, tmp_path: Path, monkeypatch, capsys):
    """⑤ 的落点之一：代码不在 git 里时，快照目录也要能从命令行写进去——
    否则人还是只能把它塞进 --commit，而那一格里躺着一个不是 commit 的东西。"""
    import trace_write as W

    data = tmp_path / "d4"
    data.mkdir()
    init(sandbox, data_dir=str(data))
    monkeypatch.setattr(cli, "load_config", lambda: {"data_dir": str(data)})
    new_args = type("A", (), {"project": None, "parent": None, "status": "wip", "date": "",
                              "commit": "", "author": "human", "tags": "", "path": None,
                              "title": "配对", "input": ["001 | pocket.csv"],
                              "branch": "", "decision": "",
                              "code": ["snapshot | /orange/snap/20260809 | manifest=MANIFEST.md5"]})()
    sd = core.steps_dir_of(data, "第一个课题")
    W.create_step(sd, title="上游")
    assert cli.cmd_new(new_args) == 0
    text = (sd / W.load(sd)["002"].dirname / core.NOTE_NAME).read_text(encoding="utf-8")
    assert "input: 001 | pocket.csv" in text
    assert "code: snapshot | /orange/snap/20260809 | manifest=MANIFEST.md5" in text


def test_paths_missing_lists_only_what_vanished_and_never_deletes_it(sandbox, tmp_path: Path,
                                                                     monkeypatch, capsys):
    import trace_write as W

    data = tmp_path / "d2"
    data.mkdir()
    init(sandbox, data_dir=str(data))
    monkeypatch.setattr(cli, "load_config", lambda: {"data_dir": str(data)})
    sd = core.steps_dir_of(data, "第一个课题")
    W.create_step(sd, title="a", paths=[
        "/orange/在 | output | 还在的 | checked=2026-08-09",
        "/blue/没了 | input | 57 GB 的那个 | size=61203283968 missing=2026-08-09"])
    assert cli.cmd_paths(paths_args(missing=True)) == 0
    out = capsys.readouterr().out
    assert "/blue/没了" in out and "/orange/在" not in out
    assert "57 GB" in out or "GB" in out, "大小要看得见——「没了的那个有多大」是要留的信息"
    assert len(W.load(sd)["001"].paths) == 2, "--missing 只是过滤显示，一行都不删"


def test_paths_shows_the_role_and_the_last_verdict(sandbox, tmp_path: Path, monkeypatch, capsys):
    import trace_write as W

    data = tmp_path / "d3"
    data.mkdir()
    init(sandbox, data_dir=str(data))
    monkeypatch.setattr(cli, "load_config", lambda: {"data_dir": str(data)})
    sd = core.steps_dir_of(data, "第一个课题")
    W.create_step(sd, title="a", paths=["/orange/pockets | output | 纯 RNA 口袋 | n=4554 size=620756992"])
    cli.cmd_paths(paths_args())
    out = capsys.readouterr().out
    assert "产物" in out and "4554 条" in out and "MB" in out
    assert "从未核对过" in out, "没查过和查过说「还在」是两回事，不能长得一样"


# ------------------------------------------------------------ 岔路口（fork / forks / check）
#
# 这几条钉的是 CLI 这个门面。它和 REST / MCP 用的是同一份判据（core 的候选组），
# 所以这里不重验「谁和谁是一组」，只验**分档**：哪些算错误、哪些只是写法提示、
# 哪些连毛病都不是（未决的岔路口是待办）。分错档的后果是人整体忽略这一段。


def fork_args(**kw):
    return type("A", (), {"project": None, "decision": "", "note": None, **kw})()


def forks_args(**kw):
    return type("A", (), {"project": None, "all": False, **kw})()


@pytest.fixture()
def forked(sandbox, tmp_path: Path, monkeypatch):
    """001 底下两条互斥候选，都还活着 —— 一个还没做决定的岔路口。"""
    import trace_write as W

    data = tmp_path / "forkdata"
    data.mkdir()
    init(sandbox, data_dir=str(data))
    monkeypatch.setattr(cli, "load_config", lambda: {"data_dir": str(data)})
    sd = core.steps_dir_of(data, "第一个课题")
    body = "## 为什么\n试试\n## 做了什么\n跑\n## 结论\n有\n"
    W.create_step(sd, title="基线", status="done", commit="abc",
                  paths=["/blue/x | output | 训练集"], body=body)
    W.create_step(sd, parent="001", title="调采样权重", status="done", commit="abc",
                  paths=["/blue/x | output | 训练集"], body=body)
    W.create_step(sd, parent="001", title="改损失函数", status="done", commit="abc",
                  paths=["/blue/x | output | 训练集"], body=body)
    return data


def test_fork_marks_a_group_and_writes_the_question_on_the_parent(forked, capsys):
    assert cli.cmd_fork(fork_args(ids=["002", "002b"], decision="类别不平衡怎么处理？",
                                  note=["002=先试最便宜的"])) == 0
    out = capsys.readouterr().out
    assert "标成互斥候选" in out and "未决 · 2 选 1" in out
    import trace_write as W
    sd = core.steps_dir_of(forked, "第一个课题")
    assert W.load(sd)["002"].branch == "alternative"
    assert W.load(sd)["002"].branch_note == "先试最便宜的"
    assert W.load(sd)["001"].decision == "类别不平衡怎么处理？"


def test_fork_refuses_steps_that_do_not_share_a_parent(forked):
    """跨父节点标只会得到几个各含一个候选的组，一条错都不报——
    而人以为自己刚记下了一个岔路口。同父校验是这个子命令存在的主要理由。"""
    import trace_write as W

    with pytest.raises(W.WriteError) as e:
        cli.cmd_fork(fork_args(ids=["001", "002"]))
    assert "同一个父节点" in str(e.value)


def test_forks_lists_only_the_undecided_ones_unless_asked(forked, capsys):
    cli.cmd_fork(fork_args(ids=["002", "002b"], decision="类别不平衡怎么处理？"))
    capsys.readouterr()
    assert cli.cmd_forks(forks_args()) == 0
    out = capsys.readouterr().out
    assert "共 1 个岔路口，其中 1 个还没做决定" in out
    assert "类别不平衡怎么处理？" in out and "002b" in out

    import trace_write as W
    W.update_step(core.steps_dir_of(forked, "第一个课题"), "002b",
                  {"status": "dead", "body": "## 为什么\n试试\n## 做了什么\n跑\n"
                                             "## 结论\n重加权反而更差，放弃这条\n"})
    cli.cmd_forks(forks_args())
    assert "已经有结论了" in capsys.readouterr().out, "定了的不再天天列出来"
    cli.cmd_forks(forks_args(all=True))
    assert "已定 → 002" in capsys.readouterr().out


def test_check_files_the_undecided_fork_as_a_todo_not_as_a_warning(forked, capsys):
    """**它不是缺陷，是待办。**混进警告栏会稀释警告的分量，而人消除「警告」
    最省事的办法是随手把一条支标成 dead —— 那是拿假结论换绿色。"""
    cli.cmd_fork(fork_args(ids=["002", "002b"], decision="类别不平衡怎么处理？"))
    capsys.readouterr()
    assert cli.cmd_check(check_args()) == 0
    out = capsys.readouterr().out
    assert "还有 1 个岔路口没做决定" in out
    assert "待办，不是缺陷，不计入退出码" in out
    body = out.split("岔路口没做决定")[0]
    assert "⚠ [001" not in body and "undecided_fork" not in body, \
        "内核那条 undecided_fork 不该在警告栏里再说一遍"


def test_an_undecided_fork_never_fails_strict(forked, capsys):
    """--strict 是给 CI 用的闸门。用「你还有个决定没做」拦住一次合并，
    等于逼人在还没想清楚的时候先编一个结论。"""
    cli.cmd_fork(fork_args(ids=["002", "002b"], decision="类别不平衡怎么处理？"))
    assert cli.cmd_check(check_args(strict=True)) == 0


def test_the_two_fork_writing_hints_are_hints_not_warnings(forked, capsys):
    """「一组只有一个候选」「有候选却没写在决定什么」说的是这条记录还差一句人写的话，
    和 L0–L4 无关：一个岔路口写不写得清楚，不改变「这个结果追不追得到」。"""
    import trace_write as W

    W.update_step(core.steps_dir_of(forked, "第一个课题"), "002", {"branch": "alternative"})
    assert cli.cmd_check(check_args(strict=True)) == 0
    out = capsys.readouterr().out
    assert "写法提示" in out
    assert "只有一个" in out.split("写法提示")[1]


def test_mv_says_what_it_did_to_both_forks(forked, capsys):
    """移动会改变**两个**岔路口的成员。事后只能靠再跑一遍 forks 才看得见，
    而人下次看到的会是一条来路不明的「这一组只有一个候选」。"""
    cli.cmd_fork(fork_args(ids=["002", "002b"], decision="类别不平衡怎么处理？"))
    capsys.readouterr()
    cli.cmd_mv(mv_args(id="002b", parent=None, reason="002b 其实是独立的一条线"))
    out = capsys.readouterr().out
    assert "原来那个岔路口 001" in out and "只有一个候选" in out
