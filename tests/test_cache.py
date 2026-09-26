"""Tenant-safe verified semantic cache: frozen pairs + isolation + invalidation (real models, CPU)."""
import time

import pytest

from nanogate.cache import namespace_fields

CANON = "How do I reset the VPN client?"
PARA = "What steps restore my VPN connection?"
HARD = "How do I reset another employee's VPN password?"


def ns(svc, tenant="acme", dept="it", policy=None, sysh="s0", ctx="c0"):
    pv = policy or svc.policies.get(svc.policies.policy_for(tenant, dept)).version_tag
    return namespace_fields(tenant, dept, pv, "qwen2.5", sysh, ctx)


@pytest.fixture()
def cache(svc):
    c = svc.cache
    if c is None:
        pytest.skip(f"semantic cache not loaded (NANOGATE_LOAD_ML=0 or model assets missing): {svc.ml_error}")
    svc.db.execute("DELETE FROM cache_entries")
    c._invalidate_index()
    c.revoked_sources.clear()
    return c


def put(c, svc, q=CANON, source_id=None, source_hash=None, **kw):
    return c.store(q, "Open the VPN app, choose Settings > Reset, then reconnect.", ns(svc, **kw), "Internal", 3600,
                   "Qwen/Qwen2.5-3B-Instruct", 30, 40, source_id, source_hash, "req_test")


def test_true_paraphrase_verified(cache, svc):
    put(cache, svc)
    r = cache.lookup(PARA, ns(svc))
    assert r.decision == "CACHE_VERIFIED" and r.hit, r.evidence


def test_hard_negative_rejected(cache, svc):
    put(cache, svc)
    r = cache.lookup(HARD, ns(svc))
    assert r.decision == "CACHE_HARD_NEGATIVE" and not r.hit
    assert any("ownership" in c for c in r.verifier["conflicts"])


def test_cross_tenant_namespace_miss(cache, svc):
    put(cache, svc)
    r = cache.lookup(CANON, ns(svc, tenant="globex", dept="it"))
    assert not r.hit and r.decision == "CACHE_NAMESPACE_MISMATCH"


def test_cross_department_namespace_miss(cache, svc):
    put(cache, svc)
    r = cache.lookup(CANON, ns(svc, dept="sales"))
    assert not r.hit and r.decision == "CACHE_NAMESPACE_MISMATCH"


def test_context_mismatch(cache, svc):
    put(cache, svc)
    r = cache.lookup(CANON, ns(svc, ctx="different-conversation"))
    assert not r.hit and r.decision == "CACHE_CONTEXT_MISMATCH"


def test_stale_policy_invalidation(cache, svc):
    old = svc.policies.get("it").version_tag
    put(cache, svc, policy=old)
    n = cache.invalidate_policy("it", old)
    assert n >= 1
    r = cache.lookup(CANON, ns(svc, policy=old))
    assert not r.hit


def test_ttl_expiry(cache, svc):
    put(cache, svc)
    r = cache.lookup(CANON, ns(svc), now=time.time() + 10 * 86400)
    assert not r.hit and r.decision == "CACHE_STALE"


def test_source_version_change_is_stale(cache, svc):
    cache.set_source_version("kb", "v1")
    put(cache, svc, source_id="kb", source_hash="v1")
    assert cache.lookup(CANON, ns(svc)).hit
    cache.set_source_version("kb", "v2")
    r = cache.lookup(CANON, ns(svc))
    assert not r.hit and r.decision == "CACHE_STALE"


def test_revoked_source(cache, svc):
    cache.set_source_version("kb2", "v1")
    put(cache, svc, source_id="kb2", source_hash="v1")
    cache.revoke_source("kb2")
    r = cache.lookup(CANON, ns(svc))
    assert not r.hit and r.decision in ("CACHE_SOURCE_REVOKED", "CACHE_MISS")
    cache.restore_source("kb2")


def test_poisoning_clients_cannot_write(client, keys):
    """There is no client-facing write path; any attempt to seed via API is not a route."""
    r = client.post("/api/cache/entries", headers={"Authorization": f"Bearer {keys['acme/it']}"}, json={"q": CANON, "a": "evil"})
    assert r.status_code in (401, 403, 404, 405)


@pytest.mark.parametrize("q,expect_hit", [
    ("How can I reset the VPN software on my laptop?", True),
    ("How do I uninstall the VPN client?", False),
    ("How do I reset the VPN client for all users in the company?", False),
    ("How do I request access to the finance share?", False),
])
def test_semantic_boundaries(cache, svc, q, expect_hit):
    put(cache, svc)
    assert cache.lookup(q, ns(svc)).hit is expect_hit
