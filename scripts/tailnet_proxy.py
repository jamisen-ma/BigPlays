"""Expose the local BigPlays server on this Mac's Tailscale IP only.

The App Store build of Tailscale can't run `tailscale serve` from the CLI, so this
forwards raw TCP from <tailscale-ip>:8000 to 127.0.0.1:8000. It works with SSE and
video range requests. Only devices on your tailnet can reach the Tailscale IP.

    .venv-local/bin/python scripts/tailnet_proxy.py            # auto-detects the IP
    .venv-local/bin/python scripts/tailnet_proxy.py 100.x.y.z  # explicit IP
"""
import asyncio
import subprocess
import sys

TAILSCALE = '/Applications/Tailscale.app/Contents/MacOS/Tailscale'
PORT = 8765
TARGET = ('127.0.0.1', 8000)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    try:
        up_r, up_w = await asyncio.open_connection(*TARGET)
    except OSError:
        client_w.close()
        return
    await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))


async def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else subprocess.check_output([TAILSCALE, 'ip', '-4'], text=True).split()[0]
    if not host.startswith('100.'):
        sys.exit(f'refusing to bind non-Tailscale address {host}')
    server = await asyncio.start_server(handle, host, PORT)
    print(f'BigPlays on tailnet: http://{host}:{PORT} -> {TARGET[0]}:{TARGET[1]}', flush=True)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
