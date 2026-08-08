"""trace_git — debounce 自动 commit + push。

服务器上的 steps/ 是唯一工作副本，这里负责把它持续推到远端私有仓库。
这条链路是 G4（删掉所有程序，剩下的文件仍然可读）在"上了服务器"之后的兑现方式：
你在本地 git pull 拿到的就是完整、可 grep、可 diff 的文件树。

失败绝不阻塞 API：git 出问题只记 warning，写入照常成功。**但绝不安静地失败**——
deploy/README 把「数据仓 git push」当成换机器和灾难恢复的全部依据，一次没人看见的
push 失败，等价于用户以为自己有备份而其实没有。所以每一次失败都做三件事：
写服务端日志、留在 status() 里（GET /api/git 可查）、并给出一条**照着做就能修**的提示。
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

import trace_write as W  # 只为了 LOCK_NAME —— 锁文件名只该有一个真相源

TIMEOUT = 120

log = logging.getLogger("trace.git")

# 「这一次同步是成功的」= 该推的都推走了，或者本来就没东西要推。
# committed（commit 成功但 push 失败）**不算**成功：文件还留在这一台机器上，
# 而这条链路存在的意义就是"不只在这一台机器上"。
OK_STATES = frozenset({"pushed", "clean"})


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


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 失败分类

HINT_IDENTITY = (
    "在数据仓目录里跑：git config user.email \"你@example.com\" 和 "
    "git config user.name \"你的名字\"。服务通常以专用系统用户运行，"
    "它的 ~/.gitconfig 是空的——所以这一条在**第一次 push 时必炸**，"
    "而且此前的写入全部只躺在这台机器上。"
)
HINT_REMOTE = (
    "数据仓里没有这个 remote。跑 git remote -v 看看实际叫什么名字，"
    "然后改 config.json 的 git.remote，或者 git remote add <名字> <地址>。"
)
HINT_AUTH = (
    "远端拒绝了这台机器的身份。检查三样：deploy key 有没有勾**写权限**、"
    "服务用户的 ~/.ssh/ 下有没有对应私钥、以及 known_hosts 里有没有远端主机"
    "（ssh-keyscan github.com | sudo -u <服务用户> tee -a ~/.ssh/known_hosts）。"
)
HINT_HOSTKEY = (
    "远端主机不在 known_hosts 里，ssh 停在交互式确认上。以**服务用户**的身份跑："
    "ssh-keyscan <主机> | sudo -u <服务用户> tee -a ~/.ssh/known_hosts。"
)
HINT_HTTPS = (
    "远端是 https 地址，git 想弹出用户名/密码输入框，但服务里没有终端。"
    "改成 ssh 地址（git remote set-url <remote> git@…），或者配一个凭据助手。"
)
HINT_BRANCH = (
    "推的分支名和远端对不上。看 config.json 里的 git.branch 是不是写成了 main "
    "而数据仓其实在 master（或反过来）；空仓库要先有一次本地 commit 才能推。"
)
HINT_REJECTED = (
    "远端有这台机器上没有的提交，push 被拒。到数据仓目录手工处理一次"
    "（git pull --rebase 然后 git push），自动同步会在下一次写入时恢复。"
)
HINT_NOT_REPO = (
    "这个目录不是 git 仓库。要么在里面 git init 并加好 remote，"
    "要么把 config.json 的 git.enabled 改成 false——"
    "开着却推不出去，比明确关掉危险得多。"
)
HINT_NO_GIT = "这台机器上没有 git 可执行文件（或不在服务进程的 PATH 里）。装一个，或者关掉自动同步。"

# 顺序有意义：先匹配的赢。每条是（关键词, 一句人话摘要, 照着做就能修的提示）。
# 摘要**不含**任何路径和远端地址——它会出现在不需要令牌的 /api/git 里。
_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("author identity unknown", "please tell me who you are", "empty ident", "user.email"),
     "数据仓没有配 git 身份，commit 根本建不出来", HINT_IDENTITY),
    (("not a git repository", "not in a git directory"),
     "数据仓不是 git 仓库", HINT_NOT_REPO),
    (("找不到 git 可执行文件",),
     "机器上找不到 git", HINT_NO_GIT),
    (("host key verification failed", "no matching host key", "known_hosts"),
     "远端主机没通过 ssh 主机指纹校验", HINT_HOSTKEY),
    (("permission denied (publickey", "permission denied (public key", "access denied",
      "authentication failed", "could not read from remote repository"),
     "远端拒绝了这台机器的身份（deploy key / ssh 密钥）", HINT_AUTH),
    (("could not read username", "terminal prompts disabled", "no such device or address"),
     "远端要交互式输入用户名密码，服务里没有终端可用", HINT_HTTPS),
    # git 对「remote 名字不存在」和「remote 地址不是仓库」用的是同一句话
    # （fatal: '<名字>' does not appear to be a git repository），所以合成一条。
    (("does not appear to be a git repository", "no such remote", "remote not found"),
     "config.json 里的 remote 在数据仓里不存在，或者它指向的地址不是仓库", HINT_REMOTE),
    (("src refspec", "matches more than one", "does not match any"),
     "要推的分支在本地不存在", HINT_BRANCH),
    (("non-fast-forward", "rejected", "fetch first", "behind its remote"),
     "远端有本机没有的提交，push 被拒绝", HINT_REJECTED),
    (("超时",),
     "git 命令超时（远端不通或仓库过大）",
     "手工在数据仓里跑一次同名命令看看卡在哪；网络不通时自动同步会在下一次写入时重试。"),
)


def classify(out: str) -> tuple[str, str]:
    """把 git 的英文报错翻成（摘要, 提示）。认不出来就返回两个空串。"""
    low = (out or "").lower()
    for keys, summary, hint in _RULES:
        if any(k in low for k in keys):
            return summary, hint
    return "", ""


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
        # configured = 用户在 config.json 里要的；enabled = 实际能跑的。
        # 分开记是因为"要了但跑不起来"正是最危险的那一格：用户以为有备份。
        self.configured = bool(enabled)
        self.enabled = self.configured and (self.root / ".git").exists()
        self.remote = remote
        self.branch = branch
        self.debounce = debounce
        self.last_ok_at = ""
        self._pending: set[str] = set()
        self._task: asyncio.Task | None = None
        self._nagged = False
        self.last: dict[str, Any] = {}
        if not self.configured:
            self._record("disabled", summary="没开自动同步（config.json 里 git.enabled=false）")
        elif not self.enabled:
            self._record(
                "misconfigured",
                detail=f"{self.root} 下没有 .git",
                summary="配置里开了自动同步，但数据仓不是 git 仓库——同步一次都没跑过",
                hint=HINT_NOT_REPO,
            )
        else:
            self._record("idle", summary="还没有同步过（服务起来之后还没有写入）")

    # ---- 状态 --------------------------------------------------------

    def _record(self, state: str, *, detail: str = "", summary: str = "",
                hint: str = "", pushed: bool = False) -> dict[str, Any]:
        self.last = {
            "state": state,
            "detail": detail,          # git 原文，可能含路径/远端地址 → 只给带令牌的调用方
            "summary": summary or state,
            "hint": hint,
            "pushed": pushed,
            "at": _now(),
        }
        if state in OK_STATES:
            self.last_ok_at = self.last["at"]
        if state in ("error", "committed", "misconfigured"):
            # 日志是唯一一条"不需要有人主动去问"的出口。detail 留在服务端，不外发。
            log.warning("git 同步没成功：%s | git 原文：%s | 怎么修：%s",
                        self.last["summary"], detail.replace("\n", " ⏎ ")[:400], hint)
        elif state == "pushed":
            log.info("git 已推送：%s", detail)
        return self.last

    def status(self, *, detail: bool = False) -> dict[str, Any]:
        """给 /api/git 和 /api/status 用。

        detail=False 是**公开**视图：只有分类过的中文摘要和提示，不含 git 原文、
        不含数据仓路径、不含远端地址——读接口是不要令牌的，那些东西不能外发。
        detail=True 需要写入令牌，给 doctor / 管理员看，带 git 原样输出。
        """
        out: dict[str, Any] = {
            "state": self.last["state"],
            "ok": self.last["state"] in OK_STATES,
            "at": self.last["at"],
            "summary": self.last["summary"],
            "hint": self.last["hint"],
            "pushed": bool(self.last["pushed"]),
            "configured": self.configured,
            "enabled": self.enabled,
            "remote": self.remote,
            "branch": self.branch,
            "debounce": self.debounce,
            "pending": len(self._pending),
            "last_ok_at": self.last_ok_at,
        }
        if detail:
            out["detail"] = self.last["detail"]
            out["pending_ids"] = sorted(self._pending)
            out["root"] = str(self.root)
        return out

    def preflight(self) -> dict[str, Any]:
        """起服务时先查掉「第一次 push 必炸」的那两件事：没有身份、没有 remote。

        为什么不等到真出错再说：debounce 默认 45 秒，第一次写入之后失败发生在
        没人看着的时刻；而这两样都是**装机时漏掉一步**造成的静态错误，
        起服务的当下就能查出来，正好赶在 deploy/README 的验证清单那一步。
        """
        if not self.enabled:
            return self.last
        problems, hints = [], []
        for key in ("user.email", "user.name"):
            code, out = _run(self.root, "config", "--get", key)
            if code != 0 or not out.strip():
                problems.append(f"没有 {key}")
        if problems:
            hints.append(HINT_IDENTITY)
        code, out = _run(self.root, "remote", "get-url", self.remote)
        if code != 0 or not out.strip():
            problems.append(f"没有名为 {self.remote} 的 remote")
            hints.append(HINT_REMOTE)
        if not problems:
            return self.last
        return self._record(
            "misconfigured",
            detail="; ".join(problems),
            summary="数据仓还没配好，第一次 push 会失败（" + "；".join(problems) + "）",
            hint=" ".join(hints),
        )

    # ---- 触发 --------------------------------------------------------

    def touch(self, ids: list[str] | str) -> None:
        if not self.enabled:
            if self.configured and not self._nagged:
                # 只吼一次：每次写入都刷一行日志会把真正的错误淹掉。
                self._nagged = True
                log.warning("git 自动同步开着但跑不起来（%s），这次以及之后的写入都不会被推走",
                            self.last["summary"])
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
            # 关掉的原因在 __init__ 里就分好类了（disabled / misconfigured），
            # 这里再覆盖一遍只会把"开着却跑不起来"这条重要信息抹平成"未启用"。
            return self.last

        code, out = _run(self.root, "status", "--porcelain")
        if code != 0:
            summary, hint = classify(out)
            return self._record("error", detail=f"git status: {out}",
                                summary=summary or "git status 失败，数据仓状态读不出来", hint=hint)
        if not out.strip():
            return self._record("clean", detail="无变更", summary="没有要同步的改动")

        msg = "steps: " + (", ".join(ids) if ids else "manual sync")
        if len(msg) > 120:
            msg = msg[:117] + "..."

        # 锁文件排除写在这里，而不是只靠数据仓的 .gitignore：那份 .gitignore 在
        # **另一个仓库**里，要用户自己记得加一行。忘了的话每次同步都会 commit 一个
        # 运行期锁，两台机器一拉就冲突——而冲突的是一个空文件，毫无意义。
        # pathspec 是我们控制得了的，不依赖对面仓库配得对不对。
        for args in (("add", "-A", "--", ".", f":(exclude)*{W.LOCK_NAME}"),
                     ("commit", "-m", msg)):
            code, out = _run(self.root, *args)
            if code != 0:
                summary, hint = classify(out)
                return self._record("error", detail=f"git {args[0]}: {out}",
                                    summary=summary or f"git {args[0]} 失败", hint=hint)

        code, out = _run(self.root, "push", self.remote, f"HEAD:{self.branch}")
        if code != 0:
            # 已经 commit 成功了，本地历史是安全的；push 失败下次自然重试。
            # 但"本地安全"不等于"备份成功"——状态仍然是失败的那一类，见 OK_STATES。
            summary, hint = classify(out)
            return self._record(
                "committed", detail=f"push 失败: {out}",
                summary=(summary or "push 失败") + "；改动已在本机 commit，还没推到远端",
                hint=hint or HINT_AUTH)

        return self._record("pushed", detail=msg, summary=f"已推送到 {self.remote}/{self.branch}", pushed=True)
