"""写入必须是「要么旧的、要么新的」，不能有中间态。

这一组测试守的是 G4：删掉全部程序之后，剩下的 .md 仍然能回答
「我当年为什么放弃了 X」。前提是那份正文一直在磁盘上——
一次失败的写入如果留下 0 字节的空文件，G4 当场就破了。

一起守在这里的还有两件同源的事：
  * 跨进程互斥（两个写入者同时建步骤会分到同一个 id）；
  * 摘要指纹与乐观并发（人在网页保存不能静默抹掉 agent 期间写的内容）。
"""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import trace_core as core
import trace_write as W

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def proj(tmp_path: Path):
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    return tmp_path, core.steps_dir_of(tmp_path, "课题")


def note_of(steps_dir: Path, step) -> Path:
    return steps_dir / step.dirname / core.NOTE_NAME


# ------------------------------------------------------ 编码失败不许清空原文

# 落单代理字符：LLM 截断 emoji、从 JS 侧 slice 半个代理对都会产生，
# 它在 JSON 里完全合法，一路能传到写盘那一刻。
LONE_SURROGATE = "正常内容\n\ud800\n更多内容\n"


def test_a_body_that_cannot_be_encoded_leaves_the_old_note_byte_for_byte(proj):
    """老实现是 write_text（先截断再写）：一次 500 就把整条记录变成 0 字节。"""
    _root, d = proj
    s, _ = W.create_step(d, title="有正文的一步", body="## 结论\n这条线走通了。")
    note = note_of(d, s)
    before = note.read_bytes()
    assert len(before) > 0

    with pytest.raises(W.WriteError):
        W.update_step(d, s.id, {"body": LONE_SURROGATE})

    assert note.read_bytes() == before, "写失败之后留在磁盘上的必须是旧内容，不是空文件"
    assert W.load(d)[s.id].body.strip().endswith("这条线走通了。")


def test_the_encoding_error_blames_the_content_not_the_server(proj):
    """错误信息要让人知道该删哪个字符，而不是以为服务器坏了去重启。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    with pytest.raises(W.WriteError) as e:
        W.update_step(d, s.id, {"body": LONE_SURROGATE})
    msg = str(e.value)
    assert "UTF-8" in msg
    assert "原文一个字节都没动" in msg


def test_a_failed_write_leaves_no_temp_file_behind(proj):
    """临时文件会被 git add -A 扫进数据仓，也会让人以为目录坏了。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    with pytest.raises(W.WriteError):
        W.update_step(d, s.id, {"body": LONE_SURROGATE})
    assert sorted(p.name for p in (d / s.dirname).iterdir()) == [core.NOTE_NAME]


def test_append_body_is_protected_too(proj):
    """append 端点走的是同一个 update_step，不能有第二条不安全的写入路径。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x", body="## 结果\n原有的一段")
    before = note_of(d, s).read_bytes()
    with pytest.raises(W.WriteError):
        W.append_body(d, s.id, LONE_SURROGATE)
    assert note_of(d, s).read_bytes() == before


def test_a_bad_body_on_create_leaves_no_half_built_step(proj):
    """校验要排在 mkdir 之前，否则会留下一个只有目录、没有 note.md 的空壳——
    scan 会静默跳过它，但那个号已经被占掉了。"""
    _root, d = proj
    with pytest.raises(W.WriteError):
        W.create_step(d, title="会失败的一步", body=LONE_SURROGATE)
    assert list(d.iterdir()) == []
    assert W.create_step(d, title="下一步")[0].id == "001", "失败的那次不该占号"


def test_project_note_is_written_atomically_too(proj):
    """洞察和「已删除」都住在 project.md 里，它和 note.md 一样不能被截成空文件。"""
    root, _d = proj
    W.update_project(root, "课题", add=("idea", "先跑通再说"))
    note = core.project_dir(root, "课题") / core.PROJECT_NOTE
    before = note.read_bytes()
    with pytest.raises(W.WriteError):
        W.update_project(root, "课题", insights="## 坑\n" + LONE_SURROGATE)
    assert note.read_bytes() == before


# ------------------------------------------------------ write_atomic 本身


def test_write_atomic_puts_the_exact_bytes_on_disk(tmp_path: Path):
    target = tmp_path / "a.md"
    W.write_atomic(target, "第一行\n第二行\n")
    assert target.read_bytes() == "第一行\n第二行\n".encode("utf-8"), "不许做换行翻译"
    W.write_atomic(target, b"\x00\x01raw")
    assert target.read_bytes() == b"\x00\x01raw", "bytes 原样落盘"


def test_write_atomic_does_not_touch_the_target_when_encoding_fails(tmp_path: Path):
    target = tmp_path / "a.md"
    W.write_atomic(target, "旧内容\n")
    with pytest.raises(W.WriteError):
        W.write_atomic(target, "\ud800")
    assert target.read_text(encoding="utf-8") == "旧内容\n"
    assert [p.name for p in tmp_path.iterdir()] == ["a.md"], "临时文件不许残留"


def test_write_atomic_can_overwrite_an_existing_file(tmp_path: Path):
    """Windows 上 os.replace 覆盖已存在的目标是允许的——这条在 POSIX 上不会失败，
    留着是为了防止有人为了迁就 Windows 改成 unlink+rename（那就不原子了）。"""
    target = tmp_path / "a.md"
    target.write_bytes(b"old")
    W.write_atomic(target, b"new")
    assert target.read_bytes() == b"new"


# ------------------------------------------------------ 非 UTF-8 的原文不许被毁


GBK_NOTE = "---\nid: 001\nstatus: wip\ntitle: 中文标题\n---\n\n## 为什么\n中文正文\n"


def test_a_non_utf8_note_is_refused_instead_of_being_rewritten_as_question_marks(proj):
    """读侧用的是 errors='replace'：GBK 的 note.md 会被读成一串 U+FFFD，
    照写回去原始字节就永久没了。写入侧必须先确认自己读到的确实是原文。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    note = note_of(d, s)
    note.write_bytes(GBK_NOTE.encode("gbk"))
    raw = note.read_bytes()

    with pytest.raises(W.WriteError) as e:
        W.update_step(d, "001", {"status": "done"})       # = 网页上点一下 done
    assert "UTF-8" in str(e.value) and "转码" in str(e.value)
    assert note.read_bytes() == raw, "原始 GBK 字节必须还在，等人手工转码"


