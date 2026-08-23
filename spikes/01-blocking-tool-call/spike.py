"""Spike driver.

QUESTION: does a real MCP client tolerate a tool call that blocks for minutes
while a human decides?

If YES  -> Airgap's blocking-approval architecture is sound as specified.
If NO   -> the broker must return a request handle immediately and the agent
           must poll, which changes the tool contract and every ticket downstream.

Tested on BOTH transports, because they fail differently: stdio has no network
layer to time out, streamable-http does (ASGI servers, proxies, load balancers).

Run:  python spike.py [hold_seconds]
"""

import asyncio
import inspect
import subprocess
import sys
import time
from pathlib import Path

import httpx
import mcp
from mcp import ClientSession, StdioServerParameters, stdio_client

HERE = Path(__file__).parent
BROKER = "http://127.0.0.1:8791"
MCP_HTTP_PORT = 8792
HOLD = int(sys.argv[1]) if len(sys.argv) > 1 else 120


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}", flush=True)


def report_sdk_defaults() -> None:
    hr("SDK TIMEOUT SURFACE -- what knobs exist and what they default to")
    targets = [
        (ClientSession.__init__, "ClientSession.__init__"),
        (ClientSession.call_tool, "ClientSession.call_tool"),
        (mcp.Client.__init__, "Client.__init__"),
        (mcp.Client.call_tool, "Client.call_tool"),
    ]
    for fn, name in targets:
        params = inspect.signature(fn).parameters
        hits = {k: v.default for k, v in params.items() if "timeout" in k.lower()}
        if hits:
            for k, d in hits.items():
                print(f"  {name}({k}) default = {d!r}", flush=True)
        else:
            print(f"  {name} -- no timeout parameter", flush=True)


async def wait_for(url: str, label: str, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    async with httpx.AsyncClient() as c:
        while time.time() < deadline:
            try:
                r = await c.get(url, timeout=2)
                if r.status_code < 500:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.25)
    raise RuntimeError(f"{label} never came up at {url}")


async def press_the_button_after(delay: float) -> str:
    """Stand-in for the Arduino: wait, then approve whatever is pending."""
    await asyncio.sleep(delay)
    async with httpx.AsyncClient(timeout=15) as c:
        pend = (await c.get(f"{BROKER}/pending")).json()
        if not pend:
            return "!! NOTHING WAS PENDING -- the call had already died upstream"
        rid = pend[0]["id"]
        await c.post(f"{BROKER}/decide/{rid}",
                     json={"approved": True, "reason": "human pressed APPROVE"})
        return f"pressed APPROVE on request {rid} after {delay}s"


ARGS = {"action": "DROP TABLE users_backup", "justification": "cleaning up staging"}


def as_text(result) -> str:
    parts = []
    for c in getattr(result, "content", []) or []:
        t = getattr(c, "text", None)
        if t:
            parts.append(t)
    return " ".join(parts) or repr(result)[:200]


async def case_stdio() -> tuple[bool, float, str]:
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(HERE / "mcp_server.py")])
    started = time.monotonic()
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                call = asyncio.create_task(session.call_tool("request_approval", ARGS))
                press = asyncio.create_task(press_the_button_after(HOLD))
                result = await call
                print(f"  driver: {await press}", flush=True)
                return True, time.monotonic() - started, as_text(result)
    except Exception as e:
        return False, time.monotonic() - started, f"{type(e).__name__}: {e}"


async def case_http() -> tuple[bool, float, str]:
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "mcp_server.py"), "--http", str(MCP_HTTP_PORT)],
        cwd=str(HERE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    started = time.monotonic()
    try:
        await wait_for(f"http://127.0.0.1:{MCP_HTTP_PORT}/mcp", "mcp http server")
        started = time.monotonic()
        async with mcp.Client(f"http://127.0.0.1:{MCP_HTTP_PORT}/mcp") as client:
            call = asyncio.create_task(client.call_tool("request_approval", ARGS))
            press = asyncio.create_task(press_the_button_after(HOLD))
            result = await call
            print(f"  driver: {await press}", flush=True)
            return True, time.monotonic() - started, as_text(result)
    except Exception as e:
        return False, time.monotonic() - started, f"{type(e).__name__}: {e}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


async def main() -> int:
    report_sdk_defaults()

    broker = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "broker:app",
         "--host", "127.0.0.1", "--port", "8791", "--log-level", "warning"],
        cwd=str(HERE),
    )
    results: list[tuple[str, bool, float, str]] = []
    try:
        await wait_for(f"{BROKER}/health", "broker")
        print("\nbroker up on :8791", flush=True)

        for label, fn in (("stdio transport, client defaults", case_stdio),
                          ("streamable-http transport, client defaults", case_http)):
            hr(f"CASE: {label}  -- holding the call for {HOLD}s")
            ok, elapsed, detail = await fn()
            print(f"  -> {'SURVIVED' if ok else 'DIED'} after {elapsed:.1f}s", flush=True)
            print(f"  -> {detail}", flush=True)
            results.append((label, ok, elapsed, detail))
    finally:
        broker.terminate()
        try:
            broker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            broker.kill()

    hr("FINDING")
    for label, ok, elapsed, _ in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}  ({elapsed:.1f}s)", flush=True)
    passed = [ok for _, ok, _, _ in results]
    if all(passed):
        print(f"\n  Blocking tool calls survive {HOLD}s on BOTH transports with default", flush=True)
        print("  client settings. Airgap's blocking architecture is SOUND as specified.", flush=True)
    elif any(passed):
        print(f"\n  Blocking survives {HOLD}s on some transports but not all. The spec must", flush=True)
        print("  pin the working transport and document the failure mode of the other.", flush=True)
    else:
        print("\n  Blocking tool calls DO NOT survive. Architecture must change to", flush=True)
        print("  handle + poll: request_approval returns an id, check_approval polls.", flush=True)
    return 0 if any(passed) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
