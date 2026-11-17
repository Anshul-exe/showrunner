import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from catalog_api.vendor import VendorClient, VendorError


class Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def do_GET(self):  # noqa: N802
        if Handler.mode == "slow":
            import time
            time.sleep(0.5)
        status = 503 if Handler.mode == "error" else 200
        self.send_response(status)
        self.end_headers()
        body = {"recs": ["t02", "t03"], "customer": self.headers.get("x-customer")}
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_ok(server):
    Handler.mode = "ok"
    assert VendorClient(server, "acme-tv", 1).recommendations("t01") == ["t02", "t03"]


def test_http_error(server):
    Handler.mode = "error"
    with pytest.raises(VendorError, match="503"):
        VendorClient(server, "acme-tv", 1).recommendations("t01")


def test_timeout_is_enforced(server):
    Handler.mode = "slow"
    with pytest.raises(VendorError, match="unreachable"):
        VendorClient(server, "acme-tv", 0.1).recommendations("t01")