def test_a_non_utf8_project_note_is_refused_too(proj):
    root, _d = proj
    note = core.project_dir(root, "课题") / core.PROJECT_NOTE
    note.write_bytes("---\nname: 课题\n---\n\n## 坑\n- 中文\n".encode("gbk"))
    raw = note.read_bytes()
    with pytest.raises(W.WriteError, match="UTF-8"):
        W.update_project(root, "课题", add=("pitfall", "又一个坑"))
    assert note.read_bytes() == raw


def test_a_bom_prefixed_note_is_still_writable(proj):
    """BOM 是合法 UTF-8，core.parse_note 已经处理过它——不能把它误判成非 UTF-8。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    note = note_of(d, s)
    note.write_bytes("﻿".encode("utf-8") + note.read_bytes())
    W.update_step(d, s.id, {"status": "done"})
    assert W.load(d)[s.id].status == "done"


# ------------------------------------------------------ 摘要指纹与冲突


def test_the_digest_formula_is_the_one_agreed_across_agents(proj):
    """core 在 Step.to_dict() 里暴露的 digest 和这里必须逐字一致，
    否则客户端拿到的指纹永远对不上，冲突检测会退化成每次都误报。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    note = note_of(d, s)
    assert W.digest_of(note) == hashlib.sha256(note.read_bytes()).hexdigest()[:12]
    assert len(W.digest_of(note)) == 12


def test_a_stale_expect_is_a_conflict_and_nothing_is_written(proj):
    """需求 1 的招牌场景：你在网页里编辑，同一时间你的 agent 往同一步写了东西。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x", body="## 结果\nbase")
    stale = W.digest_of(note_of(d, s))

    W.update_step(d, s.id, {"body": "## 结果\nbase\nAGENT 写的"})   # agent 先落盘
    after_agent = note_of(d, s).read_bytes()

    with pytest.raises(W.Conflict) as e:
        W.update_step(d, s.id, {"body": "## 结果\nbase\n人写的"}, expect=stale)
    assert "重新读一遍" in str(e.value)
    assert note_of(d, s).read_bytes() == after_agent, "冲突时一个字节都不许写"


def test_a_matching_expect_goes_through(proj):
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    cur = W.digest_of(note_of(d, s))
    W.update_step(d, s.id, {"status": "done"}, expect=cur)
    assert W.load(d)[s.id].status == "done"


def test_an_empty_expect_means_no_check(proj):
    """老调用方（CLI、旧客户端）不传 expect，行为必须和以前完全一样。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    W.update_step(d, s.id, {"status": "done"})
    W.update_step(d, s.id, {"status": "dead"}, expect="")
    assert W.load(d)[s.id].status == "dead"


