"""python -m cozy [--phone] [--port 8000] [--tray]"""
from __future__ import annotations

import argparse
import logging
import os
import threading

from .config import get_settings, lan_ip, load_token


def main() -> None:
    parser = argparse.ArgumentParser(prog="cozy")
    parser.add_argument("--phone", action="store_true", help="allow your phone on the same Wi-Fi (token required)")
    parser.add_argument("--port", type=int)
    parser.add_argument("--tray", action="store_true", help="show a system-tray icon (needs pystray + pillow)")
    args = parser.parse_args()
    if args.phone:
        os.environ["COZY_LAN"] = "1"
    if args.port:
        os.environ["COZY_PORT"] = str(args.port)
    settings = get_settings()

    import uvicorn

    from .server import create_app

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app = create_app(settings)
    token = load_token(settings.data_dir)
    print(f"\n  Cozy 2.0 is running\n  On this laptop : http://127.0.0.1:{settings.port}/")
    if settings.lan:
        print(f"  On your phone  : http://{lan_ip()}:{settings.port}/?t={token}   (same Wi-Fi; open this link once)")
        print("  Note: traffic on your home Wi-Fi is not encrypted. Don't use --phone on public networks.")
    else:
        print("  Phone access is off. Start with --phone (or start_phone.bat) to enable it.")
    print("  Chrome extension: load the 'extension' folder at chrome://extensions (Developer mode).\n")

    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    if args.tray:
        from .tray import run_tray
        threading.Thread(target=server.run, daemon=True).start()
        run_tray(settings, app, server)
    else:
        server.run()


if __name__ == "__main__":
    main()
