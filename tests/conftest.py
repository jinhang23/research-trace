import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _one_batch_per_turn(monkeypatch):
    """a39 起语义批次按材料量切；绝大多数 hook 测试假设「一轮一批」，这里统一恢复旧行为，
    需要验证按量封批的测试自己覆盖这个环境变量。"""
    monkeypatch.setenv("TRACE_BATCH_MIN_CHARS", "0")
