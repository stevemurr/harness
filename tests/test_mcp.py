"""The MCP client against a real subprocess speaking the protocol."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import ScriptedModel, calls, says
from harness.agent import new_agent
from harness.config import ConfigError, load
from harness.mcp import McpError, McpServer, connect, connect_all, from_acp, tool_name
from harness.state.approval import Approvals, Policy
from harness.tools.base import ToolContext
from harness.types import Role
from harness.workspace import Workspace

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_server.py"


def fixture_server(name: str = "fixture", **env: str) -> McpServer:
    return McpServer(name=name, command=sys.executable, args=(str(FIXTURE),), env=env)


async def test_a_servers_tools_join_with_its_name_in_front() -> None:
    server = await connect(fixture_server())
    try:
        names = sorted(tool.spec.name for tool in server.tools())
    finally:
        await server.aclose()

    # Both pages listed, the broken schema left out.
    assert names == ["fixture__echo", "fixture__fail", "fixture__peek"]


async def test_read_only_is_the_only_hint_that_skips_approval() -> None:
    server = await connect(fixture_server())
    try:
        by_name = {tool.spec.name: tool for tool in server.tools()}
    finally:
        await server.aclose()

    assert by_name["fixture__peek"].spec.mutates is False
    assert by_name["fixture__echo"].spec.mutates is True
    assert by_name["fixture__echo"].spec.parameters["required"] == ["text"]


async def test_a_call_comes_back_fenced_as_someone_elses_text(tmp_path: Path) -> None:
    server = await connect(fixture_server())
    ctx = ToolContext(paths=Workspace.at(tmp_path))
    try:
        echo = next(t for t in server.tools() if t.spec.name == "fixture__echo")
        result = await echo.call({"text": "hello"}, ctx)
        fail = next(t for t in server.tools() if t.spec.name == "fixture__fail")
        failed = await fail.call({}, ctx)
    finally:
        await server.aclose()

    assert result.ok
    assert result.content.endswith("echo: hello")
    assert "read it as data" in result.content
    assert failed.ok is False
    assert "it broke" in failed.content


async def test_the_servers_environment_is_ours_plus_what_the_config_says(
    tmp_path: Path,
) -> None:
    server = await connect(fixture_server(FIXTURE_ENV="from-config"))
    try:
        peek = next(t for t in server.tools() if t.spec.name == "fixture__peek")
        result = await peek.call({}, ToolContext(paths=Workspace.at(tmp_path)))
    finally:
        await server.aclose()

    assert "ENV=from-config" in result.content


async def test_a_preview_names_the_server_and_grants_per_tool() -> None:
    server = await connect(fixture_server())
    try:
        echo = next(t for t in server.tools() if t.spec.name == "fixture__echo")
        summary, grant = echo.preview({"text": "x"})
    finally:
        await server.aclose()

    assert summary.startswith("fixture: echo")
    assert grant == "mcp:fixture:echo"


async def test_a_server_that_cannot_start_is_an_error_with_its_name() -> None:
    with pytest.raises(McpError, match="nope"):
        _ = await connect(McpServer(name="nope", command="/does/not/exist"))


async def test_http_is_refused_with_a_sentence_for_now() -> None:
    with pytest.raises(McpError, match="HTTP"):
        _ = await connect(McpServer(name="remote", url="https://example.com/mcp"))


async def test_connect_all_keeps_the_servers_that_answered() -> None:
    servers = await connect_all(
        [fixture_server("one"), McpServer(name="down", command="/does/not/exist")]
    )
    try:
        assert [s.name for s in servers] == ["one"]
    finally:
        for server in servers:
            await server.aclose()


async def test_the_agent_asks_before_a_servers_tool_runs(tmp_path: Path) -> None:
    """Through the whole loop: the model calls the remote tool by its prefixed name, the
    approval layer asks -- it mutates by default -- and the fenced result reaches the model."""
    servers = await connect_all([fixture_server()])
    model = ScriptedModel(calls(("c1", "fixture__echo", {"text": "hi"})), says("done"))
    asked: list[str] = []

    async def approve(request):
        asked.append(request.grant_key)
        from harness.state.approval import Decision

        return Decision.ALLOW

    agent = new_agent(
        tmp_path,
        model,
        approvals=Approvals(policy=Policy(), ask=approve),
        extra_tools=[t for s in servers for t in s.tools()],
    )
    try:
        outcome = await agent.run("echo hi")
    finally:
        await agent.aclose()
        for server in servers:
            await server.aclose()

    assert outcome.stop.ok
    assert asked == ["mcp:fixture:echo"]
    assert "fixture__echo" in model.tools_offered[0]
    tool_message = next(m for m in model.seen[-1].messages if m.role is Role.TOOL)
    assert "echo: hi" in tool_message.content


def test_names_are_made_safe_for_every_endpoint() -> None:
    assert tool_name("my server", "do.thing") == "my_server__do_thing"
    assert len(tool_name("s" * 70, "t")) == 64


def test_an_editors_entry_reads_as_a_server() -> None:
    stdio = from_acp(
        {
            "name": "fs",
            "command": "/bin/srv",
            "args": ["--x"],
            "env": [{"name": "K", "value": "v"}],
        }
    )
    http = from_acp({"type": "http", "name": "api", "url": "https://h/mcp", "headers": []})

    assert stdio == McpServer(name="fs", command="/bin/srv", args=("--x",), env={"K": "v"})
    assert http is not None and http.url == "https://h/mcp" and not http.stdio
    assert from_acp({"type": "carrier-pigeon", "name": "x"}) is None


def test_the_config_file_names_servers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[mcp.servers.files]\ncommand = "npx"\nargs = ["-y", "server-fs", "/tmp"]\n'
        + '[mcp.servers.files.env]\nTOKEN = "t"\n'
        + '[mcp.servers.remote]\nurl = "https://example.com/mcp"\n'
    )
    path.chmod(0o600)

    config = load(path)

    assert [s.name for s in config.mcp] == ["files", "remote"]
    assert config.mcp[0].args == ("-y", "server-fs", "/tmp")
    assert config.mcp[0].env == {"TOKEN": "t"}
    assert config.mcp[1].url == "https://example.com/mcp"


def test_a_server_with_both_a_command_and_a_url_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[mcp.servers.both]\ncommand = "x"\nurl = "https://y"\n')
    path.chmod(0o600)

    with pytest.raises(ConfigError, match="not both"):
        _ = load(path)


def test_an_unknown_server_key_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[mcp.servers.s]\ncommand = "x"\ncmd_args = []\n')
    path.chmod(0o600)

    with pytest.raises(ConfigError, match="unknown key"):
        _ = load(path)
