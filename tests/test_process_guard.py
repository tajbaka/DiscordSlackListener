from __future__ import annotations

from pathlib import Path

from discord_slack_listener.app import _matches_daemon_process, _matches_listener_process
from discord_slack_listener.conf import ROOT_DIR


class FakeProcess:
    def __init__(self, cmdline: list[str], cwd: Path = ROOT_DIR):
        self._cmdline = cmdline
        self._cwd = cwd

    def cmdline(self) -> list[str]:
        return self._cmdline

    def cwd(self) -> str:
        return str(self._cwd)


def test_listener_process_matcher_is_repo_scoped() -> None:
    proc = FakeProcess(["python", "-m", "discord_slack_listener", "listen"])

    assert _matches_listener_process(proc) is True
    assert _matches_daemon_process(proc) is False


def test_daemon_process_matcher_is_repo_scoped() -> None:
    proc = FakeProcess(["python", "-m", "discord_slack_listener", "daemon"])

    assert _matches_daemon_process(proc) is True
    assert _matches_listener_process(proc) is False


def test_process_matcher_ignores_other_repos() -> None:
    proc = FakeProcess(
        ["python", "-m", "discord_slack_listener", "listen"],
        cwd=Path("/tmp/other"),
    )

    assert _matches_listener_process(proc) is False
