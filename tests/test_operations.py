"""Operations: keys & departments, roles, audit log, alerts, retention & erasure (chain stays verifiable), backups,
exports, chargeback, company knowledge, assistant proposals, employee chat sessions."""
import base64
import csv
import io
import json
import tarfile
import time

import pytest

from conftest import auth, needs_model


def admin_h(client, keys):
    tok = client.post("/api/session", json={"api_key": keys["admin"]}).json()["token"]
    return auth(tok)


def test_org_lists_tenants_and_policies(client, keys):
    j = client.get("/api/org", headers=admin_h(client, keys)).json()
    acme = next(t for t in j["tenants"] if t["tenant_id"] == "acme")
    assert {"hr", "it"} <= {d["department_id"] for d in acme["departments"]}
    assert "hr" in j["policies"]


def test_create_department_then_key_then_revoke(client, keys, svc):
    h = admin_h(client, keys)
    r = client.post("/api/org/departments", headers=h,
                    json={"tenant_id": "acme", "department_id": "finance", "display_name": "Finance", "policy_id": "hr"})
    assert r.status_code == 200, r.text
    assert svc.policies.policy_for("acme", "finance") == "hr"
    dup = client.post("/api/org/departments", headers=h,
                      json={"tenant_id": "acme", "department_id": "finance", "display_name": "x", "policy_id": "hr"})
    assert dup.status_code == 422
    k = client.post("/api/keys", headers=h, json={"tenant_id": "acme", "department_id": "finance", "kind": "app",
                                                   "name": "ledger-bot", "days": 30}).json()
    assert k["api_key"].startswith("ng_live_") and k["kind"] == "app"
    listed = client.get("/api/keys", headers=h).json()["keys"]
    row = next(x for x in listed if x["key_id"] == k["key_id"])
    assert row["state"] == "active" and "api_key" not in row and k["api_key"] not in json.dumps(listed)
    # the new key works against the OpenAI API (identity resolves to the new department)
    assert client.get("/v1/models", headers=auth(k["api_key"])).status_code == 200
    assert client.post(f"/api/keys/{k['key_id']}/revoke", headers=h).status_code == 200
    assert client.get("/v1/models", headers=auth(k["api_key"])).status_code == 401
    actions = [e["action"] for e in client.get("/api/audit", headers=h).json()["entries"]]
    assert {"department.create", "key.create", "key.revoke"} <= set(actions)
    # the raw key never reaches the audit log
    assert k["api_key"] not in json.dumps(client.get("/api/audit", headers=h).json())


def test_cannot_revoke_own_key(client, keys, svc):
    h = admin_h(client, keys)
    me = client.get("/api/me", headers=h).json()["key_id"]
    assert client.post(f"/api/keys/{me}/revoke", headers=h).status_code == 422


def test_auditor_reads_but_cannot_change(client, keys, svc):
    h = admin_h(client, keys)
    a = client.post("/api/keys", headers=h, json={"tenant_id": "acme", "department_id": "it", "kind": "auditor",
                                                   "name": "external audit"}).json()
    s = client.post("/api/session", json={"api_key": a["api_key"]})
    assert s.status_code == 200
    ah = auth(s.json()["token"])
    assert client.get("/api/me", headers=ah).json()["scopes"] == ["audit"]
    for path in ("/api/overview", "/api/requests", "/api/keys", "/api/audit", "/api/ops", "/api/alerts"):
        assert client.get(path, headers=ah).status_code == 200, path
    assert client.post("/api/keys", headers=ah, json={"tenant_id": "acme", "department_id": "it", "kind": "app",
                                                       "name": "x"}).status_code == 403
    assert client.post("/api/policies/publish", headers=ah, json={"policy_id": "it", "body": {}}).status_code == 403
    assert client.post("/api/ops/backups", headers=ah).status_code == 403
    # auditors cannot use the chat API with an audit-only key
    assert client.get("/v1/models", headers=auth(a["api_key"])).status_code == 403


