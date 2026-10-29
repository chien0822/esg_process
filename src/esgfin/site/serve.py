"""以 Python 內建 http.server 在 127.0.0.1 提供 site/（SPEC 3.1、12.3）。"""

from __future__ import annotations

import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"  # 只綁本機


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def serve(site_dir: Path, port: int) -> None:
    if not (site_dir / "index.html").exists():
        raise FileNotFoundError("site/index.html 不存在，請先執行 python run.py")
    handler = functools.partial(_QuietHandler, directory=str(site_dir))
    try:
        server = ThreadingHTTPServer((HOST, port), handler)
    except OSError as e:
        raise OSError(f"無法使用 port {port}（可能已被占用），請修改 config/settings.yaml 的 site.port：{e}") from e
    print(f"結果網頁：http://{HOST}:{port}/")
    print("按 Ctrl+C 停止伺服器")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
