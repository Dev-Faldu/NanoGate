"""Authentication + server-side identity + spoof rejection."""
import json

from conftest import auth


def _chat(client, key, headers=None, content="hello"):
    h = auth(key) if key else {}
    h.update(headers or {})
    return client.post("/v1/chat/completions", headers=h, json={"model": "nanogate-auto", "messages": [{"role": "user", "content": content}]})


def test_missing_key(client):
    r = _chat(client, None)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_INVALID"
    assert r.headers["x-nanogate-receipt-id"].startswith("rcpt_")


def test_invalid_key(client):
    r = _chat(client, "ng_live_deadbeef_notarealkey000000000000")
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID"


def test_revoked_key(client, svc):
    kid, raw = svc.keys.create("acme", "it", "member", ["chat"], label="to-revoke")
    svc.keys.revoke(kid)
    r = _chat(client, raw)
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REVOKED"


def test_expired_key(client, svc):
    kid, raw = svc.keys.create("acme", "it", "member", ["chat"], label="to-expire")
    svc.keys.expire_now(kid)
    r = _chat(client, raw)
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_EXPIRED"


def test_insufficient_scope(client, svc):
    _, raw = svc.keys.create("acme", "it", "member", ["read"], label="no-chat")
    r = _chat(client, raw)
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"


def test_tenant_spoof_rejected(client, keys, svc):
    r = _chat(client, keys["acme/it"], {"X-Tenant": "globex"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "TENANT_SPOOF_REJECTED"
    rec = svc.receipts.get(r.headers["x-nanogate-receipt-id"])
    assert rec["reason"] == "TENANT_SPOOF_REJECTED"
    assert rec["body"]["identity"]["tenant_id"] == "acme"   # identity from key, not header


def test_department_spoof_rejected(client, keys):
    r = _chat(client, keys["acme/it"], {"X-Department": "hr"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "DEPARTMENT_SPOOF_REJECTED"


def test_role_spoof_rejected(client, keys):
    r = _chat(client, keys["acme/it"], {"X-Role": "admin"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "DEPARTMENT_SPOOF_REJECTED"


def test_matching_identity_header_is_not_an_attack(client, keys):
    r = _chat(client, keys["acme/it"], {"X-Tenant": "acme", "X-Department": "it"}, content="my password is Hunter2!x9z")
    # passes identity; blocked later for the secret, proving identity stage succeeded
    assert r.json()["error"]["code"] == "SECRET_BLOCKED"


def test_raw_keys_never_stored(svc, keys):
    rows = svc.db.all("SELECT * FROM api_keys")
    blob = json.dumps(rows)
    for raw in keys.values():
        assert raw not in blob


def test_dashboard_requires_admin(client, keys):
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", headers=auth(keys["acme/it"])).status_code == 403
    tok = client.post("/api/session", json={"api_key": keys["admin"]}).json()["token"]
    assert client.get("/api/overview", headers=auth(tok)).status_code == 200
    assert client.post("/api/session", json={"api_key": keys["acme/it"]}).status_code == 403
