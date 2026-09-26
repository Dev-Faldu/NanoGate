"""NanoGate Assistant: ask questions about the gateway in plain language, and have it prepare admin tasks.

Runs entirely on the device (the local-large model, falling back to the local model); nothing is sent to a remote
tier. The model never sees prompt or answer text of other users' requests (NanoGate does not store it by default),
only the metadata the tools return. It answers from tool results, not from memory.

Tools that only read run immediately. Tools that change something (create a key, publish a budget, erase data...)
are never executed by the model: they become a *proposal* the signed-in admin must confirm in the UI, and the
confirmed action is executed with that admin's identity and written to the audit log with source "assistant".
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .actions import ActionError, Actions, public_channels

log = logging.getLogger("nanogate.assistant")

MAX_STEPS = 5
RESULT_CHARS = 5000
PROPOSAL_TTL_S = 15 * 60


@dataclass
class Tool:
    description: str
    params: dict[str, str]
    fn: Callable[..., Any]
    write: bool = False
    summary: Callable[[dict], str] | None = None


HELP = {
    "connect": "Apps connect with any OpenAI SDK: set base_url to http(s)://<nanogate-host>:8080/v1 and api_key to a key "
               "created on the Access page (kind 'app'). Model names: nanogate-auto (recommended: policy and router decide), "
               "nanogate-local, nanogate-local-large, nanogate-remote. Embeddings: POST /v1/embeddings.",
    "routes": "Every request is checked in order: identity (API key), DLP (secrets and personal data), department policy, "
              "budget, verified cache, local model, router (estimated chance the local answer is wrong), then the final "
              "route: cache, local, local_large, or remote (only if the policy allows it for that data class).",
    "receipts": "Each request, answered or denied, gets a receipt sealed into a SHA-256 hash chain with an HMAC. "
                "Verify one on its page; the chain check proves nothing was altered, reordered or removed. Retention "
                "erases old receipt bodies but keeps their hashes, so the chain still verifies.",
    "privacy": "Sensitive data (Confidential/Restricted/Secret, or personal data under a local-only policy) never goes to a "
               "remote tier; secrets are blocked. The Sensitive egress tile counts bytes of sensitive requests sent out.",
    "cache": "A cached answer is reused only for the same tenant and department namespace and only if an NLI model plus "
             "rule checks confirm the new question means the same thing. Revoking a source invalidates answers built on it.",
    "keys": "Access page: kinds are app (OpenAI API), person (chat app), auditor (read-only dashboard) and admin. A key is "
            "shown once when created; NanoGate stores only a keyed hash. Revoking takes effect immediately.",
    "alerts": "Alerts page: add Slack, Teams or webhook channels; rules cover budget thresholds, model down, blocked secrets, "
              "impersonation attempts, sensitive egress, receipt-chain failures and error spikes.",
    "knowledge": "Knowledge page: upload PDFs or text for a tenant and choose which departments may use them. Answers cite "
                 "the document. Revoking a source stops its use and invalidates cached answers built from it.",
    "operations": "Operations page: HTTPS status, backups (scheduled and manual; restore with scripts/restore.sh), "
                  "retention, right-to-erasure, CSV/JSON exports and SIEM (syslog) forwarding.",
}


def _since(hours: float) -> float:
    return time.time() - float(hours) * 3600


class Assistant:
    def __init__(self, svc):
        self.svc = svc
        self.actions: Actions = svc.actions
        self.proposals: dict[str, dict] = {}
        self.tools = self._tools()

    # ---- tools ----------------------------------------------------------------------------------------
    def _tools(self) -> dict[str, Tool]:
        a, svc = self.actions, self.svc
        return {
            "overview": Tool("Totals for a recent window: requests, answered/denied, routes, reasons, latency, cost, "
                             "sensitive egress, cache hits.", {"hours": "window in hours, default 24"}, self._overview),
            "search_requests": Tool("Find requests (metadata only, no prompt text).",
                                    {"department": "optional", "tenant": "optional", "reason": "optional reason code",
                                     "route": "optional: cache|local|local_large|remote|denied|error",
                                     "status": "optional: ok|denied|error", "hours": "default 24", "limit": "default 10"},
                                    self._search),
            "explain_receipt": Tool("Everything recorded for one receipt: stages, route, reasons, router score, cost.",
                                    {"receipt_id": "rcpt_..."}, self._receipt),
            "verify_receipt": Tool("Cryptographically verify one receipt.", {"receipt_id": "rcpt_..."},
                                   lambda receipt_id: svc.receipts.verify(receipt_id)),
            "spend": Tool("Measured cost per department for a month.", {"month": "YYYY-MM, default current",
                                                                         "tenant": "optional"},
                          lambda month=None, tenant=None: a.chargeback(month, tenant)),
            "organisation": Tool("Tenants, departments, their policy, budget, spend this month and key counts.", {},
                                 a.org),
            "policy": Tool("A policy's rules (routes, remote limits, DLP actions, cache, budget, retention).",
                           {"policy_id": "e.g. hr, it, sales, security"}, self._policy),
            "test_prompt": Tool("Run the real DLP + policy engine on a sample prompt for a department (no model call): "
                                "shows its data class and which routes are allowed.",
                                {"prompt": "text", "tenant": "default acme", "department": "e.g. hr"}, self._test_prompt),
            "keys": Tool("API keys (never the secret): kind, department, state, last use.",
                         {"tenant": "optional", "department": "optional"},
                         lambda tenant=None, department=None: a.keys(tenant, department)),
            "alerts": Tool("Recent alerts and the configured alert rules/channels.", {"limit": "default 10"}, self._alerts),
            "system_status": Tool("Health of models, DLP, cache, router, telemetry (GPU power, memory) and remote connector.",
                                  {}, self._status),
            "benchmarks": Tool("Latest measured benchmark results (RESULTS.md rows with run ids).", {}, self._benchmarks),
            "knowledge_sources": Tool("Uploaded company document sources and who may use them.", {"tenant": "optional"},
                                      lambda tenant=None: [{k: s[k] for k in ("source_id", "tenant_id", "name", "departments",
                                                                              "data_class", "chunks", "revoked", "version")}
                                                           for s in svc.kb.list(tenant)]),
            "audit_log": Tool("Recent administrative changes (who did what).", {"limit": "default 15", "action": "optional prefix"},
                              lambda limit=15, action=None: svc.ops.audit.list(int(limit), action)),
            "backups": Tool("Backups on this device.", {}, svc.ops.backups.list),
            "product_help": Tool("How NanoGate works and how to use it.", {"topic": ", ".join(HELP)},
                                 lambda topic="connect": HELP.get(str(topic).lower(), "Topics: " + ", ".join(HELP))),
            # --- proposals: executed only after the admin confirms ---
            "create_key": Tool("PROPOSE creating an API key.", {"tenant_id": "", "department_id": "",
                                                                  "kind": "app|person|auditor|admin", "name": "who/what it is for",
                                                                  "days": "optional expiry"},
                               a.create_key, True,
                               lambda x: f"Create {'an' if str(x.get('kind', '')).startswith(('a', 'e', 'i', 'o', 'u')) else 'a'} {x.get('kind')} key '{x.get('name')}' for {x.get('tenant_id')}/{x.get('department_id')}"
                                         + (f", expiring in {x['days']} days" if x.get("days") else "")),
            "revoke_key": Tool("PROPOSE revoking an API key.", {"key_id": ""}, a.revoke_key, True,
                               lambda x: f"Revoke key {x.get('key_id')} (stops working immediately)"),
            "create_department": Tool("PROPOSE adding a department.", {"tenant_id": "", "department_id": "",
                                                                        "display_name": "", "policy_id": ""},
                                      a.create_department, True,
                                      lambda x: f"Add department {x.get('tenant_id')}/{x.get('department_id')} "
                                                f"('{x.get('display_name')}') under policy {x.get('policy_id')}"),
            "set_budget": Tool("PROPOSE changing a policy's monthly budget (publishes a new policy version).",
                               {"policy_id": "", "monthly_usd": "number", "monthly_tokens": "optional"},
                               a.set_budget, True,
                               lambda x: f"Set the {x.get('policy_id')} policy budget to ${x.get('monthly_usd')}/month"
                                         + (f" and {x['monthly_tokens']} tokens" if x.get("monthly_tokens") else "")),
            "set_alert_rule": Tool("PROPOSE enabling/disabling an alert rule or changing its percentage.",
                                   {"rule": "budget_threshold|model_down|secret_blocked|spoof_attempt|sensitive_egress|"
                                            "chain_integrity|error_spike", "enabled": "true|false", "percent": "optional"},
                                   a.set_alert_rule, True,
                                   lambda x: f"{'Enable' if str(x.get('enabled')).lower() in ('true', '1') else 'Disable'} alert "
                                             f"rule {x.get('rule')}" + (f" at {x['percent']}%" if x.get("percent") else "")),
            "run_backup": Tool("PROPOSE taking a backup now.", {}, a.backup, True, lambda x: "Take a backup now"),
            "set_remote_mode": Tool("PROPOSE switching the remote connector mode.", {"mode": "disabled|mock|live|outage-test"},
                                    a.set_remote_mode, True, lambda x: f"Switch the remote connector to '{x.get('mode')}'"),
            "revoke_knowledge_source": Tool("PROPOSE revoking (or restoring with revoked=false) a knowledge source.",
                                            {"source_id": "", "revoked": "true|false"},
                                            lambda source_id, revoked=True, **kw: a.set_source_revoked(
                                                source_id, str(revoked).lower() not in ("false", "0"), **kw), True,
                                            lambda x: f"{'Restore' if str(x.get('revoked')).lower() in ('false', '0') else 'Revoke'} "
                                                      f"knowledge source {x.get('source_id')}"),
            "erase_data": Tool("PROPOSE erasing everything recorded for one key, or for one department (right to erasure).",
                               {"key_id": "optional", "tenant_id": "optional", "department_id": "optional"},
                               a.erase, True,
                               lambda x: "Erase all requests, receipt bodies and cached answers for "
                                         + (f"key {x['key_id']}" if x.get("key_id") else f"{x.get('tenant_id')}/{x.get('department_id')}")
                                         + " (cannot be undone)"),
        }

    def _overview(self, hours: float = 24) -> dict:
        rows = self.svc.db.all("SELECT route, status, reason, latency_ms, data_class, remote_bytes, cache_status, cost_usd,"
                               " counterfactual_usd FROM requests WHERE ts>=?", (_since(hours),))
        cnt = lambda k: dict(sorted({r[k]: sum(1 for x in rows if x[k] == r[k]) for r in rows}.items(), key=lambda kv: -kv[1]))
        lat = sorted(r["latency_ms"] for r in rows if r["status"] == "ok" and r["latency_ms"] is not None)
        return {"window_hours": hours, "requests": len(rows), "by_status": cnt("status"), "by_route": cnt("route"),
                "denied_or_error_reasons": dict(sorted({r["reason"]: sum(1 for x in rows if x["reason"] == r["reason"] and x["status"] != "ok")
                                                        for r in rows if r["status"] != "ok"}.items(), key=lambda kv: -kv[1])[:6]),
                "answered_outcomes": dict(sorted({r["reason"]: sum(1 for x in rows if x["reason"] == r["reason"] and x["status"] == "ok")
                                                  for r in rows if r["status"] == "ok"}.items(), key=lambda kv: -kv[1])[:6]),
                "latency_p50_ms": lat[len(lat) // 2] if lat else None,
                "latency_p95_ms": lat[int(len(lat) * 0.95)] if lat else None,
                "cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 6),
                "hosted_equivalent_usd": round(sum(r["counterfactual_usd"] or 0 for r in rows), 6),
                "sensitive_egress_bytes": sum(r["remote_bytes"] or 0 for r in rows
                                              if r["data_class"] in ("Confidential", "Restricted", "Secret")),
                "cache_verified_hits": sum(1 for r in rows if r["cache_status"] == "CACHE_VERIFIED")}

    def _search(self, department=None, tenant=None, reason=None, route=None, status=None, hours: float = 24,
                limit: int = 10) -> list[dict]:
        where, args = ["ts>=?"], [_since(hours)]
        for col, v in (("department_id", department), ("tenant_id", tenant), ("reason", reason), ("route", route),
                       ("status", status)):
            if v:
                where.append(f"{col}=?")
                args.append(v)
        rows = self.svc.db.all("SELECT receipt_id, ts, tenant_id, department_id, intent, data_class, route, reason, status,"
                               " p_error, latency_ms, prompt_tokens, completion_tokens, cost_usd FROM requests WHERE "
                               + " AND ".join(where) + " ORDER BY ts DESC LIMIT ?", (*args, min(int(limit), 25)))
        for r in rows:
            r["time"] = dt.datetime.fromtimestamp(r.pop("ts"), dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        return rows

    def _receipt(self, receipt_id: str) -> dict:
        r = self.svc.receipts.get(receipt_id)
        if not r:
            return {"error": f"no receipt {receipt_id}"}
        b = r["body"]
        if not b:
            return {"receipt_id": receipt_id, "pruned": True, "note": "body erased by retention or an erasure request"}
        return {"receipt_id": receipt_id, "reason": r["reason"],
                "identity": {k: b.get("identity", {}).get(k) for k in ("tenant_id", "department", "policy_version", "timestamp")},
                "classification": b.get("classification"), "decision": b.get("decision"), "router": b.get("router"),
                "cache": b.get("cache"), "model": b.get("model"), "economics": b.get("economics"),
                "stages": [f"{t['stage']}:{t['status']}@{round(t['t_ms'])}ms" for t in b.get("timeline", [])]}

    def _policy(self, policy_id: str) -> dict:
        try:
            p = self.svc.policies.get(policy_id)
        except KeyError:
            return {"error": f"unknown policy {policy_id}", "policies": sorted(self.svc.policies._current)}
        return {"policy_id": policy_id, "version": p.version_tag, **p.body()}

    async def _test_prompt(self, prompt: str, department: str = "it", tenant: str = "acme") -> dict:
        svc = self.svc
        pid = svc.policies.policy_for(tenant, department)
        dres = await asyncio.to_thread(svc.dlp.scan, prompt)
        dec = svc.policies.evaluate(pid, dres.data_class, dres.counts, dres.has_secret, dres.has_pii, dres.available,
                                    svc.remote.mode)
        return {"policy": dec.policy_version, "data_class": dec.data_class, "findings": dres.counts, "action": dec.action,
                "allowed_routes": dec.allowed_routes, "reason_codes": dec.reason_codes,
                "remote_permitted": dec.egress_permitted, "cache_eligible": dec.cache_eligible}

    def _alerts(self, limit: int = 10) -> dict:
        rows = self.svc.db.all("SELECT alert_id, ts, rule, severity, subject, title, acknowledged_at FROM alerts "
                               "ORDER BY ts DESC LIMIT ?", (int(limit),))
        return {"recent": rows, "rules": self.svc.ops_settings.get("alerts.rules"),
                "channels": public_channels(self.svc.ops_settings.get("alerts.channels"))}

    def _status(self) -> dict:
        c = self.svc.components()
        s = self.svc.last_sample or {}
        return {"ready": self.svc.ready(),
                "components": {k: {kk: v.get(kk) for kk in ("ok", "state", "reason", "model", "mode", "version") if v.get(kk) is not None}
                               for k, v in c.items()},
                "gpu": {k: s.get(k) for k in ("gpu_util", "gpu_power_w", "gpu_temp_c", "mem_used_bytes", "mem_total_bytes")},
                "local_tokens_per_s": self.svc.local.last_tokens_per_s}

    def _benchmarks(self) -> dict:
        from .settings import ROOT
        runs = sorted((ROOT / "results").glob("*-report-*/summary.json"))
        if not runs:
            return {"available": False}
        return json.loads(runs[-1].read_text())

    # ---- model ----------------------------------------------------------------------------------------
    def _adapter(self):
        big = self.svc.local_large
        if big and big.status.get("state") == "ready":
            return big
        return self.svc.local

    def _system(self, ident) -> str:
        tools = "\n".join(f"- {n}({', '.join(f'{k}: {v}' for k, v in t.params.items())}): {t.description}"
                          for n, t in self.tools.items())
        snap = self._overview(24)
        return (
            "You are NanoGate Assistant, built into the NanoGate dashboard on an HP ZGX Nano. NanoGate is a local-first, "
            "OpenAI-compatible AI gateway: it checks identity, scans for secrets and personal data, applies department "
            "policies and budgets, reuses verified cached answers, runs local models, routes risky questions to a larger "
            "tier, and seals every decision into a tamper-evident receipt.\n"
            f"Today is {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. You are helping admin key "
            f"{ident.key_id} ({ident.tenant_id}/{ident.department_id}).\n\n"
            "RULES\n"
            "1. Answer only from tool results and the snapshot below. Never invent numbers, ids or names. If data is "
            "missing, say so and suggest where to look.\n"
            "2. Tools marked PROPOSE do not run: calling one shows the user a confirmation card. Say that the change is "
            "waiting for their confirmation; never claim it is done.\n"
            "3. You cannot see prompt or answer text of requests (it is not stored); only metadata.\n"
            "4. Spend: on-device tiers are priced at $0 per token (config/pricing.yaml), so when asked about cost also give "
            "tokens, the hosted-API equivalent (counterfactual) and the cost avoided, using the spend tool.\n"
            "5. Be brief and practical. Use short paragraphs or bullet lists (Markdown). Mention receipt ids when relevant.\n\n"
            "RESPOND WITH ONE JSON OBJECT and nothing else, either\n"
            '{"tool": "<name>", "args": {...}}   to call a tool, or\n'
            '{"answer": "<markdown reply to the user>"}   when you can answer.\n\n'
            f"TOOLS\n{tools}\n\nLIVE SNAPSHOT (last 24h)\n{json.dumps(snap, default=str)}"
        )

    async def _ask(self, messages: list[dict]) -> tuple[dict, dict]:
        ad = self._adapter()
        params = {"temperature": 0.0, "max_tokens": 600, "response_format": {"type": "json_object"}}
        try:
            res = await ad.generate(messages, params, logprobs=False)
        except Exception:
            params.pop("response_format")
            res = await ad.generate(messages, params, logprobs=False)
        meta = {"model": ad.model, "tier": ad.tier, "completion_tokens": res.completion_tokens,
                "ms": round(res.total_ms or 0)}
        return _parse(res.text), meta

    async def chat(self, ident, history: list[dict], message: str) -> dict:
        t0 = time.perf_counter()
        msgs = [{"role": "system", "content": self._system(ident)}]
        for h in history[-12:]:
            if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
                msgs.append({"role": h["role"], "content": h["content"][:4000]})
        msgs.append({"role": "user", "content": message[:4000]})
        steps, proposals, models = [], [], []
        answer = None
        for _ in range(MAX_STEPS + 1):
            out, meta = await self._ask(msgs)
            models.append(meta)
            msgs.append({"role": "assistant", "content": json.dumps(out)})
            if "answer" in out or "tool" not in out:
                answer = out.get("answer") or out.get("text") or out.get("_raw")
                break
            name, args = out.get("tool"), out.get("args") or {}
            tool = self.tools.get(name)
            if tool is None:
                msgs.append({"role": "user", "content": f"TOOL ERROR: no tool named {name}. Use one from the list."})
                steps.append({"tool": name, "ok": False, "summary": "unknown tool"})
                continue
            if not isinstance(args, dict):
                args = {}
            if tool.write:
                pid = "prop_" + uuid.uuid4().hex[:12]
                summary = tool.summary(args) if tool.summary else name
                self.proposals[pid] = {"tool": name, "args": args, "key_id": ident.key_id, "ts": time.time(),
                                       "summary": summary}
                proposals.append({"proposal_id": pid, "tool": name, "args": args, "summary": summary})
                steps.append({"tool": name, "ok": True, "summary": "proposed: " + summary})
                msgs.append({"role": "user", "content": f"TOOL RESULT {name}: proposal {pid} shown to the user for "
                                                        f"confirmation ({summary}). It has NOT been executed."})
                continue
            try:
                r = tool.fn(**args)
                if asyncio.iscoroutine(r):
                    r = await r
                text = json.dumps(r, default=str)
                steps.append({"tool": name, "args": args, "ok": True, "summary": _brief(r)})
            except TypeError as e:
                text = f"ERROR: bad arguments ({e})"
                steps.append({"tool": name, "args": args, "ok": False, "summary": "bad arguments"})
            except Exception as e:
                text = f"ERROR: {type(e).__name__}: {e}"
                steps.append({"tool": name, "args": args, "ok": False, "summary": str(e)[:120]})
            msgs.append({"role": "user", "content": f"TOOL RESULT {name}: {text[:RESULT_CHARS]}"
                                                    + (" …(truncated)" if len(text) > RESULT_CHARS else "")})
        if answer is None:
            answer = "I could not finish that within the step limit. Try asking a narrower question."
        self._expire()
        return {"answer": answer, "steps": steps, "proposals": proposals, "models": models,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000)}

    # ---- proposals ------------------------------------------------------------------------------------
    def _expire(self) -> None:
        now = time.time()
        for k in [k for k, p in self.proposals.items() if now - p["ts"] > PROPOSAL_TTL_S]:
            self.proposals.pop(k, None)

    def confirm(self, proposal_id: str, ident) -> dict:
        self._expire()
        p = self.proposals.get(proposal_id)
        if not p:
            raise ActionError("this proposal expired or was already handled; ask again")
        if p["key_id"] != ident.key_id:
            raise ActionError("only the admin who asked can confirm this proposal")
        self.proposals.pop(proposal_id)
        tool = self.tools[p["tool"]]
        args = dict(p["args"])
        for k in ("days", "monthly_tokens"):
            if args.get(k) in ("", None):
                args.pop(k, None)
        if "monthly_usd" in args:
            args["monthly_usd"] = float(args["monthly_usd"])
        if "enabled" in args:
            args["enabled"] = str(args["enabled"]).lower() in ("true", "1", "yes", "on")
        if "percent" in args and args["percent"] in ("", None):
            args.pop("percent")
        try:
            result = tool.fn(**args, actor=ident, source="assistant")
        except TypeError as e:
            raise ActionError(f"the proposal has invalid arguments: {e}")
        return {"proposal_id": proposal_id, "summary": p["summary"], "result": result}

    def dismiss(self, proposal_id: str) -> None:
        self.proposals.pop(proposal_id, None)


def _parse(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t).strip()
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else {"answer": str(v)}
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
    return {"answer": text.strip(), "_raw": text}


def _brief(r: Any) -> str:
    if isinstance(r, list):
        return f"{len(r)} rows"
    if isinstance(r, dict):
        if "error" in r:
            return str(r["error"])[:100]
        return ", ".join(list(r)[:5])
    return str(r)[:80]
