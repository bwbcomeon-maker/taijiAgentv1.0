from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import agent.lsp.client as client_module
from agent.lsp.client import LSPClient


class _GracefullyExitedProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.wait_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0

    async def wait(self) -> int:
        self.wait_calls += 1
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1


class _ProcessThatNeedsTerminate(_GracefullyExitedProcess):
    def __init__(self) -> None:
        super().__init__()
        self.terminated = False

    async def wait(self) -> int:
        self.wait_calls += 1
        if not self.terminated:
            await asyncio.Future()
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.terminated = True


class _ProcessThatNeedsKill(_GracefullyExitedProcess):
    def __init__(self) -> None:
        super().__init__()
        self.killed = False

    async def wait(self) -> int:
        self.wait_calls += 1
        if not self.killed:
            await asyncio.Future()
        self.returncode = -9
        return -9

    def kill(self) -> None:
        self.kill_calls += 1
        self.killed = True


@pytest.mark.asyncio
async def test_cleanup_waits_for_graceful_exit_before_signalling(tmp_path: Path):
    client = LSPClient(
        server_id="graceful",
        workspace_root=str(tmp_path),
        command=["unused"],
    )
    proc = _GracefullyExitedProcess()
    client._proc = proc

    await client._cleanup_process()

    assert proc.wait_calls == 1
    assert proc.terminate_calls == 0
    assert proc.kill_calls == 0


@pytest.mark.asyncio
async def test_cleanup_terminates_only_after_grace_period(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(client_module, "SHUTDOWN_GRACE", 0.001)
    client = LSPClient(
        server_id="needs-terminate",
        workspace_root=str(tmp_path),
        command=["unused"],
    )
    proc = _ProcessThatNeedsTerminate()
    client._proc = proc

    await client._cleanup_process()

    assert proc.wait_calls == 2
    assert proc.terminate_calls == 1
    assert proc.kill_calls == 0


@pytest.mark.asyncio
async def test_cleanup_kills_only_after_terminate_grace_period(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(client_module, "SHUTDOWN_GRACE", 0.001)
    client = LSPClient(
        server_id="needs-kill",
        workspace_root=str(tmp_path),
        command=["unused"],
    )
    proc = _ProcessThatNeedsKill()
    client._proc = proc

    await client._cleanup_process()

    assert proc.wait_calls == 3
    assert proc.terminate_calls == 1
    assert proc.kill_calls == 1
