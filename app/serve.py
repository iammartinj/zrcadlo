"""Spousteni aplikace. Host a port bere z config.json.

Server bezi ve vlakne, okno drzi hlavni vlakno. Zavrenim okna se ukonci
i server. Kdyz nativni okno nejde otevrit, aplikace se otevre v prohlizeci
a rekne proc.
"""
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from .config import CFG

TITLE = "Zrcadlo"
WIN_W, WIN_H = 1440, 900
MIN_W, MIN_H = 1000, 640


class _Server(uvicorn.Server):
    """Uvicorn ve vlakne si nesmi sahat na signaly hlavniho vlakna."""

    def install_signal_handlers(self):
        pass


def _wait_for_port(host, port, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.15)
    return False


def _window_size(webview):
    """Okno se vejde i na mensi obrazovku."""
    try:
        screen = webview.screens[0]
        w = max(MIN_W, min(WIN_W, screen.width - 80))
        h = max(MIN_H, min(WIN_H, screen.height - 100))
        return w, h
    except Exception:
        return WIN_W, WIN_H


def main():
    host = CFG["server"]["host"]
    port = int(CFG["server"]["port"])
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = "http://" + connect_host + ":" + str(port) + "/"

    server = _Server(uvicorn.Config("app.main:app", host=host, port=port,
                                    log_level="info"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not _wait_for_port(connect_host, port):
        print("Server se nepodařilo nastartovat na " + url)
        return 1
    print("Zrcadlo běží na " + url)

    try:
        import webview
    except ImportError:
        print("Balík pywebview chybí, otevírám v prohlížeči.")
        print("Doinstaluj ho příkazem: .venv\\Scripts\\python.exe -m pip install pywebview")
        return _browser_fallback(url, thread)

    try:
        w, h = _window_size(webview)
        webview.create_window(TITLE, url, width=w, height=h,
                              min_size=(MIN_W, MIN_H))
        webview.start()
    except Exception as exc:
        print("Nativní okno se nepodařilo otevřít (" + type(exc).__name__ + ": " +
              str(exc) + "), otevírám v prohlížeči.")
        print("Na Windows k němu potřebuješ WebView2 runtime od Microsoftu.")
        return _browser_fallback(url, thread)

    server.should_exit = True
    thread.join(timeout=5)
    return 0


def _browser_fallback(url, thread):
    """Okno nevzniklo. Aplikace pojede v prohlizeci, server drzi konzole."""
    webbrowser.open(url)
    print("Zavři tohle okno, až budeš chtít Zrcadlo ukončit.")
    try:
        while thread.is_alive():
            thread.join(1.0)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
