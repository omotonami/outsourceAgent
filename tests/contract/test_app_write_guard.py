"""Review PR #2 (2026-11-04): POST ghi (gate dự án, escalation, demo) chỉ nhận Content-Type application/json từ
Host/Origin cục bộ (hoặc dashboard.allowed_hosts) — chống CSRF / DNS rebinding. Sai → 403 (Content-Type → 415)."""
import pytest

from src.blackboard.eventstore import EventStore
from src.server import app as srv
from tests.contract.test_ops_escalation import _iso, _state, db, pid  # noqa: F401

tc = pytest.importorskip("fastapi.testclient")
BODY = {"signer": "Lan Nguyen", "typed_confirm": "XAC NHAN"}


@pytest.fixture
def mk(monkeypatch, db):
    monkeypatch.setattr(srv, "EVENTSTORE", EventStore(db, readonly=True))
    monkeypatch.setattr(srv, "GATE_STORE", EventStore(db))
    return lambda base="http://localhost": tc.TestClient(srv.app, base_url=base)


@pytest.mark.parametrize("path", ["/api/projects/{pid}/escalation/abandon", "/api/projects/{pid}/gate/G1/approve",
                                  "/api/step", "/api/reset", "/api/gate/approve"])
@pytest.mark.parametrize("base,headers,code", [
    ("http://evil.example", {}, 403),                                    # Host lạ (DNS rebinding)
    ("http://localhost", {"Origin": "http://evil.example"}, 403),        # Origin lạ (CSRF)
    ("http://localhost", {"Origin": "null"}, 403),
    ("http://localhost", {"Origin": "file://x"}, 403),
    ("http://localhost", {"Content-Type": "text/plain"}, 415),
    ("http://localhost", {"Content-Type": "application/x-www-form-urlencoded"}, 415),
])
def test_bad_origin_host_or_content_type_refused(mk, db, pid, path, base, headers, code):
    n = EventStore(db).last_seq(pid)
    c = mk(base)
    import json
    h = {"Content-Type": "application/json", **headers}
    r = c.post(path.format(pid=pid), content=json.dumps(BODY), headers=h)
    assert r.status_code == code, r.text
    assert EventStore(db).last_seq(pid) == n and _state(db, pid) == "ESCALATED"


@pytest.mark.parametrize("base,origin", [("http://localhost:8000", "http://localhost:8000"),
                                         ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
                                         ("http://127.0.0.1:8000", None)])          # curl không gửi Origin
def test_local_origin_or_missing_origin_with_local_host_passes(mk, db, pid, base, origin):
    h = {"Origin": origin} if origin else {}
    r = mk(base).post(f"/api/projects/{pid}/escalation/abandon", json=BODY, headers=h)
    assert r.status_code == 200, r.text
    assert _state(db, pid) == "ABANDONED"


def test_allowed_hosts_from_config(mk, db, pid, monkeypatch):
    real = srv.config.load_yaml

    def fake(rel, root=None):
        d = real(rel, root)
        return {**d, "dashboard": {"allowed_hosts": ["dash.noibo.local"]}} if str(rel) == "config/runtime.yaml" else d
    monkeypatch.setattr(srv.config, "load_yaml", fake)
    r = mk("http://dash.noibo.local").post(f"/api/projects/{pid}/escalation/abandon", json=BODY,
                                           headers={"Origin": "http://dash.noibo.local"})
    assert r.status_code == 200, r.text


def test_get_not_affected(mk, pid):
    assert mk("http://evil.example").get(f"/api/projects/{pid}/escalation").status_code == 200


def test_write_refusal_unit():
    assert srv.write_refusal("GET", {"host": "evil"}) is None
    assert srv.write_refusal("POST", {"host": "localhost:8000", "content-type": "application/json; charset=utf-8"}) is None
    assert srv.write_refusal("POST", {"host": "[::1]:8000", "content-type": "application/json"}) is None
    assert srv.write_refusal("POST", {"content-type": "application/json"})[0] == 403          # thiếu Host
    assert srv.write_refusal("DELETE", {"host": "localhost.evil.com", "content-type": "application/json"})[0] == 403
