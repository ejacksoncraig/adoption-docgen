"""The same application, served to a browser on this machine.

    python -m app.server

Everything that matters happens exactly where it did before: Python renders the
documents and writes them to output/, and LibreOffice makes the PDFs. The browser
only draws the screen. Nothing is uploaded anywhere — the "server" is a loopback
socket talking to the program already running on this computer.

Why it exists: editing app/ui/index.html and pressing refresh beats rebuilding a
window every time. It is a development and testing convenience, not a step
towards hosting this anywhere. See "Not a step towards hosting" below.

## What is different from the desktop window

A web page cannot open a native Save or Open dialog, so:

  * saving an intake writes to intake/ under its usual name, and says where;
  * opening an intake, or importing a client's form, uses the browser's own file
    picker and copies the file in;
  * a questionnaire is written to output/.

Generation, validation and PDF export are the same code either way.

## Not a step towards hosting

The socket binds to 127.0.0.1 — the loopback address, which nothing outside this
machine can reach. That is deliberate and it is not the whole defence: any web
page you happen to have open can try to POST to a localhost port, so every call
must carry a token this program generated at startup and handed to its own page.
Requests from anywhere else are refused.

None of that makes this safe to expose. Putting adoption records on a network
needs authentication, transport security, access logging and a security review —
a separate piece of work, as PROJECT_BRIEF.md says.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app.main import Api
from app.registry import UI_DIR, Registry
from app.schema import ConfigError

HOST = "127.0.0.1"
DEFAULT_PORT = 8765

#: Uploaded copies of files the browser handed us. Gitignored with the rest of
#: intake/, because a client's completed form is client data.
INCOMING = "incoming"

#: Anything larger than this is not an intake file or a form export.
MAX_UPLOAD = 25 * 1024 * 1024


def free_port(preferred: int = DEFAULT_PORT) -> int:
    """The preferred port if it is free, otherwise whatever the OS offers."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((HOST, candidate))
                return probe.getsockname()[1]
            except OSError:
                continue
    raise SystemExit("No free port to listen on.")


class BrowserApi(Api):
    """The desktop bridge, with the native file dialogs swapped for the browser's.

    Everything else is inherited unchanged, so the two front ends cannot drift
    apart: there is one implementation of generate, review and import.
    """

    #: Tells the page which affordances to offer — a native dialog, or its own
    #: file picker.
    mode = "browser"

    def upload(self, payload: dict) -> dict:
        """Take a file the browser picked and put it where the engine can read it.

        A copy, not a transfer: the file came from this machine and stays on it.
        """
        try:
            name = Path(str(payload.get("name") or "upload")).name
            blob = base64.b64decode(payload.get("data") or "", validate=True)
        except Exception:
            return {"ok": False, "problems": ["That file could not be read."]}

        if not blob:
            return {"ok": False, "problems": ["That file is empty."]}
        if len(blob) > MAX_UPLOAD:
            return {"ok": False, "problems": [
                f"{name} is larger than {MAX_UPLOAD // (1024 * 1024)} MB, which is far bigger "
                f"than any intake file or form export. Check it is the right file."
            ]}

        from app.registry import INTAKE_DIR

        folder = INTAKE_DIR / INCOMING
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        target.write_bytes(blob)
        return {"ok": True, "path": str(target), "name": name}


def page_with_token(token: str) -> bytes:
    """index.html, told the token it must send back with every call."""
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    inject = f'<script>window.DOCGEN_TOKEN = "{token}";</script>\n</head>'
    return html.replace("</head>", inject, 1).encode("utf-8")


def make_handler(api: BrowserApi, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AdoptionFilingGenerator"
        protocol_version = "HTTP/1.1"

        # -- plumbing --------------------------------------------------------

        def log_message(self, *_args) -> None:
            """Silence per-request logging: the URLs carry matter and file names."""

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # The page is regenerated on every load; never let a stale copy stick,
            # or an edit to index.html appears not to have worked.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

        def _authorised(self) -> bool:
            """Only this program's own page may call the API.

            The token stops another local program guessing its way in. The Origin
            check stops a web page you have open elsewhere from POSTing here — the
            custom header already forces a cross-origin preflight, which fails, and
            this is the belt to that pair of braces.

            Same origin is judged against the address the request actually came in
            on, not a hardcoded "localhost". Reached through a forwarded port — a
            Codespace, say — the page is served from that forwarded name, and
            insisting on loopback would refuse the application's own page.
            """
            if not secrets.compare_digest(self.headers.get("X-Docgen-Token", ""), token):
                return False

            origin = self.headers.get("Origin")
            if origin is None:
                return True
            sender = urlparse(origin)
            if sender.netloc.lower() == (self.headers.get("Host") or "").lower():
                return True
            return sender.hostname in {HOST, "localhost", "::1"}

        # -- routes ----------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 - the base class names it
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, page_with_token(token), "text/html; charset=utf-8")
                return

            asset = (UI_DIR / path.lstrip("/")).resolve()
            if UI_DIR.resolve() in asset.parents and asset.is_file():
                kind = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
                self._send(200, asset.read_bytes(), kind)
                return
            self._send(404, b"Not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not path.startswith("/api/"):
                self._send(404, b"Not found", "text/plain")
                return
            if not self._authorised():
                self._json(403, {"ok": False, "problems": [
                    "This request did not come from the application's own window."
                ]})
                return

            method = path[len("/api/"):]
            handler = getattr(api, method, None)
            if not callable(handler) or method.startswith("_"):
                self._json(404, {"ok": False, "problems": [f"No such action: {method}"]})
                return

            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"ok": False, "problems": ["The request was not readable."]})
                return

            self._json(200, handler(payload))

    return Handler


def in_codespace() -> bool:
    import os

    return os.environ.get("CODESPACES") == "true"


def serve(port: int | None = None, open_browser: bool = True, host: str = HOST) -> int:
    try:
        registry = Registry.load()
    except ConfigError as exc:
        print("Configuration is not valid. The application cannot start.\n", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    from app.registry import INTAKE_DIR, OUTPUT_DIR

    for directory in (OUTPUT_DIR, INTAKE_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    api = BrowserApi(registry)
    token = secrets.token_urlsafe(24)
    port = port or free_port()
    url = f"http://{host}:{port}/"

    httpd = ThreadingHTTPServer((host, port), make_handler(api, token))
    print(f"Adoption Filing Generator — open {url}")
    if host == HOST:
        print("  Serving to this computer only. Press Ctrl+C to stop.")
    else:
        print(f"  Listening on {host}. Anything that can reach this machine on port")
        print("  {port} can reach the application. Press Ctrl+C to stop.".format(port=port))
    if in_codespace():
        print("  In a Codespace: open the Ports tab and click the globe beside this port.")
    if not registry.settings.get("attorney_short_name"):
        print("  note: config/settings.json has no attorney details yet.")

    # Opening a browser inside a container puts a window nobody can see on a
    # machine nobody is sitting at.
    if open_browser and not in_codespace():
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="app.server", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, help=f"port to listen on (default: {DEFAULT_PORT}, or any free one)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    parser.add_argument(
        "--host", default=HOST,
        help="interface to bind (default: %(default)s, this computer only). Use 0.0.0.0 only "
             "inside a container whose ports are forwarded for you, never on a real network.",
    )
    args = parser.parse_args(argv)
    return serve(args.port, open_browser=not args.no_browser, host=args.host)


if __name__ == "__main__":
    raise SystemExit(main())
