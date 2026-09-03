"""What a deployment writes down: the file, and its precedence.

`config` is the file and what it resolves to, provider and server included; `settings` is
what a run is handed. This module reads the one and produces the other, and `settings`
knows nothing about files.

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

import argparse
import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from harness.mcp.base import McpServer
from harness.settings import Approval, Compaction, Limits, Output, Settings
from harness.state.approval import POLICY_NAMES, named_policy
from harness.types import JSON

#: Where the harness keeps its own things: config, threads, boards, language servers. One
#: folder a person can inspect and delete. Not expanded here, so a test can point `HOME`
#: elsewhere before anything reads them.
HOME = Path("~/.harness")
DEFAULT_PATH = HOME / "config.toml"
THREADS = HOME / "threads"
BOARDS = HOME / "boards"
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
_MCP_KEYS = frozenset({"servers"})
_MCP_SERVER_KEYS = frozenset({"command", "args", "env", "url", "headers"})
_APPROVAL_KEYS = frozenset({"policy", "always_allow"})
_TABLES = frozenset({
    "provider", "server", "compaction", "output", "limits", "mcp", "approval",
})


class ConfigError(Exception):
    """The config file exists and cannot be used. Never raised for an absent file."""


@dataclass(frozen=True, slots=True)
class Provider:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = field(default="", repr=False)
    #: Merged into every model request. See `providers/openai.py` for why this exists.
    extra_body: JSON = field(default_factory=dict)
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
    #: `server/app.py` each rebuilt `Compaction` field by field out of a config-local twin, in
    #: two places that had to be kept in step by hand -- which is the bug this module opens
    #: by describing. One type, read here, handed to `Agent` whole.
    settings: Settings = field(default_factory=Settings)
    #: Tool servers to connect to, from `[mcp.servers.<name>]`. Every front end connects
    #: to the same ones, for the reason every front end reads the same provider.
    mcp: tuple[McpServer, ...] = ()
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
        raw: JSON = tomllib.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{resolved}: {exc}") from exc

    if unknown := set(raw) - _TABLES:
        raise ConfigError(
            f"{resolved}: unknown section(s) {', '.join(sorted(unknown))}. "
            + f"Expected: {', '.join(sorted(_TABLES))}."
        )

    provider = _table(raw, "provider", _PROVIDER_KEYS, resolved)
    server = _table(raw, "server", _SERVER_KEYS, resolved)
    compaction = _table(raw, "compaction", _COMPACTION_KEYS, resolved)
    output = _table(raw, "output", _OUTPUT_KEYS, resolved)
    limits = _table(raw, "limits", _LIMITS_KEYS, resolved)
    mcp = _mcp_servers(_table(raw, "mcp", _MCP_KEYS, resolved))
    approval = _table(raw, "approval", _APPROVAL_KEYS, resolved)
    policy = approval.text("policy", Approval().policy)
    if named_policy(policy) is None:
        raise ConfigError(
            f"{resolved}: approval.policy {policy!r} is not one of: "
            + f"{', '.join(POLICY_NAMES)}."
        )

    extra = provider.values.get("extra_body", {})
    if not isinstance(extra, dict):
        raise ConfigError(f"{resolved}: provider.extra_body must be a table")

    if provider.values.get("api_key") and _is_group_or_world_readable(resolved):
        raise ConfigError(
            f"{resolved} holds an api_key and is readable by other users. "
            + f"Run: chmod 600 {resolved}"
        )

    return Config(
        provider=Provider(
            base_url=provider.text("base_url", DEFAULT_BASE_URL),
            model=provider.text("model", DEFAULT_MODEL),
            api_key=provider.text("api_key", ""),
            extra_body=dict(cast("JSON", extra)),
            context_window=provider.integer("context_window", DEFAULT_CONTEXT_WINDOW),
            temperature=provider.number("temperature", 0.0),
            top_p=provider.optional_number("top_p"),
            presence_penalty=provider.optional_number("presence_penalty"),
        ),
        server=Server(
            host=server.text("host", DEFAULT_HOST),
            port=server.integer("port", DEFAULT_PORT),
            token=server.text("token", ""),
        ),
        settings=Settings(
            compaction=Compaction(
                # `is None` rather than `or`: `enabled = false` is the whole point of the
                # key, and `or` would read it as absent and turn compaction back on.
                enabled=compaction.flag("enabled", True),
                at=compaction.number("at", Compaction().at),
                keep_turns=compaction.integer("keep_turns", Compaction().keep_turns),
            ),
            output=Output(
                per_result=output.integer("per_result", Output().per_result),
                per_turn=output.integer("per_turn", Output().per_turn),
            ),
            limits=Limits(
                max_turns=limits.integer("max_turns", Limits().max_turns),
                max_consecutive_refusals=limits.integer(
                    "max_consecutive_refusals", Limits().max_consecutive_refusals
                ),
            ),
            approval=Approval(
                policy=policy,
                always_allow=tuple(str(rule) for rule in _strings(approval, "always_allow")),
            ),
        ),
        mcp=mcp,
        path=resolved,
    )


def _mcp_servers(table: _Table) -> tuple[McpServer, ...]:
    """`[mcp.servers.<name>]`, one server each. A name is the sub-table's key, so it
    cannot be missing, and a server that is both a command and a URL is refused here
    rather than guessed at."""
    servers = table.values.get("servers", {})
    if not isinstance(servers, dict):
        raise ConfigError(f"{table.path}: mcp.servers must be a table of tables")
    found: list[McpServer] = []
    for name, raw in cast("JSON", servers).items():
        entry = _table(cast("JSON", servers), name, _MCP_SERVER_KEYS, table.path)
        _ = raw
        try:
            found.append(
                McpServer(
                    name=name,
                    command=entry.text("command", ""),
                    args=tuple(str(a) for a in _strings(entry, "args")),
                    env=_mapping(entry, "env"),
                    url=entry.text("url", ""),
                    headers=_mapping(entry, "headers"),
                )
            )
        except ValueError as exc:
            raise ConfigError(f"{table.path}: {exc}") from exc
    return tuple(found)


def _strings(table: _Table, key: str) -> list[object]:
    value = table.values.get(key, [])
    if not isinstance(value, list):
        raise ConfigError(f"{table.path}: {table.name}.{key} must be a list")
    return cast("list[object]", value)


def _mapping(table: _Table, key: str) -> dict[str, str]:
    value = table.values.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{table.path}: {table.name}.{key} must be a table")
    return {str(k): str(v) for k, v in cast("JSON", value).items()}


@dataclass(frozen=True, slots=True)
class _Table:
    """One `[section]`, read by type.

    TOML values are typed, so a key of the wrong type is the file being wrong, and it is
    said so with the key's name. An absent or empty value is the default -- the same `or`
    the reads used to do -- except for `flag`, where `false` is the whole point of the
    key and must not read as absent.
    """

    name: str
    values: JSON
    path: Path

    def text(self, key: str, default: str) -> str:
        value = self.values.get(key)
        return default if not value else str(value)

    def integer(self, key: str, default: int) -> int:
        value = self.values.get(key)
        if value is None or value == 0:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{self.path}: {self.name}.{key} must be an integer")
        return value

    def number(self, key: str, default: float) -> float:
        found = self.optional_number(key)
        return found if found else default

    def optional_number(self, key: str) -> float | None:
        """A sampling parameter, or nothing at all.

        Absent is not zero. `presence_penalty = 0` is a real instruction to penalise
        nothing, and leaving the key out means "do not send it" -- which some gateways
        treat differently from sending the neutral value.
        """
        value = self.values.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ConfigError(f"{self.path}: {self.name}.{key} must be a number")
        return float(value)

    def flag(self, key: str, default: bool) -> bool:
        value = self.values.get(key)
        return default if value is None else bool(value)


def _table(raw: JSON, name: str, allowed: frozenset[str], path: Path) -> _Table:
    table = raw.get(name, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [{name}] must be a table")
    values = cast("JSON", table)
    if unknown := set(values) - allowed:
        raise ConfigError(
            f"{path}: unknown key(s) in [{name}]: {', '.join(sorted(unknown))}. "
            + f"Expected: {', '.join(sorted(allowed))}."
        )
    return _Table(name, values, path)


def _is_group_or_world_readable(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IRGRP | stat.S_IROTH))


def flag(args: argparse.Namespace, name: str) -> str:
    """One string flag. `Namespace` is untyped, and this is the one place it is read."""
    value = cast("object", getattr(args, name, ""))
    return value if isinstance(value, str) else ""


def int_flag(args: argparse.Namespace, name: str) -> int | None:
    """One integer flag, or `None` when it was not given."""
    value = cast("object", getattr(args, name, None))
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def bool_flag(args: argparse.Namespace, name: str) -> bool:
    return cast("object", getattr(args, name, False)) is True


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
    _ = resolved.write_text(
        "# harness settings. A flag beats an env var beats this file.\n"
        + "\n"
        + "[provider]\n"
        + f'base_url = "{DEFAULT_BASE_URL}"\n'
        + f'model = "{DEFAULT_MODEL}"\n'
        + 'api_key = ""\n'
        + f"context_window = {DEFAULT_CONTEXT_WINDOW}\n"
        + "\n"
        + "# Sampling, from your model's own card. These are Qwen3.6's for non-thinking\n"
        + "# mode; a different model wants different numbers. temperature = 0 is greedy\n"
        + "# decoding, which several model cards warn produces endless repetition.\n"
        + "# temperature = 0.7\n"
        + "# top_p = 0.8\n"
        + "# presence_penalty = 1.5\n"
        + "\n"
        + "# Deployment dialect the OpenAI schema does not cover. A Qwen3 behind LiteLLM\n"
        + "# answers with an empty string without this.\n"
        + "# [provider.extra_body.chat_template_kwargs]\n"
        + "# enable_thinking = false\n"
        + "\n"
        + "[server]\n"
        + f'host = "{DEFAULT_HOST}"\n'
        + f"port = {DEFAULT_PORT}\n"
        + '# token = ""   # set to require a bearer token; empty means no auth\n'
        + "\n"
        + "# What asks before it runs. ask | edits | full-access; and standing rules by\n"
        + "# grant key: run:<program> for a command, write_file for every write.\n"
        + "# [approval]\n"
        + '# policy = "ask"\n'
        + '# always_allow = ["run:git"]\n'
        + "\n"
        + "# Summarise and hand off to a smaller context at this fraction of the window.\n"
        + "# [compaction]\n"
        + "# enabled = true\n"
        + "# at = 0.8\n"
        + "# keep_turns = 2\n"
        + "\n"
        + "# How much a tool may say: one result, and one whole turn across all its calls.\n"
        + "# [output]\n"
        + "# per_result = 30000\n"
        + "# per_turn = 120000\n"
        + "\n"
        + "# How a run may end other than the model stopping.\n"
        + "# [limits]\n"
        + "# max_turns = 0   # 0 means no limit\n"
        + "# max_consecutive_refusals = 10\n"
        + "\n"
        + "# Tool servers (MCP), one table each. Their tools join the built-in ones as\n"
        + "# <server>__<tool>, and each is asked about before it runs unless the server\n"
        + "# says it only reads. stdio only for now: a url is refused with a sentence.\n"
        + "# [mcp.servers.files]\n"
        + '# command = "npx"\n'
        + '# args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]\n'
        + "# [mcp.servers.files.env]\n"
        + '# API_KEY = ""\n',
        encoding="utf-8",
    )
    os.chmod(resolved, 0o600)
    return resolved
