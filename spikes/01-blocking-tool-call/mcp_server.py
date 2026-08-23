"""Spike MCP server: exposes the blocking broker as a single MCP tool.

This is the component under test. If an MCP client cannot tolerate a tool call
that takes minutes to return, the whole Airgap architecture has to change from
"block the call" to "return a handle, poll for the verdict".

Usage:
    python mcp_server.py               # stdio transport
    python mcp_server.py --http 8792   # streamable-http transport
"""

import os
import sys

import httpx
from mcp.server.mcpserver import MCPServer

BROKER = os.environ.get("BROKER_URL", "http://127.0.0.1:8791")

server = MCPServer("airgap-spike")


@server.tool()
async def request_approval(action: str, justification: str = "") -> str:
    """Ask a human to physically approve an irreversible action.

    Returns only after a human has decided. May take minutes.
    """
    # No client-side timeout here: we are deliberately testing how long the
    # whole chain can hang before something upstream gives up.
    async with httpx.AsyncClient(timeout=None) as client:
        r = await client.post(
            f"{BROKER}/request_approval",
            json={"action": action, "justification": justification},
        )
        r.raise_for_status()
        data = r.json()
    verdict = "APPROVED" if data["approved"] else "DENIED"
    return f"{verdict}: {data.get('reason') or '(no reason given)'}"


if __name__ == "__main__":
    if "--http" in sys.argv:
        port = int(sys.argv[sys.argv.index("--http") + 1])
        server.run(transport="streamable-http", host="127.0.0.1", port=port)
    else:
        server.run()
