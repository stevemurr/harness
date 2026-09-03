"""The eval runner, tested: what a check says when it fails, what a rung must satisfy
before it is trusted, and the one honest way two sweeps meet.

A grader that cannot say why a rung failed is the same class of bug as a reader that cannot
tell "nothing happened" from "something went wrong", and this ladder has produced three of
those. A comparison that pairs a best case against an outlier produced the retraction in
`evals/FINDINGS.md`.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import replace
from pathlib import Path

import pytest
from evals.record import Attempt, Sweep
from evals.report import compare, table
from evals.rungs import Rung, discover, unsolved
from evals.verify import verify

needs_bash = pytest.mark.skipif(not Path("/bin/bash").exists(), reason="needs bash")


def _rung(tmp_path: Path, body: str, name: str = "rung") -> Rung:
    folder = tmp_path / name
    folder.mkdir()
    _ = (folder / "verify.sh").write_text(body)
    _ = (folder / "task.md").write_text("do the thing\n")
    _ = (folder / "rung.json").write_text('{"tests": "a test rung"}\n')
    (tmp_path / "work").mkdir(exist_ok=True)
    return Rung.at(folder)


# -- verify ----------------------------------------------------------------------------


@needs_bash
def test_heredoc_failure_reports_the_assertion(tmp_path: Path) -> None:
    """The reason, not the heredoc's own terminator.

    `$BASH_COMMAND` holds a heredoc whole -- opener, body and closing `EOF`. Echoed whole,
    its trailing lines look like things the script said, and the last one is always `EOF`,
    so every heredoc failure used to report `python3 - <<'EOF'  ||  EOF` and discard the
    error. Remove the `head -1` from the trap in `verify` and this goes red.
    """
    rung = _rung(
        tmp_path,
        "#!/bin/sh\nset -eu\npython3 - <<'EOF'\n"
        + 'assert 1 == 2, "the stated reason"\n'
        + "EOF\n",
    )
    verdict = verify(rung.script, tmp_path / "work")
    assert not verdict.passed
    assert "the stated reason" in verdict.why
    assert not verdict.why.endswith("EOF")


@needs_bash
def test_silent_check_still_names_the_command(tmp_path: Path) -> None:
    """The case the trap was built for: `test` fails and prints nothing at all."""
    rung = _rung(tmp_path, '#!/bin/sh\nset -eu\ntest "1" = "2"\n')
    verdict = verify(rung.script, tmp_path / "work")
    assert not verdict.passed
    assert 'test "1" = "2"' in verdict.why


@needs_bash
def test_a_score_line_is_partial_credit(tmp_path: Path) -> None:
    rung = _rung(tmp_path, '#!/bin/sh\nset -eu\necho "SCORE 35 45"\ntest "1" = "2"\n')
    verdict = verify(rung.script, tmp_path / "work")
    assert not verdict.passed
    assert verdict.score == "[35/45]"
    assert verdict.why.startswith("[35/45] ")


@needs_bash
def test_a_check_that_overruns_takes_its_children_with_it(tmp_path: Path) -> None:
    """A timed-out check must not leave the thing it started running.

    Found live on 2026-09-01: `07-service/code.3` timed out, its server survived with
    `PPID=1`, and the next attempt failed with `Address already in use` -- a red row that
    measured nothing about the model.
    """
    rung = _rung(tmp_path, "#!/bin/sh\nset -eu\nsleep 30 &\necho $! > child.pid\nwait\n")
    work = tmp_path / "work"

    verdict = verify(rung.script, work, timeout=1)

    assert not verdict.passed
    assert "timed out" in verdict.why
    child = int((work / "child.pid").read_text().strip())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(child, signal.SIGKILL)
    raise AssertionError("the check's child outlived the timeout that killed its shell")


# -- rungs -----------------------------------------------------------------------------


@needs_bash
def test_a_rung_whose_checks_pass_on_the_unsolved_seed_is_refused(tmp_path: Path) -> None:
    """The promise the old runner's docstring made and no code kept."""
    green = _rung(tmp_path, "#!/bin/sh\nexit 0\n", name="green")
    red = _rung(tmp_path, "#!/bin/sh\ntest -f done.txt\n", name="red")

    assert unsolved(green, tmp_path / "stage") is not None
    assert unsolved(red, tmp_path / "stage") is None


@needs_bash
def test_a_rung_naming_a_file_its_seed_lacks_is_refused(tmp_path: Path) -> None:
    """Six red rows on 2026-09-02, every one a `No such file` from a check written
    against a layout that had moved. The seed check catches it before the model runs."""
    stale = _rung(tmp_path, "#!/bin/sh\ngrep -q x harness/server.py\n", name="stale")

    reason = unsolved(stale, tmp_path / "stage")

    assert reason is not None and "harness/server.py" in reason


