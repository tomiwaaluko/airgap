# Spike 01 — Can an MCP tool call block for minutes?

**Date:** 2026-08-22
**Status:** resolved — the blocking architecture is sound
**Decides:** the shape of `request_approval` in `docs/spec/03-broker-api.md`

## The question

Airgap's whole design rests on a tool call that hangs while a human decides. If
an MCP client gives up after 30 or 60 seconds, `request_approval` cannot block —
it would have to return a handle immediately and make the agent poll, which
changes the tool contract, the broker, the dashboard, and every ticket
downstream. Worth two minutes of wall clock to find out before writing the spec.

## What was tested

A real MCP client → a real MCP server → a FastAPI broker that parks the request
on an `asyncio.Event` and does not resolve it until a separate process calls
`/decide`. The Arduino is stubbed out by that second call, so this tests the
software chain only — which is the part that could impose a timeout.

Both transports, because they fail differently: stdio has no network layer to
time out, streamable-http does.

- `mcp` Python SDK **2.0.0**, Python 3.14.3, Windows.
- Hold duration **150 s**, chosen to clear the two most common default timeouts
  (60 s and 120 s) decisively.
- Client settings left at **defaults** — no `read_timeout_seconds` passed.

## Result

```
[PASS] stdio transport, client defaults              (151.1s)
[PASS] streamable-http transport, client defaults    (150.1s)
```

Full run recorded in [`spike-result.txt`](spike-result.txt).

Timeout surface, for the record — nothing defaults to a finite value:

```
ClientSession.__init__(read_timeout_seconds) default = None
ClientSession.call_tool(read_timeout_seconds) default = None
Client.__init__(read_timeout_seconds)        default = None
Client.call_tool(read_timeout_seconds)       default = None
```

## Reading

**Blocking works, on both transports, out of the box.** `request_approval` is
specified as a blocking call. Keep it blocking: it is what makes the demo legible
(the agent visibly stalls) and what makes the guarantee simple to state.

## Caveats worth knowing

1. **150 s is not infinity.** A real approval could take ten minutes if the user
   walks away. Nothing here defaults to a timeout, so there is no reason to expect
   a wall — but it has not been measured past 150 s. The 30-minute request expiry
   in the broker spec exists partly to bound this.
2. **This was localhost.** A reverse proxy, load balancer, or corporate gateway
   between client and server may impose its own idle timeout — nginx defaults to
   60 s on `proxy_read_timeout`. If Airgap is ever deployed behind one, re-run
   this spike against that deployment before trusting it.
3. **Only the Python SDK was tested.** A different MCP client implementation
   could behave differently. The fallback design is documented in
   `docs/spec/03-broker-api.md` — do not build it until something needs it.

## Incidental findings

- `mcp` 2.x **removed `mcp.server.fastmcp`**. The server class is now `MCPServer`
  in `mcp.server.mcpserver`, and there is a new high-level `mcp.Client` that
  accepts a URL string, an in-process server, or a custom transport. Any tutorial
  or generated code importing `FastMCP` is written against 1.x and will not run.
  This is captured in ticket AIR-10 so it isn't rediscovered the hard way.

## Reproducing

```bash
cd spikes/01-blocking-tool-call
uv venv .venv && uv pip install --python .venv fastapi uvicorn httpx mcp
.venv/Scripts/python.exe spike.py 150     # or: .venv/bin/python spike.py 150
```