def test_secret_blocked_raises_alert(client, keys, svc):
    import asyncio
    before = svc.db.one("SELECT COUNT(*) n FROM alerts WHERE rule='secret_blocked'")["n"]
    r = client.post("/v1/chat/completions", headers=auth(keys["acme/sales"]),
                    json={"messages": [{"role": "user", "content": "deploy with AKIAIOSFODNN7EXAMPLE please"}]})
    assert r.json()["error"]["code"] == "SECRET_BLOCKED"
    for _ in range(50):
        if svc.db.one("SELECT COUNT(*) n FROM alerts WHERE rule='secret_blocked'")["n"] > before:
            break
        time.sleep(0.1)
    j = client.get("/api/alerts", headers=admin_h(client, keys)).json()
    a = next(x for x in j["alerts"] if x["rule"] == "secret_blocked")
    assert a["subject"] == "acme/sales" and "AKIAIOSFODNN7EXAMPLE" not in json.dumps(a)


def test_alert_channel_url_is_not_echoed(client, keys):
    h = admin_h(client, keys)
    url = "https://hooks.example.invalid/services/T000/B000/SECRETTOKEN"
    ch = client.post("/api/alerts/channels", headers=h, json={"name": "ops", "kind": "slack", "url": url}).json()
    assert ch["host"] == "hooks.example.invalid" and "url" not in ch
    assert "SECRETTOKEN" not in client.get("/api/alerts", headers=h).text
    assert client.post("/api/alerts/channels", headers=h, json={"name": "x", "kind": "slack", "url": "ftp://x"}).status_code == 422
    assert client.delete(f"/api/alerts/channels/{ch['id']}", headers=h).status_code == 200


def test_retention_prunes_bodies_and_chain_still_verifies(client, keys, svc):
    for _ in range(3):
        client.post("/v1/chat/completions", headers=auth(keys["acme/it"]),
                    json={"messages": [{"role": "user", "content": "token=AKIAIOSFODNN7EXAMPLE"}]})
    old = svc.db.all("SELECT receipt_id, request_id FROM receipts WHERE tenant_id='acme' AND department_id='it' "
                     "AND pruned_at IS NULL ORDER BY seq LIMIT 2")
    assert len(old) == 2
    long_ago = time.time() - 400 * 86400   # IT policy keeps 365 days
    for r in old:
        svc.db.execute("UPDATE receipts SET ts=? WHERE receipt_id=?", (long_ago, r["receipt_id"]))
        svc.db.execute("UPDATE requests SET ts=? WHERE request_id=?", (long_ago, r["request_id"]))
    res = client.post("/api/ops/retention/run", headers=admin_h(client, keys)).json()
    assert res["receipts_pruned"] >= 2 and res["requests_deleted"] >= 2
    rec = svc.receipts.get(old[0]["receipt_id"])
    assert rec["body"] == {} and rec["pruned_at"]
    v = svc.receipts.verify(old[0]["receipt_id"])
    assert v["valid"] and v["checks"]["body_pruned"] and v["checks"]["hmac_valid"] and v["checks"]["chain_link_valid"]
    assert svc.receipts.verify_chain()["valid"]


def test_erasure_by_key(client, keys, svc):
    h = admin_h(client, keys)
    k = client.post("/api/keys", headers=h, json={"tenant_id": "acme", "department_id": "hr", "kind": "person",
                                                   "name": "Test Person"}).json()
    client.post("/v1/chat/completions", headers=auth(k["api_key"]),
                json={"messages": [{"role": "user", "content": "password: Hunter2!x9z"}]})
    assert svc.db.one("SELECT COUNT(*) n FROM requests WHERE key_id=?", (k["key_id"],))["n"] == 1
    assert client.post("/api/ops/erase", headers=h, json={"key_id": k["key_id"], "confirm": "nope"}).status_code == 422
    r = client.post("/api/ops/erase", headers=h, json={"key_id": k["key_id"], "confirm": "ERASE"}).json()
    assert r["requests_deleted"] == 1 and r["receipts_pruned"] == 1
    assert svc.db.one("SELECT COUNT(*) n FROM requests WHERE key_id=?", (k["key_id"],))["n"] == 0
    assert svc.receipts.verify_chain()["valid"]


