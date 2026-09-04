"""The `harness` command: one entry point, subcommands, and what each refuses."""

from __future__ import annotations

import sys
from collections.abc import Coroutine
from pathlib import Path

import pytest

from harness.cli import main, parser
from harness.cli.resolve import CliError, require_key, resolve
from harness.config import Config, Provider


def test_every_subcommand_parses_and_names_its_handler() -> None:
    made = parser()
    for argv in (
        ["run", "do it"],
        ["run", "do it", "-C", "/tmp", "--plan", "-y", "--resume", "thr_1", "--model", "m"],
        ["serve", "--port", "9"],
        ["acp", "--model", "m"],
        ["threads"],
        ["init"],
        ["init-agents", "-C", "/tmp"],
        ["init-skill", "deploy", "-C", "/tmp"],
        ["install-servers"],
        ["install-webkit", "--from", "/nowhere/at/all"],
        ["evals", "run", "--label", "x", "--both"],
        ["evals", "report", "a.json", "b.json"],
    ):
        args = made.parse_args(argv)
        assert callable(args.handler), argv


def test_run_needs_a_prompt_and_a_bare_prompt_is_not_a_command() -> None:
    """`harness "serve"` would be ambiguous, so the prompt lives under `run`."""
    with pytest.raises(SystemExit):
        _ = parser().parse_args(["run"])
    with pytest.raises(SystemExit):
        _ = parser().parse_args(["fix the parser"])


def test_init_writes_the_starter_config_where_it_is_told(tmp_path: Path) -> None:
    target = tmp_path / "cfg" / "config.toml"

    assert main(["init", "--config", str(target)]) == 0
    assert target.exists()
    assert main(["init", "--config", str(target)]) == 2  # refuses to overwrite, in one line


def test_init_agents_writes_a_conventions_file_once(tmp_path: Path) -> None:
    assert main(["init-agents", "-C", str(tmp_path)]) == 0
    assert (tmp_path / "AGENTS.md").exists()
    assert main(["init-agents", "-C", str(tmp_path)]) == 0  # leaves it alone


def test_init_skill_writes_a_starter_under_the_folder(tmp_path: Path) -> None:
    assert main(["init-skill", "deploy", "-C", str(tmp_path)]) == 0
    assert (tmp_path / ".harness" / "skills" / "deploy" / "SKILL.md").exists()
    assert main(["init-skill", "deploy", "-C", str(tmp_path)]) == 0  # leaves it alone


def test_a_key_is_required_for_a_remote_endpoint_and_not_a_local_one() -> None:
    remote = Config(provider=Provider(base_url="https://api.example.com/v1", api_key=""))
    local = Config(provider=Provider(base_url="http://localhost:4000/v1", api_key=""))
    with pytest.raises(CliError, match="no API key"):
        require_key(remote)
    require_key(local)


def test_resolve_takes_the_flag_over_the_environment_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    _ = config.write_text('[provider]\nmodel = "from-file"\nbase_url = "http://localhost:1/v1"\n')
    monkeypatch.setenv("HARNESS_MODEL", "from-env")
    made = parser()

    flagged = resolve(made.parse_args(["run", "x", "--config", str(config), "--model", "m"]))
    from_env = resolve(made.parse_args(["run", "x", "--config", str(config)]))
    monkeypatch.delenv("HARNESS_MODEL")
    from_file = resolve(made.parse_args(["serve", "--config", str(config)]))

    assert flagged.provider.model == "m"
    assert from_env.provider.model == "from-env"
    assert from_file.provider.model == "from-file"
    assert from_file.provider.base_url == "http://localhost:1/v1"


def test_a_bad_extra_body_is_one_line_and_exit_two(tmp_path: Path) -> None:
    assert main(["serve", "--extra-body", "{not json", "--config", str(tmp_path / "none")]) == 2


def _local(tmp_path: Path) -> str:
    """A config that needs no key, so `run` gets as far as its own checks."""
    config = tmp_path / "config.toml"
    _ = config.write_text('[provider]\nbase_url = "http://localhost:1/v1"\n')
    return str(config)


def test_a_folder_that_is_not_there_is_one_line_and_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["run", "x", "-C", "/nonexistent/at/all", "--config", _local(tmp_path)])

    assert code == 2
    err = capsys.readouterr().err
    assert "not a folder" in err
    assert "Traceback" not in err


def test_ctrl_c_is_one_line_and_exit_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """On 3.13 `asyncio.run` raises the interrupt itself, after cancelling the task."""
    from harness.cli import run

    def interrupted(coro: Coroutine[object, object, int]) -> int:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(run.asyncio, "run", interrupted)

    code = main(["run", "x", "-C", str(tmp_path), "--config", _local(tmp_path)])

    assert code == 130
    out = capsys.readouterr()
    assert "interrupted." in out.out
    assert "Traceback" not in out.err


def test_evals_says_where_it_lives_when_the_package_is_not_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in [n for n in sys.modules if n == "evals" or n.startswith("evals.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "evals", None)  # what an installed wheel would see

    assert main(["evals", "run", "--label", "x"]) == 2


# -- streaming on a terminal ------------------------------------------------------------


def test_the_narrator_prints_words_as_they_come_and_not_again_at_the_turn(capsys) -> None:
    from harness.agent.loop import Turn
    from harness.cli.terminal import Narrator
    from harness.providers.base import Chunk
    from harness.types import Message, Role

    narrator = Narrator()
    narrator.listen(Chunk("thinking hard", thought=True))
    narrator.listen(Chunk("Hello"))
    narrator.listen(Chunk(", world"))
    narrator.render(Turn(Message(Role.ASSISTANT, "Hello, world")))

    out = capsys.readouterr().out
    assert out.count("Hello, world") == 1
    assert "thinking hard" not in out


def test_the_narrator_prints_the_prose_whole_when_nothing_was_streamed(capsys) -> None:
    from harness.agent.loop import Turn
    from harness.cli.terminal import Narrator
    from harness.types import Message, Role

    Narrator().render(Turn(Message(Role.ASSISTANT, "Hello, world")))

    assert "Hello, world" in capsys.readouterr().out
