"""trace_git — debounce 自动 commit + push。

服务器上的 steps/ 是唯一工作副本，这里负责把它持续推到远端私有仓库。
这条链路是 G4（删掉所有程序，剩下的文件仍然可读）在"上了服务器"之后的兑现方式：
你在本地 git pull 拿到的就是完整、可 grep、可 diff 的文件树。

失败绝不阻塞 API：git 出问题只记 warning，写入照常成功。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

TIMEOUT = 120


def _run(root: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, "找不到 git 可执行文件"
    except subprocess.TimeoutExpired:
        return 124, f"git {args[0]} 超时"


class GitSync:
    def __init__(
        self,
        root: Path,
        *,
        enabled: bool = True,
        remote: str = "origin",
        branch: str = "main",
        debounce: float = 45.0,
    ) -> None:
        self.root = Path(root)
        self.enabled = enabled and (self.root / ".git").exists()
        self.remote = remote
        self.branch = branch
        self.debounce = debounce
        self.last: dict[str, Any] = {"state": "idle", "detail": "", "pushed": False}
        self._pending: set[str] = set()
        self._task: asyncio.Task | None = None

    def touch(self, ids: list[str] | str) -> None:
        if not self.enabled:
            return
        if isinstance(ids, str):
            ids = [ids]
        self._pending.update(ids)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._later())

    async def _later(self) -> None:
        await asyncio.sleep(self.debounce)
        ids = sorted(self._pending)
        self._pending.clear()
        await asyncio.to_thread(self.commit_now, ids)

    def commit_now(self, ids: list[str] | None = None) -> dict[str, Any]:
        if not self.enabled:
            self.last = {"state": "disabled", "detail": "未启用或不是 git 仓库", "pushed": False}
            return self.last

        code, out = _run(self.root, "status", "--porcelain")
        if code != 0:
            self.last = {"state": "error", "detail": out, "pushed": False}
            return self.last
        if not out.strip():
            self.last = {"state": "clean", "detail": "无变更", "pushed": False}
            return self.last

        msg = "steps: " + (", ".join(ids) if ids else "manual sync")
        if len(msg) > 120:
            msg = msg[:117] + "..."

        for args in (("add", "-A"), ("commit", "-m", msg)):
            code, out = _run(self.root, *args)
            if code != 0:
                self.last = {"state": "error", "detail": f"git {args[0]}: {out}", "pushed": False}
                return self.last

        code, out = _run(self.root, "push", self.remote, f"HEAD:{self.branch}")
        if code != 0:
            # 已经 commit 成功了，本地历史是安全的；push 失败下次自然重试。
            self.last = {"state": "committed", "detail": f"push 失败: {out}", "pushed": False}
            return self.last

        self.last = {"state": "pushed", "detail": msg, "pushed": True}
        return self.last
