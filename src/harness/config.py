"""Settings that survive between runs, so a terminal is not four exports long.

`~/.harness/config.toml`, beside `threads/`. Both front ends read it -- the terminal CLI and
the HTTP server -- because a deployment that needs `chat_template_kwargs` needs it whichever
way the agent is driven, and the two disagreeing about the provider is a bug that only shows
up as an empty answer. That happened: the CLI grew `--extra-body` and the server did not, so
the same model worked one way and silently produced nothing the other.

## Precedence

A flag beats an environment variable beats this file beats the built-in default. That order
is the one people expect and the one that makes a config file safe to write: nothing here
can override something you typed.

## The key

`api_key` is a secret in a file, which is a real trade rather than an oversight. The
alternative is a keyring, and a keyring is what a *client* wants -- something a person logs
into. A server started by a supervisor at boot has nobody to prompt, so it reads a file or
an environment variable, and a file that only its owner can read is the better of the two:
an environment variable is visible in `ps` output on some systems and leaks into every child
process the agent spawns.

So the file is created 0600, and `load` says so plainly when it finds one that is not.
"""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.settings import Compaction, Limits, Output, Settings

DEFAULT_PATH = Path("~/.harness/config.toml")
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
#: The window the model behind this harness actually has. Worth writing down rather than
#: inheriting an OpenAI-shaped number: too large and compaction never fires before the
#: endpoint 400s, so the feature ships off; too small and it compacts runs that had room.
#: Neither error announces itself, which is why it is in the file `--init` writes.
DEFAULT_CONTEXT_WINDOW = 262_144

#: Every key this file may carry, so a typo is an error rather than a setting that does
#: nothing. A silently ignored `base_ur1` is a person reading a correct-looking file and
#: wondering why the default is in force.
_PROVIDER_KEYS = frozenset({
    "base_url", "model", "api_key", "extra_body", "context_window",
    "temperature", "top_p", "presence_penalty",
})
_SERVER_KEYS = frozenset({"host", "port", "token"})
_COMPACTION_KEYS = frozenset({"enabled", "at", "keep_turns"})
_OUTPUT_KEYS = frozenset({"per_result", "per_turn"})
_LIMITS_KEYS = frozenset({"max_turns", "max_consecutive_refusals"})
_TABLES = frozenset({"provider", "server", "compaction", "output", "limits"})


class ConfigError(Exception):
    """The config file exists and cannot be used. Never raised for an absent file."""


@dataclass(frozen=True, slots=True)
class Provider:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = field(default="", repr=False)
    #: Merged into every model request. See `providers/openai.py` for why this exists.
    extra_body: dict[str, Any] = field(default_factory=dict)
    #: How much context this model has. A property of the model, so it sits here and is
    #: handed to the provider rather than threaded to both front ends separately.
    context_window: int = DEFAULT_CONTEXT_WINDOW
    #: Sampling, per whatever the model's own card recommends. Here rather than in
    #: `settings.py` because these are facts about a model and not knobs of this harness:
    #: the right numbers change when the model does, and only a deployment knows which.
    temperature: float = 0.0
    top_p: float | None = None
    presence_penalty: float | None = None


@dataclass(frozen=True, slots=True)
class Server:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    #: When set, the server requires it as a bearer token. Empty means no auth at all.
    token: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class Config:
    provider: Provider = field(default_factory=Provider)
    server: Server = field(default_factory=Server)
    #: The runtime's own settings type, not a copy of it. There was a copy: `cli.py` and
    #: `server.py` each rebuilt `Compaction` field by field out of a config-local twin, in
    #: two places that had to be kept in step by hand -- which is the bug this module opens
    #: by describing. One type, read here, handed to `Agent` whole.
    settings: Settings = field(default_factory=Settings)
    #: Where this came from, or None. Front ends print it so a surprising setting is
    #: traceable to a file rather than guessed at.
    path: Path | None = None


