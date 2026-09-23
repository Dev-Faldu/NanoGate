"""HTTP surface: health, metrics, OpenAI compatibility, explicit failure states, receipts for denials."""
import pytest

from conftest import MODEL_UP, auth, needs_model


def test_healthz_reports_components(client):
    j = client.get("/healthz").json()
    assert j["alive"] and {"database", "model", "dlp", "cache", "router", "telemetry"} <= set(j["components"])


def test_readyz_matches_reality(client, svc):
    r = client.get("/readyz")
    ok, failing = svc.ready()
    assert (r.status_code == 200) == ok
    assert r.json()["failing"] == failing


def test_metrics_prometheus(client):
    r = client.get("/metrics")
    assert r.status_code == 200 and "nanogate_requests_total" in r.text


def test_models_requires_auth_and_lists_tiers(client, keys):
    assert client.get("/v1/models").status_code == 401
    ids = [m["id"] for m in client.get("/v1/models", headers=auth(keys["acme/it"])).json()["data"]]
    assert "nanogate-auto" in ids and "nanogate-local" in ids


def test_secret_blocked_with_headers_and_receipt(client, keys, svc):
    r = client.post("/v1/chat/completions", headers=auth(keys["acme/it"]),
                    json={"messages": [{"role": "user", "content": "use AKIAIOSFODNN7EXAMPLE for the deploy"}]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SECRET_BLOCKED"
    assert r.headers["x-nanogate-reason"] == "SECRET_BLOCKED" and r.headers["x-nanogate-data-class"] == "Secret"
    rec = svc.receipts.get(r.headers["x-nanogate-receipt-id"])
    assert rec and "AKIAIOSFODNN7EXAMPLE" not in str(rec)
    assert svc.receipts.verify(rec["receipt_id"])["valid"]


def test_route_escalation_denied(client, keys):
    r = client.post("/v1/chat/completions", headers=auth(keys["acme/hr"]),
                    json={"model": "nanogate-remote", "messages": [{"role": "user", "content": "summarize our leave policy"}]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "ROUTE_ESCALATION_DENIED"


def test_oversized_prompt(client, keys, svc):
    big = "x " * (svc.settings.max_prompt_chars // 2 + 10)
    r = client.post("/v1/chat/completions", headers=auth(keys["acme/it"]), json={"messages": [{"role": "user", "content": big}]})
    assert r.status_code == 413 and r.json()["error"]["code"] == "INPUT_TOO_LARGE"


def test_invalid_body(client, keys):
    r = client.post("/v1/chat/completions", headers=auth(keys["acme/it"]), json={"messages": "nope"})
    assert r.status_code == 400


@pytest.mark.skipif(MODEL_UP, reason="checks the model-down path; model is up")
def test_model_unavailable_is_explicit(client, keys, svc):
    r = client.post("/v1/chat/completions", headers=auth(keys["acme/it"]),
                    json={"messages": [{"role": "user", "content": "Explain what a VPN does in one sentence."}]})
    assert r.status_code == 503 and r.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    rec = svc.receipts.get(r.headers["x-nanogate-receipt-id"])
    assert rec["body"]["status"] == "error"
    acct = [a for a in svc.budget.accounts("acme") if a["department_id"] == "it"][0]
    assert acct["reserved_tokens"] == 0     # reservation released on failure


def test_every_request_has_a_receipt(client, svc):
    n_req = svc.db.one("SELECT COUNT(*) n FROM requests")["n"]
    n_missing = svc.db.one("SELECT COUNT(*) n FROM requests r LEFT JOIN receipts c ON r.receipt_id=c.receipt_id "
                           "WHERE c.receipt_id IS NULL")["n"]
    assert n_req > 0 and n_missing == 0


def test_openapi_documents_headers(client):
    spec = client.get("/openapi.json").json()
    assert "/v1/chat/completions" in spec["paths"]
    assert "x-nanogate-receipt-id" in spec["paths"]["/v1/chat/completions"]["post"]["description"]


# ---- real-model integration (skipped with reason when the model is down) ----------------------
@needs_model
def test_openai_sdk_sync(app, keys):
    from openai import OpenAI
    import httpx
    c = OpenAI(base_url="http://testserver/v1", api_key=keys["acme/it"],
               http_client=httpx.Client(transport=httpx.ASGITransport(app=app), base_url="http://testserver"))
    r = c.chat.completions.with_raw_response.create(model="nanogate-auto",
                                                     messages=[{"role": "user", "content": "Name one benefit of a VPN in one sentence."}])
    resp = r.parse()
    assert resp.choices[0].message.content and resp.usage.completion_tokens > 0
    assert r.headers["x-nanogate-route"] in ("local", "local_large", "cache")


@needs_model
def test_openai_sdk_stream(client, keys):
    with client.stream("POST", "/v1/chat/completions", headers=auth(keys["acme/it"]),
                       json={"stream": True, "messages": [{"role": "user", "content": "Count from one to five in words."}]}) as r:
        chunks = [l for l in r.iter_lines() if l.startswith("data:")]
    assert len(chunks) > 3 and chunks[-1] == "data: [DONE]"
    assert '"nanogate"' in chunks[-2]


@needs_model
def test_demo_cache_sequence(client, keys):
    h = auth(keys["acme/it"])
    body = lambda q: {"messages": [{"role": "user", "content": q}], "temperature": 0}
    client.post("/v1/chat/completions", headers=h, json=body("How do I reset the VPN client?"))
    r2 = client.post("/v1/chat/completions", headers=h, json=body("What steps restore my VPN connection?"))
    r3 = client.post("/v1/chat/completions", headers=h, json=body("How do I reset another employee's VPN password?"))
    assert "CACHE_HARD_NEGATIVE" in r3.headers["x-nanogate-reason-codes"]
    assert r2.headers["x-nanogate-route"] in ("cache", "local", "local_large")


@needs_model
def test_hr_pii_stays_local_zero_egress(client, keys, svc):
    r = client.post("/v1/chat/completions", headers=auth(keys["acme/hr"]), json={"messages": [{"role": "user", "content":
        "Confirm leave for employee Maria Lopez (EMP-204981, maria.lopez@acme-corp.example, SSN 219-09-9999)."}]})
    assert r.status_code == 200 and "SENSITIVE_LOCAL_ONLY" in r.headers["x-nanogate-reason-codes"]
    rec = svc.receipts.get(r.headers["x-nanogate-receipt-id"])
    assert rec["body"]["decision"]["egress"]["remote_bytes_out"] == 0
    assert rec["body"]["decision"]["chosen_route"] in ("local", "local_large")
