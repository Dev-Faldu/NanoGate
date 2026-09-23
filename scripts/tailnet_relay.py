"""Expose the loopback-only gateway to specific Tailscale peers (allowlist), without widening the
gateway's own bind address.

    python scripts/tailnet_relay.py --allow 100.92.71.77 [--allow 100.85.177.127] [--listen 100.71.168.64:8080]

Connections from any other address are closed immediately and logged (IP only). The gateway's
own authentication (API keys / dashboard sessions) still applies to every request.
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import time


def tailscale_ip() -> str:
    out = subprocess.run(["ip", "-4", "-o", "addr", "show", "tailscale0"], capture_output=True, text=True).stdout
    return out.split()[3].split("/")[0]


async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow", action="append", required=True, help="Tailscale IP allowed to connect (repeatable)")
    ap.add_argument("--listen", default=None, help="host:port (default <tailscale0 ip>:8080)")
    ap.add_argument("--target", default="127.0.0.1:8080")
    a = ap.parse_args()
    lhost, lport = (a.listen or f"{tailscale_ip()}:8080").rsplit(":", 1)
    thost, tport = a.target.rsplit(":", 1)
    allow = set(a.allow)

    async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
        peer = cw.get_extra_info("peername")[0]
        if peer not in allow:
            print(f"{time.strftime('%H:%M:%S')} refused {peer}", flush=True)
            cw.close()
            return
        try:
            tr, tw = await asyncio.open_connection(thost, int(tport))
        except OSError as e:
            print(f"gateway unreachable: {e}", flush=True)
            cw.close()
            return
        await asyncio.gather(pipe(cr, tw), pipe(tr, cw))

    async def run() -> None:
        srv = await asyncio.start_server(handle, lhost, int(lport))
        print(f"relay: http://{lhost}:{lport} -> {thost}:{tport}  allow={sorted(allow)}", flush=True)
        async with srv:
            await srv.serve_forever()

    asyncio.run(run())


if __name__ == "__main__":
    main()
