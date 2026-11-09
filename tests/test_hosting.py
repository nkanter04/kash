"""The shared online copy: password gate and what's switched off there."""
import asyncio
import base64
import json

import pytest

import app as kash
import people


class Reply:
    def __init__(self, status, headers, body):
        self.status_code, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body)


class Client:
    """Sends one GET straight into the app, so no HTTP server or test-client package is needed
    (and the startup data warm-up doesn't run)."""

    def get(self, target, headers=None):
        path, _, query = target.partition("?")
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": query.encode(), "root_path": "", "client": ("test", 1), "server": ("test", 80),
                 "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]}
        sent, out = False, {"status": 0, "headers": {}, "body": b""}

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.sleep(3600)       # the app cancels this once the response is done

        async def send(m):
            if m["type"] == "http.response.start":
                out["status"], out["headers"] = m["status"], {k.decode().lower(): v.decode() for k, v in m["headers"]}
            elif m["type"] == "http.response.body":
                out["body"] += m.get("body", b"")

        asyncio.run(kash.app(scope, receive, send))
        return Reply(out["status"], out["headers"], out["body"])


@pytest.fixture
def client(monkeypatch):
    for k in ("KASH_PASSWORD", "RENDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(people, "overview", lambda g=None: {"ok": True})
    return Client()


def auth(pw, user="family"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_running_locally_needs_no_password_and_keeps_whos_trading(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/people").json() == {"ok": True}
    assert client.get("/api/config").json() == {"people": True}


def test_password_gate(client, monkeypatch):
    monkeypatch.setenv("KASH_PASSWORD", "open sesame")
    r = client.get("/")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert client.get("/static/app.js").status_code == 401
    assert client.get("/", headers=auth("wrong")).status_code == 401
    assert client.get("/", headers={"Authorization": "Basic not-base64!"}).status_code == 401
    assert client.get("/", headers=auth("open sesame")).status_code == 200
    assert client.get("/", headers=auth("open sesame", user="anyone")).status_code == 200


def test_health_check_stays_open_for_the_host(client, monkeypatch):
    monkeypatch.setenv("KASH_PASSWORD", "x")
    assert client.get("/healthz").status_code == 200


def test_host_without_a_password_refuses_to_serve(client, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    assert client.get("/").status_code == 503
    assert client.get("/api/stock/AAPL").status_code == 503


@pytest.mark.parametrize("path", ["/api/people", "/api/people/search?q=pelosi", "/api/people/stock/AAPL", "/api/people/h:x:y"])
def test_whos_trading_is_off_on_the_shared_copy(client, monkeypatch, path):
    monkeypatch.setenv("KASH_PASSWORD", "pw")
    monkeypatch.setenv("RENDER", "true")
    assert client.get(path, headers=auth("pw")).status_code == 404


def test_shared_copy_reports_the_feature_off(client, monkeypatch):
    monkeypatch.setenv("KASH_PASSWORD", "pw")
    assert client.get("/api/config", headers=auth("pw")).json() == {"people": False}
