"""缝隙里的断言。

这一批缺陷有个共同特征：**没有任何一个模块拥有它们**。
一个 agent 修渲染器、一个修内核、一个修服务端，每个人都在自己的文件里做对了，
而 bug 恰好长在两份文件的接缝上——于是三方的报告里都写着"不是我的文件"。

所以这个文件按接缝组织，不按模块。
"""

from pathlib import Path

import pytest

import trace_core as core
import trace_git
import trace_mcp as M
import trace_write as W

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import trace_server as S  # noqa: E402

TOKEN = "seam-test-token"


@pytest.fixture()
def proj(tmp_path: Path):
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    return tmp_path, core.steps_dir_of(tmp_path, "课题")


# ───────────────────────────── 渲染器 ↔ 内核：带空格的文件名

def test_a_spaced_filename_figure_is_still_required_to_have_a_caption(proj):
    """md.js 学会渲染 `![](<loss curve.png>)` 之后，core 的图注检查一度看不见它。

    后果比"少报一条警告"重：漏图注的图恰恰最常出现在文件名带空格的截图上
    （从别处拷来的、带日期和括号的），而图注是这张图对 agent 的**全部**信息。
    渲染器认得的每一种图片写法，检查器都必须认得——否则宽松的那一侧
    就是默认逃逸通道。
    """
    _root, d = proj
    s, _ = W.create_step(d, title="有图", body="## 为什么\n试\n\n![](<loss curve (run 42).png>)\n")
    codes = [w["code"] for w in core.compile_forest(d)["warnings"]]
    assert "figure_without_caption" in codes


def test_a_spaced_filename_with_a_caption_passes(proj):
    _root, d = proj
    W.create_step(d, title="有图有注",
                  body='## 为什么\n试\n\n![](<loss curve.png> "第 12 轮后验证集回升")\n')
    codes = [w["code"] for w in core.compile_forest(d)["warnings"]]
    assert "figure_without_caption" not in codes


def test_the_bare_form_still_works(proj):
    """加了尖括号分支之后，原来的裸路径形式不能退化。"""
    _root, d = proj
    W.create_step(d, title="裸路径", body="## 为什么\n试\n\n![](loss.png)\n")
    assert any(w["code"] == "figure_without_caption"
               for w in core.compile_forest(d)["warnings"])


# ───────────────────────────── 内核 ↔ 项目笔记：编码

def test_a_non_utf8_project_note_is_not_silently_mojibaked(proj):
    """步骤的 note.md 早就有编码告警了，project.md 一直是 errors='replace'。

    project.md 装的是项目级沉淀——「回译在这个数据集上一直没用」是三次尝试之后的
    判断。糊成一串 U+FFFD 不比糊掉一步的正文轻，而且更难发现：它只在侧边栏露一行。
    """
    root, _d = proj
    note = core.project_dir(root, "课题") / core.PROJECT_NOTE
    note.write_bytes("---\nname: 课题\n---\n\n## 坑\n- 回译一直没用\n".encode("gbk"))
    p = next(x for x in core.scan_projects(root) if x.slug == "课题")
    assert "⚠" in p.name, "编码有问题必须说出来，不能只在正文里留一串问号"


def test_a_normal_project_note_gets_no_warning_marker(proj):
    root, _d = proj
    W.update_project(root, "课题", add=("pitfall", "正常的中文"))
    p = next(x for x in core.scan_projects(root) if x.slug == "课题")
    assert "⚠" not in p.name


# ───────────────────────────── 服务端 ↔ 前端：只剩一个项目时的首页

@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(S, "ROOT", tmp_path)
    app = S.create_app({"data_dir": ".", "space": "", "token": TOKEN, "git": {"enabled": False}})
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "唯一的课题")
    with TestClient(app) as c:
        yield c


def test_the_only_project_still_gets_the_shortcut(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302, "只有一个项目时直接跳进去，省掉一个只有一张卡片的首页"


def test_but_the_index_stays_reachable(client):
    """没有旁路的话，从项目页点「所有项目 ▸」会被 302 立刻弹回来——

    于是首页上的跨项目搜索和「新建项目」在只有一个项目时永远够不着，
    而"只有一个项目"正是最需要建第二个的时候。
    """
    r = client.get("/?all=1", follow_redirects=False)
    assert r.status_code == 200


def test_the_web_client_asks_for_the_bypass():
    """前端和服务端必须约定同一个旁路参数，否则这个洞只是换了个地方。"""
    app_js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text(encoding="utf-8")
    assert "?all=1" in app_js, "homeHref 必须带上旁路参数"


# ───────────────────────────── 写入 ↔ git：锁文件

def test_the_lock_file_never_gets_committed(tmp_path: Path):
    """靠数据仓的 .gitignore 是不够的——那份文件在**另一个仓库**里。

    用户忘了加一行，每次同步就 commit 一个运行期锁，两台机器一拉就冲突，
    而冲突的是个空文件。pathspec 是我们控制得了的，所以这里断言的是**真跑一次
    git 之后仓库里有什么**，不是源码里写了什么字符串。
    """
    import subprocess

    def git(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    if git("init", "-q").returncode != 0:
        pytest.skip("这台机器上没有 git")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "test")

    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    d = core.steps_dir_of(tmp_path, "课题")
    W.create_step(d, title="一步")                       # 顺带留下一个 .trace-lock
    lock = next(tmp_path.rglob("*" + W.LOCK_NAME), None)
    assert lock is not None, "锁文件应该在项目目录下（这条测试的前提）"

    sync = trace_git.GitSync(tmp_path, enabled=True, remote="origin", branch="main")
    sync.commit_now(["课题/001"])

    tracked = git("ls-files").stdout.split()
    assert any("note.md" in t for t in tracked), "记录本身要进去"
    assert not [t for t in tracked if t.endswith(W.LOCK_NAME)], \
        f"运行期的锁被 commit 进了历史: {tracked}"


def test_the_lock_name_has_one_source_of_truth():
    assert trace_git.W.LOCK_NAME == W.LOCK_NAME


# ───────────────────────────── MCP ↔ 格式标准：pip 装的机器

def test_the_inline_standard_covers_what_the_file_would_have_told_them():
    """pip 装的机器上没有 FORMAT.md（pyproject 只打包三个 .py）。

    那条路径下 agent 对格式的全部知识来自 initialize 的 instructions。
    所以凡是"照着写会出错"的硬约定，都必须在内联版里，不能只在文件里。
    """
    text = M.INSTRUCTIONS
    for name in W.INSIGHT_SECTIONS.values():
        assert name in text, f"project.md 的小节名 {name} 没写进内联标准，手写就会对不上"
    assert "已删除" in text and "绝不要手工改" in text
    assert "trace_insight" in text, "得告诉它用工具写，别整段覆盖"


def test_the_inline_standard_is_honest_about_the_renderer():
    """渲染器是手写子集。不说清楚的话，agent 按 CommonMark 写，人看到的是原文。"""
    text = M.INSTRUCTIONS
    assert "有意不做" in text
    assert "数学公式" in text, "科研记录几乎一定会写公式，必须说清它不会被排版"
    assert "<loss curve.png>" in text, "带空格的文件名要给出正确写法"


def test_the_section_names_are_not_hardcoded_twice():
    """内联标准里的小节名如果和 trace_write 脱钩，下次改名就又对不上了。

    这条测试就是那根绳子：它不检查文本长什么样，只检查两边说的是同一件事。
    """
    for name in W.INSIGHT_SECTIONS.values():
        assert f"## {name}" in M.INSTRUCTIONS
