"""每条指令都要送到**执行得了它的那个读者**手上。

三条通道，读者各不相同：

* `INSTRUCTIONS`（mcp.py）—— 主 agent 无条件看得到，用于「不知道也得知道」的事；
* `skills/research-trace/SKILL.md` —— 主 agent 按需加载，用于任务触发的流程；
* `hooks/RECORDER_PROTOCOL.md` —— **只有 Recorder fork 会读**，而它被工具白名单锁着。

写给主 agent 的规则放进第三个文件，等于从没生效过。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = (ROOT / "hooks" / "RECORDER_PROTOCOL.md").read_text(encoding="utf-8")
SKILL = (ROOT / "skills" / "research-trace" / "SKILL.md").read_text(encoding="utf-8")
MCP = (ROOT / "research_trace" / "mcp.py").read_text(encoding="utf-8")


def instructions() -> str:
    body = re.search(r"INSTRUCTIONS = \((.*?)\n\)", MCP, re.S).group(1)
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', body))


def test_opt_in_capture_is_stated_where_the_main_agent_always_sees_it():
    """「采集根本没开」是这个系统最典型的静默失败：一切看起来正常，只是什么都没记。

    这件事不能只写在按需加载的 skill 里 —— agent 得先意识到有问题才会去加载它。
    """
    text = instructions()
    assert "opt-in" in text
    assert ".research-trace.json" in text
    assert "never write that marker" in text.lower()


def test_the_binding_flow_lives_in_the_skill_not_in_the_recorder_protocol():
    """Recorder 看不到发起绑定的那个请求，也没有权限去执行绑定。"""
    assert "trace-project bind --project-id" in SKILL
    assert "team_mapping_ambiguous" in SKILL
    # 协议文件可以指路，但不该复述一遍：两份副本一定会漂移，
    # 而这一份还送到了一个执行不了它的读者手上。
    assert "team_mapping_ambiguous" not in PROTOCOL
    assert "SKILL.md" in PROTOCOL, "至少要给 Recorder 留一个指路，别让人以为这块没人管"


def test_the_protocol_still_explains_why_a_batch_exists_at_all():
    """搬走「怎么绑定」不等于搬走「为什么这个目录会有批次」—— 后者是 Recorder 的背景知识。"""
    assert "Capture is opt-in per project" in PROTOCOL
    assert "Without a marker the hook exits" in PROTOCOL
