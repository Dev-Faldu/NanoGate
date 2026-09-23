"""Atomic reserve-and-settle budget accounting (per tenant/department/month).

reserve(): conservative maximum (prompt estimate + max_tokens at the most expensive permitted
route) is checked against limit - spent - reserved inside a BEGIN IMMEDIATE transaction, so
concurrent requests can never jointly overspend. settle(): records actual usage and releases
the unused part of the reservation. release(): cancels a reservation (errors, cache hits).
"""
from __future__ import annotations

import datetime as dt
import time
import uuid
from dataclasses import dataclass

from .db import Database


class BudgetDenied(Exception):
    def __init__(self, message: str, detail: dict):
        super().__init__(message)
        self.detail = detail


@dataclass
class Reservation:
    reservation_id: str
    request_id: str
    tenant_id: str
    department_id: str
    period: str
    usd: float
    tokens: int


def period_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")


class BudgetEngine:
    def __init__(self, db: Database):
        self.db = db

    def ensure_account(self, c, tenant_id: str, department_id: str, period: str, limit_usd: float, limit_tokens: int):
        c.execute("INSERT OR IGNORE INTO budget_accounts(tenant_id, department_id, period, limit_usd, limit_tokens)"
                  " VALUES (?,?,?,?,?)", (tenant_id, department_id, period, limit_usd, limit_tokens))
        # Limits follow the currently published policy.
        c.execute("UPDATE budget_accounts SET limit_usd=?, limit_tokens=? WHERE tenant_id=? AND department_id=? AND period=?",
                  (limit_usd, limit_tokens, tenant_id, department_id, period))

    def reserve(self, request_id: str, tenant_id: str, department_id: str, usd: float, tokens: int,
                limit_usd: float, limit_tokens: int) -> Reservation:
        period = period_now()
        rid = "r_" + uuid.uuid4().hex[:12]
        with self.db.tx() as c:
            self.ensure_account(c, tenant_id, department_id, period, limit_usd, limit_tokens)
            a = c.execute("SELECT * FROM budget_accounts WHERE tenant_id=? AND department_id=? AND period=?",
                          (tenant_id, department_id, period)).fetchone()
            usd_left = a["limit_usd"] - a["spent_usd"] - a["reserved_usd"]
            tok_left = a["limit_tokens"] - a["spent_tokens"] - a["reserved_tokens"]
            if usd > usd_left + 1e-12 or tokens > tok_left:
                c.execute("INSERT INTO budget_ledger(reservation_id, request_id, tenant_id, department_id, period, kind, usd,"
                          " tokens, ts) VALUES (?,?,?,?,?,?,?,?,?)",
                          (rid, request_id, tenant_id, department_id, period, "deny", usd, tokens, time.time()))
                raise BudgetDenied("department budget exhausted", {
                    "requested_usd": usd, "available_usd": max(0.0, usd_left), "requested_tokens": tokens,
                    "available_tokens": max(0, tok_left), "period": period})
            c.execute("UPDATE budget_accounts SET reserved_usd=reserved_usd+?, reserved_tokens=reserved_tokens+? "
                      "WHERE tenant_id=? AND department_id=? AND period=?", (usd, tokens, tenant_id, department_id, period))
            c.execute("INSERT INTO budget_ledger(reservation_id, request_id, tenant_id, department_id, period, kind, usd, tokens,"
                      " ts) VALUES (?,?,?,?,?,?,?,?,?)",
                      (rid, request_id, tenant_id, department_id, period, "reserve", usd, tokens, time.time()))
        return Reservation(rid, request_id, tenant_id, department_id, period, usd, tokens)

    def settle(self, r: Reservation, actual_usd: float, actual_tokens: int) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE budget_accounts SET reserved_usd=MAX(0, reserved_usd-?), reserved_tokens=MAX(0, reserved_tokens-?),"
                      " spent_usd=spent_usd+?, spent_tokens=spent_tokens+? WHERE tenant_id=? AND department_id=? AND period=?",
                      (r.usd, r.tokens, actual_usd, actual_tokens, r.tenant_id, r.department_id, r.period))
            c.execute("INSERT INTO budget_ledger(reservation_id, request_id, tenant_id, department_id, period, kind, usd, tokens,"
                      " ts) VALUES (?,?,?,?,?,?,?,?,?)",
                      (r.reservation_id, r.request_id, r.tenant_id, r.department_id, r.period, "settle", actual_usd,
                       actual_tokens, time.time()))

    def release(self, r: Reservation) -> None:
        self.settle(r, 0.0, 0)

    def accounts(self, tenant_id: str | None = None) -> list[dict]:
        q = "SELECT * FROM budget_accounts WHERE period=?"
        args: tuple = (period_now(),)
        if tenant_id:
            q += " AND tenant_id=?"
            args += (tenant_id,)
        return self.db.all(q + " ORDER BY tenant_id, department_id", args)