def test_expect_carried_inside_the_patch_is_honoured_not_rejected(proj):
    """服务端可能把整个请求体当 patch 透传。如果 expect 在这里变成
    「不支持的字段」，冲突检测就被整个绕过去了。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    stale = W.digest_of(note_of(d, s))
    W.update_step(d, s.id, {"status": "done"})
    with pytest.raises(W.Conflict):
        W.update_step(d, s.id, {"status": "dead", "expect": stale})
    # 空的 expect 同样不能变成「不支持的字段」——网页表单会原样带一个空串上来
    W.update_step(d, s.id, {"status": "dead", "expect": ""})
    assert W.load(d)[s.id].status == "dead"


# ------------------------------------------------------ 跨进程互斥

# 独立进程里建一步。用一个 go 文件当发令枪，保证它们真的在抢。
WORKER = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import trace_write as W

steps, tag, key, go = Path(sys.argv[2]), sys.argv[3], sys.argv[4], Path(sys.argv[5])
while not go.exists():
    time.sleep(0.005)
try:
    step, created = W.create_step(steps, title="并发-" + tag, key=key)
    print(json.dumps({"id": step.id, "created": created}))
except Exception as exc:
    print(json.dumps({"error": type(exc).__name__ + ": " + str(exc)}))
"""


def _race(steps_dir: Path, tmp_path: Path, n: int, key: str = "") -> list[dict]:
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER, encoding="utf-8")
    go = tmp_path / "go"
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker), str(ROOT), str(steps_dir), str(i), key, str(go)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        for i in range(n)
    ]
    time.sleep(0.6)          # 等所有解释器都起来、都堵在 go 上
    go.write_text("go", encoding="utf-8")
    out = []
    for p in procs:
        so, se = p.communicate(timeout=120)
        assert p.returncode == 0, se
        out.append(json.loads(so.strip().splitlines()[-1]))
    return out


def test_concurrent_processes_never_get_the_same_id(proj, tmp_path: Path):
    """alloc_id 是「扫一遍取 max+1」。没有跨进程锁的话，只要两个写入者的
    load() 都在对方 mkdir() 之前完成就会撞号——而「网页服务 + 本机 MCP
    同时读写同一份文件」正是推荐配置，所以这是必然会发生的事。"""
    _root, d = proj
    W.create_step(d, title="起点")
    got = _race(d, tmp_path, 5)
    assert all("error" not in r for r in got), got
    ids = [r["id"] for r in got]
    assert len(set(ids)) == len(ids), f"撞号了: {ids}"

    f = core.compile_forest(d)
    assert len(f["steps"]) == 6
    assert not [w for w in f["warnings"] if w["code"] == "duplicate_id"]


def test_the_same_idempotency_key_across_processes_yields_one_step(proj, tmp_path: Path):
    """agent 重试时同一个 key 会并发打进来。老实现在 exists() 与 mkdir() 之间
    是 TOCTOU：1 个成功、其余抛**裸 FileExistsError**——它不是 WriteError，
    服务端接不住，直接漏成 500。"""
    _root, d = proj
    got = _race(d, tmp_path, 5, key="run-2026-08-08-abc")
    assert all("error" not in r for r in got), got
    assert sum(1 for r in got if r["created"]) == 1, "只该有一个进程真的建了步骤"
    assert len({r["id"] for r in got}) == 1, "同一个 key 必须对应同一步"
    assert len(W.load(d)) == 1


def test_the_lock_file_is_not_a_step_and_not_inside_the_steps_dir(proj):
    """锁是运行期的东西，不是记录。它必须扫不进步骤树，也不能改变 steps 目录指纹
    （否则每次加锁都会白推一次 SSE）。"""
    _root, d = proj
    sig_before = core.signature(d)
    W.create_step(d, title="x")
    lock = d.parent / W.LOCK_NAME
    assert lock.is_file(), "锁文件应该在项目目录下"
    assert not (d / W.LOCK_NAME).exists(), "不能放进 steps/，那是步骤的地盘"
    assert W.LOCK_NAME.startswith("."), "点开头才会被 scan/list_files/signature 跳过"

    f = core.compile_forest(d)
    assert [s["id"] for s in f["steps"]] == ["001"]
    assert f["steps"][0]["files"] == []
    assert sig_before != core.signature(d)   # 变的是那一步，不是锁


def test_the_lock_is_reentrant_within_one_thread(proj):
    """append_body → update_step 是嵌套调用。非重入锁会在这里把自己锁死。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x", body="## 结果\n第一段")
    W.append_body(d, s.id, "第二段")
    assert "第二段" in W.load(d)[s.id].body


def test_the_lock_survives_an_exception(proj):
    """异常路径不释放锁的话，同进程里下一次写入会一直等到超时。"""
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    with pytest.raises(W.WriteError):
        W.update_step(d, s.id, {"body": LONE_SURROGATE})
    t0 = time.monotonic()
    W.update_step(d, s.id, {"status": "done"})
    assert time.monotonic() - t0 < W.LOCK_TIMEOUT / 2, "锁没被释放"
    assert W.load(d)[s.id].status == "done"