def test_backup_archive_contents(client, keys, svc):
    h = admin_h(client, keys)
    b = client.post("/api/ops/backups", headers=h).json()
    p = svc.ops.backups.path(b["backup_id"])
    assert p and (p.stat().st_mode & 0o077) == 0
    with tarfile.open(p) as t:
        names = set(t.getnames())
    assert {"nanogate.db", "manifest.json", "key_pepper", "receipt_hmac.key"} <= names
    d = client.get(f"/api/ops/backups/{b['backup_id']}/download", headers=h)
    assert d.status_code == 200 and d.content[:2] == b"\x1f\x8b"


def test_exports_and_chargeback(client, keys, svc):
    h = admin_h(client, keys)
    r = client.get("/api/export/requests.csv", headers=h)
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][0] == "time_utc" and "receipt_id" in rows[0] and len(rows) > 1
    assert "raw_prompt" not in rows[0]
    lines = client.get("/api/export/receipts.jsonl", headers=h).text.strip().split("\n")
    first = json.loads(lines[0])
    assert {"hash", "prev_hash", "hmac", "body_canonical"} <= set(first)
    cb = client.get("/api/finops/chargeback", headers=h).json()
    assert cb["label"] == "Measured" and any(x["department_id"] == "it" for x in cb["rows"])
    assert client.get("/api/finops/chargeback?month=bad", headers=h).status_code == 422
    assert client.get("/api/finops/chargeback.csv", headers=h).text.startswith("month,")


def test_knowledge_upload_retrieve_revoke(client, keys, svc):
    if svc.kb is None:
        pytest.skip("embedding model not loaded")
    h = admin_h(client, keys)
    s = client.post("/api/knowledge", headers=h, json={"tenant_id": "acme", "name": "IT runbooks", "departments": ["it"],
                                                        "data_class": "Internal"}).json()
    doc = ("# VPN troubleshooting\n\nIf the Acme VPN client shows error 809, open Settings > Network, remove the profile "
           "named ACME-GW-EU and import the file acme-gw-eu-2026.ovpn from the IT portal. Then restart the client.\n\n"
           "# Printers\n\nThe third-floor printer queue is called PRN-3F-COLOR and needs the Konica driver version 4.2.")
    up = client.post(f"/api/knowledge/{s['source_id']}/documents", headers=h,
                     json={"filename": "runbook.md", "content_b64": base64.b64encode(doc.encode()).decode()})
    assert up.status_code == 200, up.text
    assert up.json()["chunks"] >= 1
    assert "cache_entries_invalidated" in up.json()   # answers cached before the upload are no longer reused
    hit = svc.kb.retrieve("acme", "it", "How do I fix VPN error 809?")
    assert hit and "ACME-GW-EU" in hit["context"] and hit["source_id"] == s["source_id"]
    assert svc.kb.retrieve("acme", "hr", "How do I fix VPN error 809?") is None      # not shared with HR
    assert svc.kb.retrieve("globex", "it", "How do I fix VPN error 809?") is None    # other tenant
    assert client.post(f"/api/knowledge/{s['source_id']}/revoke", headers=h).status_code == 200
    assert svc.kb.retrieve("acme", "it", "How do I fix VPN error 809?") is None
    bad = client.post(f"/api/knowledge/{s['source_id']}/documents", headers=h,
                      json={"filename": "x.exe", "content_b64": base64.b64encode(b"MZ").decode()})
    assert bad.status_code == 422


