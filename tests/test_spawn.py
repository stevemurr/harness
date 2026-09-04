"""Starting a child, and stopping all of it.

`exec/spawn.py` is the one place that knows a process has descendants. These are the cases
that cost something to learn: a group kill that reached the harness's own group and killed
the test runner, and a scoped command that outlived the block that started it.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from harness.exec.spawn import Child, Stopping, own_group, scoped


def test_the_harness_never_signals_its_own_process_group() -> None:
    """The guard that stands between "kill the group" and killing the harness.

    A child spawned without `start_new_session` shares our group, so `os.getpgid(child)`
    returns *our* group and `killpg` on it is suicide. That is not hypothetical: the first
    version of the group kill missed the `watch` spawn, and the whole test run died with
    SIGKILL and no output -- no failure, no summary, nothing to read.
    """
    assert own_group(os.getpid()) is True
    assert own_group(2**31 - 1) is None, "an absent pid must be unknown, never False"


async def test_a_scoped_command_cannot_outlive_its_block(tmp_path: Path) -> None:
    """The guarantee the context manager exists for.

    A child left running after its block is the `&` failure the harness refuses commands
    for, arrived by another route -- and it is the shape both of 2026-09-01's bugs took.
    """
    marker = tmp_path / "outlived"
    async with scoped(f"sleep 2; touch {marker}", cwd=str(tmp_path)) as child:
        pid = child.pid
        assert child.running

    await asyncio.sleep(2.5)
    assert not marker.exists(), "the command outlived the block that scoped it"
    assert own_group(pid) is None, "its group should be gone"


async def test_a_scoped_command_dies_with_an_exception(tmp_path: Path) -> None:
    """Every way out, not just the tidy one."""
    marker = tmp_path / "outlived"
    with pytest.raises(RuntimeError):
        async with scoped(f"sleep 2; touch {marker}", cwd=str(tmp_path)):
            raise RuntimeError("something went wrong in the block")

    await asyncio.sleep(2.5)
    assert not marker.exists()


async def test_stopping_is_carried_by_the_child_not_remembered_by_the_caller() -> None:
    """The policy is chosen once, when the child is made, and travels with it."""
    handle = await asyncio.create_subprocess_shell("sleep 30", start_new_session=True)
    child = Child(handle, Stopping(reap=1.0))

    assert child.stopping.reap == 1.0
    await child.stop()

    assert not child.running


async def test_stopping_reaches_a_child_that_left_the_group(tmp_path: Path) -> None:
    """`swift test` runs `xctest` in a group of its own, so a group kill left it running
    with its parent gone -- twenty-odd of them, over a day. Stop kills the tree."""
    marker = tmp_path / "grandchild.pid"
    command = (
        'python3 -c "import os, subprocess, time; '
        + "p = subprocess.Popen(['sleep', '60'], start_new_session=True); "
        + f"open('{marker}', 'w').write(str(p.pid)); time.sleep(60)\""
    )
    async with scoped(command) as child:
        for _ in range(100):
            if marker.exists() and marker.read_text().strip():
                break
            await asyncio.sleep(0.05)
        grandchild = int(marker.read_text())
        assert own_group(grandchild) is False
        await child.stop()

    await asyncio.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild, 0)


async def test_a_line_longer_than_the_stream_limit_does_not_wedge_the_reader() -> None:
    """asyncio's 64 KiB default raised `ValueError` on one long line, the reader gave up,
    and the child sat blocked on a full pipe for as long as anyone waited. (2026-09-04)"""
    from harness.exec import spawn as module

    original = module.LINE_LIMIT
    module.LINE_LIMIT = 16 * 1024  # small enough that the test's lines pass it
    command = (
        'python3 -c "import sys; sys.stdout.write(\'a\' * 70_000 + chr(10)); '
        + "[sys.stdout.write('b' * 999 + chr(10)) for _ in range(400)]; "
        + 'sys.stdout.write(\'end\' + chr(10))"'
    )
    try:
        async with scoped(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        ) as child:
            seen: list[bytes] = []
            async with asyncio.timeout(20):
                async for line in child.read_lines():
                    seen.append(line)
                code = await child.wait()
    finally:
        module.LINE_LIMIT = original

    assert code == 0
    assert seen[-1] == b"end\n"
    assert sum(len(line) for line in seen) == 70_001 + 400 * 1_000 + 4
    assert sum(1 for line in seen if line.startswith(b"b" * 999)) == 400
