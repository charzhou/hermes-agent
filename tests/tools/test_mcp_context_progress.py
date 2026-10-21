"""Hermes context and progress tokens coexist on a real MCP tools/call request."""

import json
import sys
import textwrap

import pytest

SERVER = textwrap.dedent('''\
    import json
    from mcp.server.mcpserver import MCPServer, Context
    app = MCPServer("context_progress")

    @app.tool()
    async def inspect_context(ctx: Context) -> str:
        await ctx.report_progress(100, 100, "complete")
        return json.dumps(ctx.request_context.meta)

    app.run()
''')


@pytest.mark.no_isolate
@pytest.mark.platforms("posix")
@pytest.mark.parametrize("forward_context", [False, True])
def test_context_forwarding_preserves_progress_notifications(monkeypatch, tmp_path, forward_context):
    pytest.importorskip("mcp.server.mcpserver")
    home = tmp_path / "home"
    home.mkdir()
    script = home / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    (home / "config.yaml").write_text(json.dumps({"mcp_servers": {"context_progress": {
        "command": sys.executable, "args": [str(script)], "forward_hermes_context": forward_context,
    }}}), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    from agent.tool_progress_relay import bound_tool_progress
    from tools import mcp_tool_discovery as discovery
    from tools import mcp_tool_loop
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    from tools.registry import registry

    name = "mcp__context_progress__inspect_context"
    events = []
    try:
        assert name in discovery.discover_mcp_tools()
        with bound_tool_progress(lambda *a, **kw: events.append((a, kw)), name, "call-context"):
            result = json.loads(registry.dispatch(name, {}, session_id="session-context"))
        assert "result" in result, result
        metadata = json.loads(result["result"])
    finally:
        shutdown_mcp_servers()
        mcp_tool_loop._stop_mcp_loop()

    if forward_context:
        assert metadata["io.nous.hermes/context"]["session_id"] == "session-context"
    else:
        assert "io.nous.hermes/context" not in metadata
    assert len(events) == 1
    args, payload = events[0]
    assert args[:3] == ("tool.progress", name, "complete")
    assert payload["tool_call_id"] == "call-context"
    assert payload["percent"] == 100
