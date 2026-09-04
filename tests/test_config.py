"""Settings that survive between runs.

Both front ends read one file with one precedence rule. The rule matters more than the file:
the CLI grew `--extra-body` and the server did not, so the same deployment worked one way and
silently produced nothing the other -- an empty answer, which is the hardest kind of bug to
attribute. (2026-08-31)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_PORT,
    ConfigError,
    load,
    settle,
    write_example,
)


def written(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body)
    path.chmod(0o600)
    return path


def test_no_file_is_not_an_error(tmp_path: Path) -> None:
    """The normal case for anyone who has not configured anything."""
    config = load(tmp_path / "absent.toml")

    assert config.provider.base_url == DEFAULT_BASE_URL
    assert config.provider.model == DEFAULT_MODEL
    assert config.server.port == DEFAULT_PORT
    assert config.path is None


def test_a_file_supplies_the_provider(tmp_path: Path) -> None:
    path = written(
        tmp_path,
        '[provider]\nbase_url = "http://gw:4000/v1"\nmodel = "qwen3.6"\napi_key = "sk-x"\n',
    )

    config = load(path)

    assert config.provider.base_url == "http://gw:4000/v1"
    assert config.provider.model == "qwen3.6"
    assert config.provider.api_key == "sk-x"
    assert config.path == path


def test_extra_body_is_a_nested_table(tmp_path: Path) -> None:
    """The setting a Qwen3 behind LiteLLM needs, expressed as TOML rather than as JSON in a
    string."""
    path = written(
        tmp_path,
        "[provider]\n[provider.extra_body.chat_template_kwargs]\nenable_thinking = false\n",
    )

    config = load(path)

    assert config.provider.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_the_approval_table_names_a_policy_and_standing_rules(tmp_path: Path) -> None:
    path = written(
        tmp_path, '[approval]\npolicy = "edits"\nalways_allow = ["run:git", "run:uv"]\n'
    )

    config = load(path)

    assert config.settings.approval.policy == "edits"
    assert config.settings.approval.always_allow == ("run:git", "run:uv")
    assert load(written(tmp_path, "")).settings.approval.policy == "ask"


def test_a_policy_nobody_defined_is_an_error_not_a_quiet_ask(tmp_path: Path) -> None:
    path = written(tmp_path, '[approval]\npolicy = "yolo"\n')

    with pytest.raises(ConfigError, match="ask, edits, full-access"):
        _ = load(path)


def test_a_typo_is_an_error_rather_than_a_setting_that_does_nothing(tmp_path: Path) -> None:
    """A silently ignored `base_ur1` is a person reading a correct-looking file and wondering
    why the default is in force."""
    path = written(tmp_path, '[provider]\nbase_ur1 = "http://gw:4000/v1"\n')

    with pytest.raises(ConfigError, match="unknown key"):
        load(path)


def test_an_unknown_section_is_an_error(tmp_path: Path) -> None:
    path = written(tmp_path, '[providers]\nmodel = "x"\n')

    with pytest.raises(ConfigError, match="unknown section"):
        load(path)


def test_a_file_that_will_not_parse_is_an_error_not_a_silent_default(tmp_path: Path) -> None:
    """Falling back to defaults would leave someone looking at settings not in force."""
    path = written(tmp_path, "[provider\nmodel =\n")

    with pytest.raises(ConfigError):
        load(path)


def test_a_key_readable_by_others_is_refused(tmp_path: Path) -> None:
    """A secret in a file is a real trade; a secret in a file anyone can read is not."""
    path = written(tmp_path, '[provider]\napi_key = "sk-secret"\n')
    path.chmod(0o644)

    with pytest.raises(ConfigError, match="chmod 600"):
        load(path)


def test_permissions_only_matter_when_there_is_a_secret(tmp_path: Path) -> None:
    path = written(tmp_path, '[provider]\nmodel = "qwen3.6"\n')
    path.chmod(0o644)

    assert load(path).provider.model == "qwen3.6"


def test_a_flag_beats_an_env_var_beats_the_file_beats_the_default() -> None:
    """Written once rather than at each call site: five settings resolved the same way in two
    front ends is ten chances to get the order subtly different."""
    assert settle("flag", "env", "file", "default") == "flag"
    assert settle("", "env", "file", "default") == "env"
    assert settle("", "", "file", "default") == "file"
    assert settle("", "", "", "default") == "default"


def test_the_example_is_written_private_and_never_overwrites(tmp_path: Path) -> None:
    """A command that silently replaces a file holding an API key is one people run once by
    accident."""
    path = write_example(tmp_path / "config.toml")

    assert path.stat().st_mode & 0o077 == 0
    assert load(path).provider.model == DEFAULT_MODEL

    with pytest.raises(ConfigError, match="already exists"):
        write_example(path)


def test_the_web_table_sets_who_the_tools_say_they_are(tmp_path: Path) -> None:
    from harness.config import load

    path = tmp_path / "config.toml"
    _ = path.write_text(
        '[web]\nuser_agent = "Mozilla/5.0 test"\nblock_private = false\nmax_chars = 5000\n'
    )
    web = load(path).settings.web
    assert web.user_agent == "Mozilla/5.0 test"
    assert web.block_private is False and web.max_chars == 5000
    assert web.render is True  # untouched keys keep their defaults
    assert web.webkit == ""

    _ = path.write_text("[web]\nuser_agnet = 'x'\n")
    with pytest.raises(ConfigError, match="user_agnet"):
        _ = load(path)
