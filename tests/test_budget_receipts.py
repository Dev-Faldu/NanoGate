"""Budget race safety, receipt integrity, logging redaction, router features, telemetry honesty."""
import hashlib
import json
import logging
import threading

from nanogate import router_features as rf
from nanogate.budget import BudgetDenied, BudgetEngine
from nanogate.logging_setup import JsonFormatter


def test_budget_race_never_overspends(svc):
    b = BudgetEngine(svc.db)
    ok, denied = [], []
    lock = threading.Lock()

    def worker(i):
        try:
            r = b.reserve(f"race-{i}", "race", "t1", 1.0, 100, limit_usd=10.0, limit_tokens=1_000_000)
            with lock:
                ok.append(r)
        except BudgetDenied:
            with lock:
                denied.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(60)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(ok) == 10 and len(denied) == 50
    acct = [a for a in b.accounts("race") if a["department_id"] == "t1"][0]
    assert abs(acct["reserved_usd"] - 10.0) < 1e-9 and acct["reserved_usd"] <= acct["limit_usd"]
    for r in ok:
        b.settle(r, 0.25, 50)
    acct = [a for a in b.accounts("race") if a["department_id"] == "t1"][0]
    assert abs(acct["spent_usd"] - 2.5) < 1e-9 and acct["reserved_usd"] < 1e-9


def test_budget_token_limit(svc):
    b = BudgetEngine(svc.db)
    b.reserve("tok-1", "race", "t2", 0.0, 900, 1.0, 1000)
    try:
        b.reserve("tok-2", "race", "t2", 0.0, 200, 1.0, 1000)
        assert False, "should deny"
    except BudgetDenied as e:
        assert e.detail["available_tokens"] == 100


def test_receipt_chain_and_tamper_detection(svc):
    ids = []
    for i in range(3):
        rid = svc.receipts.new_id()
        svc.receipts.seal(rid, f"req-{i}", "acme", "it", "LOCAL_CONFIDENT", {"n": i, "decision": {"chosen_route": "local"}})
        ids.append(rid)
    assert all(svc.receipts.verify(r)["valid"] for r in ids)
    row = svc.db.one("SELECT body_json FROM receipts WHERE receipt_id=?", (ids[1],))
    body = json.loads(row["body_json"])
    body["decision"]["chosen_route"] = "remote"
    svc.db.execute("UPDATE receipts SET body_json=? WHERE receipt_id=?", (json.dumps(body, sort_keys=True, separators=(",", ":")), ids[1]))
    v = svc.receipts.verify(ids[1])
    assert not v["valid"] and not v["checks"]["body_hash_matches"]
    assert not svc.receipts.verify_chain()["valid"]
    # restore so later tests see a clean chain
    body["decision"]["chosen_route"] = "local"
    svc.db.execute("UPDATE receipts SET body_json=? WHERE receipt_id=?", (json.dumps(body, sort_keys=True, separators=(",", ":")), ids[1]))
    assert svc.receipts.verify(ids[1])["valid"]


def test_receipt_deletion_breaks_chain(svc):
    ids = [svc.receipts.new_id() for _ in range(3)]
    for i, rid in enumerate(ids):
        svc.receipts.seal(rid, f"del-{i}", "acme", "it", "LOCAL_CONFIDENT", {"n": i})
    saved = svc.db.one("SELECT * FROM receipts WHERE receipt_id=?", (ids[1],))
    svc.db.execute("DELETE FROM receipts WHERE receipt_id=?", (ids[1],))
    assert not svc.receipts.verify(ids[2])["checks"]["chain_link_valid"]
    cols = ",".join(saved)
    svc.db.execute(f"INSERT INTO receipts({cols}) VALUES ({','.join('?' * len(saved))})", tuple(saved.values()))


def test_log_redaction():
    f = JsonFormatter()
    rec = logging.LogRecord("t", logging.INFO, "", 0,
                            "key ng_live_abcd1234_SECRETSECRETSECRET email a.b@c.com ssn 219-09-9999 sk-abcdefghijklmnopqrstuv", None, None)
    out = f.format(rec)
    for bad in ("SECRETSECRET", "a.b@c.com", "219-09-9999", "sk-abcdefghijkl"):
        assert bad not in out


def test_router_feature_vector_shape():
    f = rf.extract([{"role": "user", "content": "What is 2+2?"}], {}, None)
    v = rf.vectorize(f)
    assert len(v) == len(rf.feature_names())
    names = rf.feature_names()
    assert v[names.index("gen_mean_logprob__missing")] == 1.0   # missing generation -> indicator set


def test_telemetry_fields_are_honest(svc):
    snap = svc.telemetry.snapshot()
    for section in ("gpu", "power", "temperature", "memory"):
        for k, fld in snap[section].items():
            assert fld["status"] in ("live", "unavailable")
            if fld["status"] == "unavailable":
                assert fld["value"] is None and fld["reason"]


def test_pricing_has_sources(svc):
    for r in svc.pricing.rates.values():
        assert r.source and r.effective_date and r.currency == "USD"
