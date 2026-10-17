"""Every tool's `character` parameter says, in its schema, that a path works.

The behaviour (`character.resolve_input`) is covered elsewhere. This guards the
part a caller actually sees: an MCP client reads the generated input schema,
not the source, so a tool whose `character` parameter looked like "an object"
got called with a guessed argument name and misreported as object-only.
"""

from __future__ import annotations

import asyncio

from pf2e_mcp.server.app import mcp


def _character_tools():
    tools = asyncio.run(mcp.list_tools())
    return [t for t in tools if "character" in t.input_schema.get("properties", {})]


def test_character_parameter_is_documented_on_every_tool():
    tools = _character_tools()
    assert len(tools) >= 10, "expected the build_*, character and sheet tools"
    missing = [
        t.name
        for t in tools
        if "path" not in t.input_schema["properties"]["character"].get("description", "").lower()
    ]
    assert not missing, f"`character` schema does not mention a path: {missing}"