def test_assistant_proposal_needs_the_same_admin(client, keys, svc):
    h = admin_h(client, keys)
    me = client.get("/api/me", headers=h).json()["key_id"]
    svc.assistant.proposals["prop_test1"] = {"tool": "set_alert_rule", "args": {"rule": "error_spike", "enabled": "false"},
                                             "key_id": "someone-else", "ts": time.time(), "summary": "x"}
    assert client.post("/api/assistant/proposals/prop_test1/confirm", headers=h).status_code == 422
    svc.assistant.proposals["prop_test2"] = {"tool": "set_alert_rule", "args": {"rule": "error_spike", "enabled": "false"},
                                             "key_id": me, "ts": time.time(), "summary": "Disable error_spike"}
    r = client.post("/api/assistant/proposals/prop_test2/confirm", headers=h)
    assert r.status_code == 200 and r.json()["result"]["enabled"] is False
    e = client.get("/api/audit?action=alerts.rule", headers=h).json()["entries"][0]
    assert e["source"] == "assistant"
    assert client.post("/api/assistant/proposals/prop_test2/confirm", headers=h).status_code == 422   # single use
    client.post("/api/alerts/rules", headers=h, json={"rule": "error_spike", "enabled": True})


def test_assistant_read_tools_run_without_model(svc):
    a = svc.assistant
    assert "requests" in a.tools["overview"].fn(hours=24)
    assert isinstance(a.tools["organisation"].fn(), list)
    assert a.tools["policy"].fn(policy_id="hr")["remote"]["enabled"] is False
    assert "base_url" in a.tools["product_help"].fn(topic="connect")
    assert all(not t.write for n, t in a.tools.items() if n in ("overview", "keys", "spend", "search_requests"))
    assert all(t.write for n, t in a.tools.items() if n in ("create_key", "revoke_key", "erase_data", "set_budget"))


def test_chat_session_scopes(client, keys, svc):
    assert client.post("/api/chat/session", json={"api_key": "ng_live_nope_nope"}).status_code == 401
    s = client.post("/api/chat/session", json={"api_key": keys["acme/hr"]})
    assert s.status_code == 200 and s.json()["identity"]["department_id"] == "hr"
    assert client.post("/api/chat/send", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401
    r = client.post("/api/chat/send", headers=auth(s.json()["token"]),
                    json={"messages": [{"role": "user", "content": "my key is AKIAIOSFODNN7EXAMPLE"}]}).json()
    assert r["ok"] is False and r["error"]["code"] == "SECRET_BLOCKED" and r["headers"]["x-nanogate-receipt-id"]
    # a chat session is not a dashboard session
    assert client.get("/api/overview", headers=auth(s.json()["token"])).status_code == 403


@needs_model
def test_chat_send_real_answer(client, keys):
    tok = client.post("/api/chat/session", json={"api_key": keys["acme/it"]}).json()["token"]
    r = client.post("/api/chat/send", headers=auth(tok), json={"messages": [{"role": "user", "content": "Say hello in one word."}]}).json()
    assert r["ok"] and r["message"].strip() and r["headers"]["x-nanogate-route"] in ("local", "local_large", "cache", "remote")


def test_embeddings_endpoint(client, keys, svc):
    if svc.cache is None:
        pytest.skip("embedding model not loaded")
    r = client.post("/v1/embeddings", headers=auth(keys["acme/it"]), json={"input": ["hello world", "vpn reset"], "model": "nanogate-embed"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["object"] == "list" and len(j["data"]) == 2 and len(j["data"][0]["embedding"]) == 384
    assert j["usage"]["prompt_tokens"] > 0 and r.headers["x-nanogate-receipt-id"]
    assert client.post("/v1/embeddings", json={"input": "x"}).status_code == 401
    blocked = client.post("/v1/embeddings", headers=auth(keys["acme/it"]), json={"input": "AKIAIOSFODNN7EXAMPLE"})
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "SECRET_BLOCKED"
