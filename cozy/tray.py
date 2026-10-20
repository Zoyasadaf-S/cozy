"""Optional system-tray icon (pip install pystray pillow). Not covered by automated tests."""
from __future__ import annotations

import webbrowser


def run_tray(settings, app, server) -> None:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("Tray needs: pip install pystray pillow  (running without it)")
        server.run()
        return
    ctx = app.state.ctx
    image = Image.new("RGB", (64, 64), "#0b0f14")
    ImageDraw.Draw(image).ellipse((12, 12, 52, 52), fill="#3ddc97")

    def toggle_offline(icon, item):
        ctx.set_setting("offline", not ctx.offline)

    def quit_app(icon, item):
        server.should_exit = True
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Cozy", lambda i, it: webbrowser.open(f"http://127.0.0.1:{settings.port}/"), default=True),
        pystray.MenuItem("Offline mode", toggle_offline, checked=lambda item: ctx.offline),
        pystray.MenuItem("Quit", quit_app),
    )
    pystray.Icon("cozy", image, "Cozy", menu).run()
