"""DLP detection + the single policy engine."""
import pytest

from nanogate.dlp import LayeredDLP, luhn_ok, ssn_ok


@pytest.fixture(scope="module")
def dlp():
    d = LayeredDLP()
    d.load()
    return d


def test_luhn_and_ssn_validation():
    assert luhn_ok("4111 1111 1111 1111") and not luhn_ok("4111 1111 1111 1112")
    assert ssn_ok("219-09-9999") and not ssn_ok("000-12-3456") and not ssn_ok("666-12-3456") and not ssn_ok("912-12-3456")


@pytest.mark.parametrize("text,entity,klass", [
    ("contact jane.doe@example.com", "EMAIL_ADDRESS", "Confidential"),
    ("call 415-555-0132 today", "PHONE_NUMBER", "Confidential"),
    ("SSN 219-09-9999", "US_SSN", "Restricted"),
    ("card 4111 1111 1111 1111", "CREDIT_CARD", "Restricted"),
    ("key AKIAIOSFODNN7EXAMPLE", "CLOUD_CREDENTIAL", "Secret"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIE", "PRIVATE_KEY", "Secret"),
    ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijkl", "JWT", "Secret"),
    ("postgres://admin:s3cr3t@db.local/prod", "DB_CREDENTIAL", "Secret"),
    ("my password is Hunter2!x9", "PASSWORD", "Secret"),
    ("employee EMP-204981 asked", "EMPLOYEE_ID", "Confidential"),
    ("reach me at jane [at] example [dot] com", "EMAIL_ADDRESS", "Confidential"),
    ("ssn two one nine zero nine nine nine nine nine", "US_SSN", "Restricted"),
])
def test_detects(dlp, text, entity, klass):
    r = dlp.scan(text)
    assert entity in r.counts, r.counts
    assert r.data_class == klass


def test_no_false_secret_for_the_word_password(dlp):
    r = dlp.scan("How do I reset another employee's VPN password?")
    assert r.data_class == "Internal" and not r.has_secret


def test_findings_never_contain_raw_values(dlp):
    r = dlp.scan("SSN 219-09-9999 and jane.doe@example.com")
    blob = str(r.public())
    assert "219-09-9999" not in blob and "jane.doe@example.com" not in blob


def test_tokenize_roundtrip(dlp):
    t = "email jane.doe@example.com now"
    r = dlp.scan(t)
    tok, m = LayeredDLP.tokenize(t, r.findings)
    assert "jane.doe" not in tok and LayeredDLP.detokenize(tok, m) == t


def test_policy_hr_pii_local_only(svc):
    d = svc.policies.evaluate("hr", "Restricted", {"US_SSN": 1}, False, True, True, "live")
    assert "remote" not in d.allowed_routes and not d.egress_permitted
    assert "SENSITIVE_LOCAL_ONLY" in d.reason_codes
    assert not d.cache_eligible


def test_policy_secret_denied(svc):
    d = svc.policies.evaluate("it", "Secret", {"API_KEY": 1}, True, False, True, "live")
    assert d.action == "deny" and d.reason_codes == ["SECRET_BLOCKED"] and d.allowed_routes == []


def test_policy_sales_tokenize_permits_remote(svc):
    d = svc.policies.evaluate("sales", "Confidential", {"EMAIL_ADDRESS": 1}, False, True, True, "live")
    assert d.transform == "tokenize" and d.data_class == "Internal" and "remote" in d.allowed_routes


def test_policy_it_clean_remote_allowed(svc):
    d = svc.policies.evaluate("it", "Internal", {}, False, False, True, "live")
    assert d.egress_permitted and "remote" in d.allowed_routes and d.cache_eligible


def test_dlp_unavailable_fails_closed(svc):
    d = svc.policies.evaluate("hr", "Internal", {}, False, False, False, "disabled")
    assert d.action == "deny" and d.reason_codes == ["DLP_UNAVAILABLE_FAIL_CLOSED"]


def test_dlp_unavailable_end_to_end(client, keys, svc):
    svc.dlp.forced_unavailable = "test: detector offline"
    try:
        r = client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {keys['acme/hr']}"},
                        json={"messages": [{"role": "user", "content": "hello"}]})
        assert r.status_code == 503 and r.json()["error"]["code"] == "DLP_UNAVAILABLE_FAIL_CLOSED"
    finally:
        svc.dlp.forced_unavailable = None


def test_policy_publish_versions_and_invalidates(svc):
    before = svc.policies.get("it").version
    body = svc.policies.get("it").body()
    body["rate_limit"]["rpm"] = 119
    p = svc.policies.publish("it", body, actor="test")
    assert p.version == before + 1 and svc.policies.get("it").rate_limit.rpm == 119
    body["rate_limit"]["rpm"] = 120
    svc.policies.publish("it", body, actor="test")


def test_policy_publish_rejects_invalid(svc):
    body = svc.policies.get("it").body()
    body["remote"]["max_data_class"] = "TopSecret"
    with pytest.raises(Exception):
        svc.policies.publish("it", body, actor="test")


def test_policy_test_endpoint(client, keys):
    tok = client.post("/api/session", json={"api_key": keys["admin"]}).json()["token"]
    r = client.post("/api/policies/test", headers={"Authorization": f"Bearer {tok}"},
                    json={"tenant": "acme", "department": "hr", "prompt": "Employee EMP-204981 SSN 219-09-9999"}).json()
    assert r["classification"] == "Restricted" and r["remote_path_removed"] is True
    assert "SENSITIVE_LOCAL_ONLY" in r["reason_codes"]