def test_every_shipped_rung_has_what_a_rung_needs() -> None:
    for suite in ("ladder", "long"):
        rungs = discover(suite)
        assert rungs, suite
        for rung in rungs:
            assert rung.tests, f"{rung.name} does not say what it tests"
            assert rung.script.exists(), rung.name
            assert rung.task.strip(), rung.name
            assert rung.long == (suite == "long"), rung.name
            # A rung whose checks compile something says how long that may take; the
            # default is sized for a Python rung and would time out a Swift build.
            if (rung.path / "seed" / "Package.swift").exists():
                assert rung.verify_timeout > 120, rung.name


# -- record and report -----------------------------------------------------------------


def _attempt(rung: str, arm: str, number: int, *, passed: bool, turns: int) -> Attempt:
    return Attempt(
        rung=rung, tests="t", arm=arm, attempt=number, passed=passed, score="", why="",
        stop="done", detail="", turns=turns, seconds=float(turns), calls=turns,
        tools={"run": turns, "find_definition": 1}, failed={}, refused={}, compactions=0,
        context_peak_chars=1000 * turns, context_peak_tokens=0, context_total_chars=0,
        model_seconds=0.0, model_calls=turns, verified_last=True, mutations=1,
        recovered=0, unrecovered=0,
    )


def _sweep(label: str, *rows: Attempt, prompt: str = "abc") -> Sweep:
    return Sweep(
        label=label, started="2026-09-02T00:00:00+00:00", commit="deadbee", prompt=prompt,
        model="m", base_url="http://x", temperature=0.7, top_p=0.8, presence_penalty=1.5,
        max_turns=30, suite="ladder", withheld=(), repeat=2, attempts=list(rows),
    )


def test_a_sweep_survives_the_round_trip_through_disk(tmp_path: Path) -> None:
    before = _sweep("a", _attempt("01", "all", 1, passed=True, turns=5))
    before.write(tmp_path / "sweep.json")
    assert Sweep.read(tmp_path / "sweep.json") == before


def test_compare_refuses_to_pair_groups_of_unequal_size() -> None:
    a = _sweep(
        "a",
        _attempt("01", "all", 1, passed=True, turns=5),
        _attempt("01", "all", 2, passed=True, turns=7),
        _attempt("02", "all", 1, passed=True, turns=9),
    )
    b = _sweep(
        "b",
        _attempt("01", "all", 1, passed=False, turns=40),
        _attempt("02", "all", 1, passed=True, turns=9),
    )
    out = compare(a, b)
    assert "refused to pair" in out and "01/all (n=2 vs n=1)" in out
    assert "\n02" in out  # the honest pairing is still shown


def test_compare_says_when_two_sweeps_are_a_different_experiment() -> None:
    a = _sweep("a", _attempt("01", "all", 1, passed=True, turns=5))
    b = replace(_sweep("b", _attempt("01", "all", 1, passed=True, turns=5)), prompt="xyz")
    assert "NOT THE SAME EXPERIMENT" in compare(a, b)
    assert "prompt" in compare(a, b)
    assert "NOT THE SAME" not in compare(a, a)


def test_the_table_reads_from_the_record_not_the_file() -> None:
    sweep = _sweep(
        "a",
        _attempt("01", "all", 1, passed=True, turns=5),
        _attempt("01", "all", 2, passed=False, turns=20),
    )
    out = table(sweep)
    assert "01" in out and "1/2" in out
    assert "widest spread: 01/all (5-20 turns)" in out


# -- withholding -------------------------------------------------------------------------------


def test_every_tool_unless_withheld_by_name(tmp_path: Path) -> None:
    """A rung that allows agents gets `delegate` and the board; a control withholds tools
    by name and nothing else changes. On any other rung nobody gets the agent tools."""
    from evals.run import Recording, assemble

    from harness.providers.openai import OpenAICompatible
    from harness.settings import Settings
    from harness.state.approval import Approvals

    provider = OpenAICompatible(base_url="http://x/v1", model="m")
    model = Recording(provider)
    (tmp_path / "work").mkdir()
    agents = _rung(tmp_path, "#!/bin/sh\nexit 1\n", name="agents")
    _ = (agents.path / "rung.json").write_text('{"tests": "t", "agents": true}\n')
    agents = type(agents).at(agents.path)
    plain = _rung(tmp_path, "#!/bin/sh\nexit 1\n", name="plain")

    def names(rung, withheld: frozenset[str] = frozenset()) -> set[str]:
        made = assemble(
            rung,
            tmp_path / "work",
            withheld=withheld,
            settings=Settings(),
            model=model,
            threads=tmp_path / "threads",
            approvals=Approvals(),
            observers=[],
        )
        return {t.spec.name for t in made.tools}

    assert agents.agents and not plain.agents
    assert {"delegate", "post_task", "find_definition"} <= names(agents)
    control = names(agents, frozenset({"find_definition", "find_references"}))
    assert "find_definition" not in control and {"delegate", "read_file"} <= control
    assert "delegate" not in names(plain) and "read_file" in names(plain)
