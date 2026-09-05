"""Contract between the Recorder and the Claude Code CLI that is actually installed.

The Recorder unit tests mock `subprocess.run`, so a flag the CLI does not know
(`--exclude-dynamic-system-prompt-sections` on 2.1.30 made every call exit 1) or a
missing subcommand (`claude auth status`, absent on 2.1.30) is invisible to them
and only shows up as a 300-second retry loop in production.

This file asks the real binary for `--help` and `auth status` only.  It never sends
a model request and never spends subscription quota.  It is skipped where the CLI
is not installed.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from research_trace import recorder as R

CLAUDE = shutil.which("claude")
pytestmark = pytest.mark.skipif(CLAUDE is None, reason="claude CLI is not installed")


def _help_text() -> str:
    result = subprocess.run(
        [CLAUDE, "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


def test_every_flag_the_recorder_passes_is_documented_by_the_installed_cli():
    text = _help_text()
    flags = R.command_flags(R.build_command(CLAUDE, "sonnet"))
    assert flags, "the command carries no flags?"
    missing = [flag for flag in flags if flag not in text]
    assert not missing, f"installed claude ({CLAUDE}) does not document: {missing}"


def test_the_auth_preflight_copes_with_whatever_the_installed_cli_answers(tmp_path):
    try:
        env = R.subscription_environment()
    except R.RecorderError as error:
        pytest.skip(f"paid/API credentials in this shell: {error}")
    try:
        value = R.verify_subscription_auth(CLAUDE, env, tmp_path)
    except R.RecorderError as error:
        if error.kind == "auth":
            pytest.skip(f"no subscription login on this machine: {error}")
        raise
    assert value["auth_method"] == "unverified" or value["logged_in"] is True
