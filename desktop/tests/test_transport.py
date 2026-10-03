"""Network client behavior measured by an independent local HTTP server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
import unittest

from app.api.errors import ApiError, NetworkError, Unauthorized
from app.api.transport import Transport


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.reply = lambda path, headers, body: (200, {"ok": True})
        test = self

        class Handler(BaseHTTPRequestHandler):
            def handle_request(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                test.requests.append((self.path, dict(self.headers), body))
                status, payload = test.reply(self.path, self.headers, body)
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            do_GET = do_POST = do_PATCH = do_DELETE = handle_request

            def log_message(self, *arguments):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.transport = Transport(f"http://127.0.0.1:{self.server.server_port}")

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_refresh_retries_exact_request_without_losing_idempotency_or_version(self):
        self.transport.set_tokens("expired", "refresh-1")

        def reply(path, headers, body):
            if path == "/auth/refresh":
                self.assertEqual(json.loads(body), {"refresh": "refresh-1"})
                return 200, {"access": "renewed", "refresh": "refresh-2"}
            if headers.get("Authorization") == "Bearer expired":
                return 401, {"title": "Expired"}
            return 200, {"id": 42}

        self.reply = reply
        result = self.transport.post("/orders", {"qty": 2}, if_match=3, idempotency_key="one-order")
        self.assertEqual(result, {"id": 42})
        original, refresh, repeated = self.requests
        self.assertEqual(original[2], repeated[2])
        for header in ("Idempotency-Key", "If-Match"):
            self.assertEqual(original[1][header], repeated[1][header])
        self.assertEqual(repeated[1]["Authorization"], "Bearer renewed")
        self.assertEqual(self.transport.refresh_token, "refresh-2")

    def test_failed_refresh_clears_session_and_does_not_loop(self):
        self.transport.set_tokens("expired", "invalid")
        self.reply = lambda *args: (401, {"title": "Expired"})
        with self.assertRaises(Unauthorized):
            self.transport.get("/orders")
        self.assertEqual(len(self.requests), 2)
        self.assertFalse(self.transport.authorized)
        self.assertIsNone(self.transport.refresh_token)

    def test_xml_bytes_and_query_parameters_survive_transport(self):
        payload = "<Товар>Труба</Товар>".encode()
        self.reply = lambda *args: (200, payload)
        result = self.transport.request("POST", "/catalog/import", raw=payload,
                                        content_type="application/xml", accept="application/xml",
                                        params={"archived": False, "status": ["one", "two"], "q": "Труба"})
        self.assertEqual(result, payload)
        path, headers, body = self.requests[0]
        self.assertIn("archived=false", path)
        self.assertIn("status=one&status=two", path)
        self.assertEqual(body, payload)
        self.assertEqual(headers["Content-Type"], "application/xml")

    def test_non_json_response_is_reported_as_api_error(self):
        self.reply = lambda *args: (200, b"<html>proxy error</html>")
        with self.assertRaises(ApiError):
            self.transport.get("/orders")

    def test_timeout_is_reported_and_logout_still_clears_local_tokens(self):
        def reply(*args):
            time.sleep(0.15)
            return 200, {}

        self.reply = reply
        with self.assertRaises(NetworkError):
            self.transport.request("GET", "/health", timeout=0.02)
        self.transport.set_tokens("access", "refresh")
        self.transport.timeout = 0.02
        self.transport.logout()
        self.assertFalse(self.transport.authorized)

    def test_connection_refused_is_reported_as_network_error(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        transport = Transport(f"http://127.0.0.1:{port}")
        with self.assertRaises(NetworkError):
            transport.get("/health")
