"""Flags, then environment, then the config file, then the built-in defaults.

One rule for every setting, in one place, for both `run` and `serve`. Five settings resolved
separately in two front ends is ten chances to get the order subtly different, and that is
not hypothetical: `--extra-body` existed on one command and not the other, so a deployment
that needed it worked one way and silently produced nothing the other.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast

from harness.config import (
    DEFAULT_BASE_URL,
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_PORT,
    Config,
    Server,
    flag,
    int_flag,
    load,
    settle,
)
from harness.types import JSON


class CliError(Exception):
    """Caller input that cannot be used, with a sentence saying why."""


class Commands(Protocol):
    """Where a subcommand registers itself: the one method of argparse's subparsers
    object that a command needs, named here because argparse keeps the type private."""

    def add_parser(self, name: str, *, help: str | None = None) -> argparse.ArgumentParser: ...


def provider_flags(parser: argparse.ArgumentParser) -> None:
    """The flags every command that talks to a model shares."""
    _ = parser.add_argument("--config", default="", help="Path to config.toml.")
    _ = parser.add_argument(
        "--model",
        default="",
        help="Model name (env: HARNESS_MODEL, or provider.model in config.toml).",
    )
    _ = parser.add_argument(
        "--base-url",
        default="",
        help="OpenAI-compatible endpoint (env: HARNESS_BASE_URL, or provider.base_url).",
    )
    _ = parser.add_argument(
        "--api-key", default="", help="env: HARNESS_API_KEY, or provider.api_key"
    )
    _ = parser.add_argument(
        "--context-window",
        type=int,
        default=None,
        help="How much context the model has. Past the configured share of it the agent "
        + "summarises what has happened and carries on in a smaller one; nothing is removed "
        + "from the transcript. (env: HARNESS_CONTEXT_WINDOW, or provider.context_window)",
    )
    _ = parser.add_argument(
        "--extra-body",
        default="",
        help="JSON merged into every request body, for deployment dialect the OpenAI schema "
        + "does not cover, e.g. '{\"chat_template_kwargs\": {\"enable_thinking\": false}}' "
        + "for Qwen3, which otherwise answers with an empty string. (env: HARNESS_EXTRA_BODY)",
    )


def resolve(args: argparse.Namespace) -> Config:
    """Every setting, resolved. `[server]` is filled from flags only `serve` declares, and
    reads as the file's values for any other command."""
    config = flag(args, "config")
    stored = load(Path(config).expanduser() if config else None)
    environment = os.environ
    extra = _extra_body(flag(args, "extra_body")) or _extra_body(
        environment.get("HARNESS_EXTRA_BODY", "")
    )
    provider = replace(
        stored.provider,
        base_url=settle(
            flag(args, "base_url"),
            environment.get("HARNESS_BASE_URL", ""),
            stored.provider.base_url,
            DEFAULT_BASE_URL,
        ),
        model=settle(
            flag(args, "model"),
            environment.get("HARNESS_MODEL", ""),
            stored.provider.model,
            DEFAULT_MODEL,
        ),
        api_key=settle(
            flag(args, "api_key"),
            environment.get("HARNESS_API_KEY", ""),
            stored.provider.api_key,
            "",
        ),
        context_window=int(
            settle(
                str(int_flag(args, "context_window") or ""),
                environment.get("HARNESS_CONTEXT_WINDOW", ""),
                str(stored.provider.context_window or ""),
                str(DEFAULT_CONTEXT_WINDOW),
            )
        ),
        extra_body=extra or stored.provider.extra_body,
    )
    server = Server(
        host=settle(
            flag(args, "host"),
            environment.get("HARNESS_HOST", ""),
            stored.server.host,
            DEFAULT_HOST,
        ),
        port=int(
            (int_flag(args, "port") or 0)
            or environment.get("HARNESS_PORT", "")
            or stored.server.port
            or DEFAULT_PORT
        ),
        token=settle(
            flag(args, "token"),
            environment.get("HARNESS_TOKEN", ""),
            stored.server.token,
            "",
        ),
    )
    return Config(provider=provider, server=server, settings=stored.settings, path=stored.path)


def require_key(config: Config) -> None:
    """A remote endpoint needs a key; a local one does not.

    Checked after resolving, not before: until 2026-09-02 the CLI read the flag and the
    environment only, so a key in config.toml -- the file the server and the evals read --
    was refused with "no API key" before the file was opened.
    """
    local = config.provider.base_url.startswith(("http://localhost", "http://127.0.0.1"))
    if not config.provider.api_key and not local:
        raise CliError(
            "no API key. Set provider.api_key in config.toml, HARNESS_API_KEY, or --api-key "
            + "(not needed for a local endpoint)."
        )


def _extra_body(raw: str) -> JSON:
    if not raw.strip():
        return {}
    try:
        parsed = cast("object", json.loads(raw))
    except json.JSONDecodeError as exc:
        raise CliError(f"--extra-body is not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CliError("--extra-body must be a JSON object")
    return cast("JSON", parsed)
