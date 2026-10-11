"""Loopback-only local web API and static UI for the Star index."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import search
from service import IndexService, ROOT as PROJECT_ROOT


_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/icons.js": ("icons.js", "text/javascript; charset=utf-8"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/fonts/Geist-Variable.woff2": ("fonts/Geist-Variable.woff2", "font/woff2"),
}
_MAX_BODY_BYTES = 64 * 1024
_MAX_URL_CHARS = 8192
_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class LocalIndexHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: IndexService) -> None:
        self.index_service = service
        super().__init__(address, LocalIndexRequestHandler)


class LocalIndexRequestHandler(BaseHTTPRequestHandler):
    server: LocalIndexHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "StarIndexLocal/0.1"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        # Keep request logs out of the protocol/API response and avoid echoing
        # user-provided query strings into the terminal.
        sys.stderr.write("star-index-web: request handled\n")

    @property
    def service(self) -> IndexService:
        return self.server.index_service

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data: https://avatars.githubusercontent.com; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("Cache-Control", "no-store")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, code: str, message: str) -> None:
        # Several POST errors are returned before consuming the body. Closing
        # the HTTP/1.1 connection prevents those bytes being parsed as a new
        # request on the same socket.
        self.close_connection = True
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _host_is_local(self) -> bool:
        raw_host = self.headers.get("Host", "")
        if not raw_host or len(raw_host) > 255 or any(ch in raw_host for ch in "\r\n/@"):
            return False
        try:
            parsed = urlsplit("//" + raw_host)
            host = (parsed.hostname or "").casefold()
            port = parsed.port
        except ValueError:
            return False
        if host not in _ALLOWED_HOSTS or parsed.username or parsed.password or parsed.path:
            return False
        bound_port = int(self.server.server_address[1])
        return port == bound_port if port is not None else bound_port == 80

    def _origin_is_local(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        if not origin or len(origin) > 512 or origin.casefold() == "null":
            return False
        try:
            parsed = urlsplit(origin)
            host = (parsed.hostname or "").casefold()
            port = parsed.port
        except ValueError:
            return False
        if (
            parsed.scheme.casefold() != "http"
            or host not in _ALLOWED_HOSTS
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return False
        bound_port = int(self.server.server_address[1])
        effective_port = port if port is not None else 80
        return effective_port == bound_port

    def _request_is_local(self) -> bool:
        if not self._host_is_local() or not self._origin_is_local():
            self._error(403, "forbidden", "Requests must use this local app's origin.")
            return False
        return True

    @staticmethod
    def _single(query: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
        values = query.get(key)
        if values is None:
            return default
        if len(values) != 1:
            raise ValueError(f"{key} must appear once")
        return values[0]

    @staticmethod
    def _int_param(query: dict[str, list[str]], key: str, default: int) -> int:
        value = LocalIndexRequestHandler._single(query, key)
        if value is None:
            return default
        if not value.isascii() or not value.isdecimal() or len(value) > 10:
            raise ValueError(f"{key} must be a positive integer")
        return int(value)

    @staticmethod
    def _archived_param(query: dict[str, list[str]]) -> bool | None:
        value = LocalIndexRequestHandler._single(query, "archived", "any")
        if value is None or value.casefold() == "any":
            return None
        if value.casefold() == "true":
            return True
        if value.casefold() == "false":
            return False
        raise ValueError("archived must be true, false, or any")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._request_is_local():
            return
        raw_target = self.path
        if len(raw_target) > _MAX_URL_CHARS:
            self._error(414, "uri_too_long", "Request URL is too long.")
            return
        parsed = urlsplit(raw_target)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
            self._error(400, "bad_request", "Malformed request path.")
            return

        if parsed.path in _STATIC_FILES:
            self._serve_static(parsed.path)
            return
        if parsed.path.startswith("/api/"):
            self._serve_api(parsed.path, parsed.query)
            return
        self._error(404, "not_found", "Route not found.")

    def _serve_static(self, route: str) -> None:
        filename, content_type = _STATIC_FILES[route]
        static_root = (PROJECT_ROOT / "web").resolve()
        target = static_root / filename
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(static_root)
            body = resolved.read_bytes()
        except FileNotFoundError:
            self._error(503, "ui_not_ready", "The web interface files are not installed yet.")
            return
        except (OSError, ValueError):
            self._error(404, "not_found", "Static resource is unavailable.")
            return
        self._send_bytes(200, body, content_type)

    def _serve_api(self, route: str, raw_query: str) -> None:
        try:
            query = parse_qs(raw_query, keep_blank_values=True, strict_parsing=False, max_num_fields=32)
            if route == "/api/info":
                if query:
                    raise ValueError("/api/info does not accept query parameters")
                self._send_json(200, self.service.info())
                return
            if route == "/api/search":
                q = self._single(query, "q", "") or ""
                if not q.strip():
                    raise ValueError("q is required")
                extras: list[str] = []
                for item in query.get("extra_terms", []):
                    extras.extend(term.strip() for term in item.split(",") if term.strip())
                if len(extras) > 20:
                    raise ValueError("at most 20 extra_terms are allowed")
                result = self.service.search(
                    q,
                    limit=self._int_param(query, "limit", 12),
                    kind=self._single(query, "kind"),
                    language=self._single(query, "language"),
                    archived=self._archived_param(query),
                    evidence_level=self._single(query, "evidence_level"),
                    extra_terms=extras,
                )
                self._send_json(200, result)
                return
            if route == "/api/repos":
                result = self.service.list(
                    limit=self._int_param(query, "limit", 12),
                    offset=self._int_param(query, "offset", 0),
                    kind=self._single(query, "kind"),
                    language=self._single(query, "language"),
                    archived=self._archived_param(query),
                    evidence_level=self._single(query, "evidence_level"),
                )
                self._send_json(200, result)
                return
            if route == "/api/repo":
                name = self._single(query, "name", "") or ""
                if not name:
                    raise ValueError("name is required")
                self._send_json(200, self.service.show(name))
                return
            self._error(404, "not_found", "API route not found.")
        except ValueError as exc:
            self._error(400, "bad_request", str(exc))
        except search.IndexErrorBase as exc:
            if "not found" in str(exc).casefold():
                self._error(404, "not_found", "Repository not found in this index.")
            else:
                self._error(503, "index_unavailable", "The local index is unavailable. Rebuild it with python search.py build.")
        except (sqlite3.Error, OSError) as exc:
            sys.stderr.write(f"star-index-web: index access failed ({type(exc).__name__})\n")
            self._error(503, "index_unavailable", "The local index is unavailable. Rebuild it with python search.py build.")
        except Exception as exc:  # Keep internals and local paths out of HTTP responses.
            sys.stderr.write(f"star-index-web: request failed ({type(exc).__name__})\n")
            self._error(500, "internal_error", "The local request could not be completed.")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._request_is_local():
            return
        parsed = urlsplit(self.path)
        if parsed.path != "/api/compare" or parsed.query or parsed.fragment:
            self._error(404, "not_found", "API route not found.")
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type != "application/json":
            self._error(415, "unsupported_media_type", "Content-Type must be application/json.")
            return
        if self.headers.get("Transfer-Encoding"):
            self._error(400, "bad_request", "Transfer-Encoding is not supported.")
            return
        length_header = self.headers.get("Content-Length")
        if (
            not length_header
            or not length_header.isascii()
            or not length_header.isdecimal()
            or len(length_header) > 10
        ):
            self._error(411, "length_required", "A valid Content-Length header is required.")
            return
        length = int(length_header)
        if length > _MAX_BODY_BYTES:
            self._error(413, "payload_too_large", "Request body is too large.")
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                self._error(400, "bad_request", "Request body was incomplete.")
                return
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"repositories"}:
                raise ValueError("body must contain only a repositories array")
            result = self.service.compare(payload["repositories"])
            self._send_json(200, result)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(400, "bad_request", "Request body must be valid UTF-8 JSON.")
        except ValueError as exc:
            self._error(400, "bad_request", str(exc))
        except search.IndexErrorBase as exc:
            if "not found" in str(exc).casefold():
                self._error(404, "not_found", "One or more repositories are not available in this index.")
            else:
                self._error(503, "index_unavailable", "The local index is unavailable. Rebuild it with python search.py build.")
        except (sqlite3.Error, OSError) as exc:
            sys.stderr.write(f"star-index-web: index access failed ({type(exc).__name__})\n")
            self._error(503, "index_unavailable", "The local index is unavailable. Rebuild it with python search.py build.")
        except Exception as exc:
            sys.stderr.write(f"star-index-web: request failed ({type(exc).__name__})\n")
            self._error(500, "internal_error", "The local request could not be completed.")


def _make_service(args: argparse.Namespace) -> IndexService:
    if args.demo:
        if args.root or args.db:
            raise ValueError("--demo cannot be combined with --root or --db")
        demo = IndexService(root=PROJECT_ROOT / "data" / "demo", public_only=True)
        demo.mode_override = "demo"
        if not demo._index_path().is_file():
            raise FileNotFoundError("Demo index is not installed; run python demo.py")
        return demo
    return IndexService(root=args.root or PROJECT_ROOT, db_path=args.db, public_only=args.public_only)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the local GitHub Star index on loopback.")
    parser.add_argument("--port", type=int, default=8765, help="loopback TCP port (default: 8765)")
    parser.add_argument("--root", help="index project root")
    parser.add_argument("--db", help="SQLite index path (relative to --root unless absolute)")
    parser.add_argument("--public-only", action="store_true", help="expose only repositories with private=false")
    parser.add_argument("--demo", action="store_true", help="serve the sanitized demo index")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        service = _make_service(args)
        if not service._index_path().is_file():
            raise FileNotFoundError("Index not found; run python search.py build")
    except (ValueError, OSError, search.IndexErrorBase) as exc:
        sys.stderr.write(f"star-index-web: {exc}\n")
        return 2

    try:
        server = LocalIndexHTTPServer(("127.0.0.1", args.port), service)
    except OSError as exc:
        sys.stderr.write(f"star-index-web: could not bind loopback port {args.port} ({type(exc).__name__})\n")
        return 2
    host, port = server.server_address
    sys.stderr.write(f"Star index is available at http://{host}:{port}/ ({service.mode} mode).\n")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        sys.stderr.write("star-index-web: stopping\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

