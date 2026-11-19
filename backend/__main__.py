"""Launch the backend with its validated binding configuration."""
import argparse

import uvicorn

from backend.config import settings
from backend.utils.config_validation import ConfigValidator


def main():
    parser = argparse.ArgumentParser(description='Run SovereignAI backend')
    parser.add_argument('--reload', action='store_true', help='Reload after code changes')
    args = parser.parse_args()
    if settings.is_local_dev:
        import socket
        import sys

        if args.reload:
            parser.error("local_dev runs in one foreground process and does not support --reload.")
        if sys.version_info[:2] != (3, 13):
            parser.error("local_dev requires Python 3.13.")
        from backend.local_dev import FRONTEND_DIST
        from backend.main import create_app

        try:
            app = create_app(settings, local_frontend=FRONTEND_DIST)
        except ValueError as exc:
            parser.error(str(exc))
        host, port = settings.backend_host, settings.backend_port
        family = socket.AF_INET6 if host == "::1" else socket.AF_INET
        config = uvicorn.Config(
            app, host=host, port=port, workers=1, proxy_headers=False, timeout_graceful_shutdown=10,
        )
        address = f"[{host}]" if family == socket.AF_INET6 else host
        with socket.socket(family, socket.SOCK_STREAM) as listener:
            operation = "configure exclusive address use"
            try:
                if sys.platform == "win32":
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                operation = "bind"
                listener.bind((host, port))
                operation = "listen"
                listener.listen(config.backlog)
                # Windows asyncio initializes a TCP self-pipe. Check local
                # connectivity before Uvicorn enters that unbounded OS wait.
                operation = "set probe deadline"
                listener.settimeout(2)
                operation = "connect"
                with socket.create_connection((host, port), timeout=2):
                    operation = "accept"
                    probe, _ = listener.accept()
                    probe.close()
            except OSError as exc:
                parser.error(f"Local listener {operation} failed ({address}:{port}): {exc}")
            listener.setblocking(False)
            print(f"Local development workbench: http://{address}:{port}/", flush=True)
            server = uvicorn.Server(config)
            server.run(sockets=[listener])
            if not server.started:
                parser.exit(1)
        return
    ConfigValidator(settings).enforce_or_exit()
    uvicorn.run('backend.main:app', host=settings.backend_host, port=settings.backend_port, reload=args.reload)


if __name__ == '__main__':
    main()