def load(path: Path | None = None) -> Config:
    """Read the config, or return defaults if there is none.

    An absent file is the normal case and not an error. A file that exists and cannot be
    parsed is an error, because silently falling back to defaults would leave someone
    looking at settings that are not in force.
    """
    resolved = (path or DEFAULT_PATH).expanduser()
    if not resolved.is_file():
        return Config()

    try:
        raw = tomllib.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{resolved}: {exc}") from exc

    if unknown := set(raw) - _TABLES:
        raise ConfigError(
            f"{resolved}: unknown section(s) {', '.join(sorted(unknown))}. "
            f"Expected: {', '.join(sorted(_TABLES))}."
        )

    provider_table = _table(raw, "provider", _PROVIDER_KEYS, resolved)
    server_table = _table(raw, "server", _SERVER_KEYS, resolved)
    compaction_table = _table(raw, "compaction", _COMPACTION_KEYS, resolved)
    output_table = _table(raw, "output", _OUTPUT_KEYS, resolved)
    limits_table = _table(raw, "limits", _LIMITS_KEYS, resolved)

    extra = provider_table.get("extra_body", {})
    if not isinstance(extra, dict):
        raise ConfigError(f"{resolved}: provider.extra_body must be a table")

    if provider_table.get("api_key") and _is_group_or_world_readable(resolved):
        raise ConfigError(
            f"{resolved} holds an api_key and is readable by other users. "
            f"Run: chmod 600 {resolved}"
        )

    return Config(
        provider=Provider(
            base_url=str(provider_table.get("base_url") or DEFAULT_BASE_URL),
            model=str(provider_table.get("model") or DEFAULT_MODEL),
            api_key=str(provider_table.get("api_key") or ""),
            extra_body=dict(extra),
            context_window=int(
                provider_table.get("context_window") or DEFAULT_CONTEXT_WINDOW
            ),
            temperature=float(provider_table.get("temperature", 0.0)),
            top_p=_optional_float(provider_table.get("top_p")),
            presence_penalty=_optional_float(provider_table.get("presence_penalty")),
        ),
        server=Server(
            host=str(server_table.get("host") or DEFAULT_HOST),
            port=int(server_table.get("port") or DEFAULT_PORT),
            token=str(server_table.get("token") or ""),
        ),
        settings=Settings(
            compaction=Compaction(
                # `is None` rather than `or`: `enabled = false` is the whole point of the
                # key, and `or` would read it as absent and turn compaction back on.
                enabled=(
                    True
                    if compaction_table.get("enabled") is None
                    else bool(compaction_table["enabled"])
                ),
                at=float(compaction_table.get("at") or Compaction().at),
                keep_turns=int(
                    compaction_table.get("keep_turns") or Compaction().keep_turns
                ),
            ),
            output=Output(
                per_result=int(
                    output_table.get("per_result") or Output().per_result
                ),
                per_turn=int(output_table.get("per_turn") or Output().per_turn),
            ),
            limits=Limits(
                max_turns=int(limits_table.get("max_turns") or Limits().max_turns),
                max_consecutive_refusals=int(
                    limits_table.get("max_consecutive_refusals")
                    or Limits().max_consecutive_refusals
                ),
            ),
        ),
        path=resolved,
    )


def _optional_float(value: Any) -> float | None:
    """A sampling parameter, or nothing at all.

    Absent is not zero. `presence_penalty = 0` is a real instruction to penalise nothing,
    and leaving the key out means "do not send it" -- which some gateways treat
    differently from sending the neutral value.
    """
    return None if value is None else float(value)


def _table(raw: dict, name: str, allowed: frozenset[str], path: Path) -> dict:
    table = raw.get(name, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [{name}] must be a table")
    if unknown := set(table) - allowed:
        raise ConfigError(
            f"{path}: unknown key(s) in [{name}]: {', '.join(sorted(unknown))}. "
            f"Expected: {', '.join(sorted(allowed))}."
        )
    return table


def _is_group_or_world_readable(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IRGRP | stat.S_IROTH))


def settle(flag: str | None, environment: str, configured: str, default: str) -> str:
    """One setting, resolved. A flag beats an env var beats the file beats the default.

    Written once rather than at each call site: five settings resolved the same way in two
    front ends is ten chances to get the order subtly different, and a precedence that
    varies per setting is one nobody can hold in their head.
    """
    if flag:
        return flag
    if environment:
        return environment
    if configured:
        return configured
    return default


def write_example(path: Path | None = None) -> Path:
    """Write a starter config, 0600, and return where it went.

    Refuses to overwrite: a command that silently replaces a file holding an API key is one
    people run once by accident.
    """
    resolved = (path or DEFAULT_PATH).expanduser()
    if resolved.exists():
        raise ConfigError(f"{resolved} already exists; edit it rather than replacing it")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        "# harness settings. A flag beats an env var beats this file.\n"
        "\n"
        "[provider]\n"
        f'base_url = "{DEFAULT_BASE_URL}"\n'
        f'model = "{DEFAULT_MODEL}"\n'
        'api_key = ""\n'
        f"context_window = {DEFAULT_CONTEXT_WINDOW}\n"
        "\n"
        "# Sampling, from your model's own card. These are Qwen3.6's for non-thinking\n"
        "# mode; a different model wants different numbers. temperature = 0 is greedy\n"
        "# decoding, which several model cards warn produces endless repetition.\n"
        "# temperature = 0.7\n"
        "# top_p = 0.8\n"
        "# presence_penalty = 1.5\n"
        "\n"
        "# Deployment dialect the OpenAI schema does not cover. A Qwen3 behind LiteLLM\n"
        "# answers with an empty string without this.\n"
        "# [provider.extra_body.chat_template_kwargs]\n"
        "# enable_thinking = false\n"
        "\n"
        "[server]\n"
        f'host = "{DEFAULT_HOST}"\n'
        f"port = {DEFAULT_PORT}\n"
        '# token = ""   # set to require a bearer token; empty means no auth\n'
        "\n"
        "# Summarise and hand off to a smaller context at this fraction of the window.\n"
        "# [compaction]\n"
        "# enabled = true\n"
        "# at = 0.8\n"
        "# keep_turns = 2\n"
        "\n"
        "# How much a tool may say: one result, and one whole turn across all its calls.\n"
        "# [output]\n"
        "# per_result = 30000\n"
        "# per_turn = 120000\n"
        "\n"
        "# How a run may end other than the model stopping.\n"
        "# [limits]\n"
        "# max_turns = 100\n"
        "# max_consecutive_refusals = 10\n",
        encoding="utf-8",
    )
    os.chmod(resolved, 0o600)
    return resolved
