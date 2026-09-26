"""Semantic-equivalence verifier for cache reuse.

Two independent layers, both must pass:
 1. NLI cross-encoder (cross-encoder/nli-deberta-v3-base, local):
      score = max(P_entail(a->b), P_entail(b->a)) * (1 - max(P_contra(a->b), P_contra(b->a)))
 2. Structured slot comparison: subject/ownership, authorization scope, action, credential object,
    polarity, timeframe, entities/numbers, requested output type. Any conflict vetoes reuse.
Evidence for every decision is returned for the receipt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

OTHER_OWNER = re.compile(r"\b(another|other|someone(?: else)?(?:'s)?|somebody(?:'s)?|his|her|their|"
                         r"(?:colleague|coworker|co-worker|employee|user|manager|boss|ceo|admin|contractor)(?:'s|s'))(?=\W|$)", re.I)
GROUP_POSSESSIVE = re.compile(r"\b(\w+)\s+(?:team|department|dept|group|org|division|unit)(?:'s|s')", re.I)
NAME_POSSESSIVE = re.compile(r"\b(?!(?:It|What|That|There|Here|Let|Who|Where|How|He|She|Today|Everyone)'s)[A-Z][a-z]+'s\b")
SELF_OWNER = re.compile(r"\b(my|mine|i|me|myself|our)\b", re.I)
BROAD_SCOPE = re.compile(r"\b(all|every|everyone'?s?|company[- ]wide|organi[sz]ation[- ]wide|team[- ]wide|entire|bulk|"
                         r"whole(?:\s+\w+)?\s+(?:team|department|company|organi[sz]ation|org|region|domain)|"
                         r"all users|every user|each user)\b", re.I)
NEGATION = re.compile(r"\b(not|never|no|without|n't|cannot|can't|don't|doesn't|isn't|won't|stop|disable|prevent)\b", re.I)
TIMEFRAME = re.compile(r"\b(today|yesterday|tomorrow|last (?:week|month|year|quarter)|next (?:week|month|year|quarter)|"
                       r"this (?:week|month|year|quarter)|q[1-4]|fy\d{2,4}|(?:19|20)\d{2}|january|february|march|april|may|"
                       r"june|july|august|september|october|november|december)\b", re.I)
NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
CVE = re.compile(r"CVE-\d{4}-\d{4,}", re.I)
CREDENTIAL_OBJ = re.compile(r"\b(password|passcode|passphrase|credential|credentials|pin|mfa|2fa|otp|token|secret|"
                            r"private key|ssh key|api key|badge|(?:two|multi|2)[- ]?(?:factor|step)(?: authentication| verification)?)\b", re.I)
_MAKE = r"\b(?:write|draft|compose|create|generate|produce|give me|prepare|format|output|return|reply with)\b[^.?!]{0,40}"
OUTPUT_TYPE = {   # requested FORM of the answer (not a topic): "draft an email", "as a table", "in JSON"
    "email": re.compile(_MAKE + r"\b(?:e-?mail|letter|memo|message)\b", re.I),
    "table": re.compile(r"\b(?:as|in|into) (?:a )?(?:table|spreadsheet|csv)\b|" + _MAKE + r"\btable\b", re.I),
    "json": re.compile(r"\b(?:as|in|into) (?:valid )?(?:json|yaml)\b|" + _MAKE + r"\b(?:json|yaml)\b", re.I),
    "code": re.compile(_MAKE + r"\b(?:code|script|function|regex|sql|query)\b", re.I),
    "list": re.compile(r"\b(?:as|in) (?:a )?(?:bulleted |numbered )?list\b|\bbullet points?\b", re.I),
    "summary": re.compile(r"\b(?:summari[sz]e|summary|tl;?dr)\b", re.I),
}
ACTION_GROUPS = {
    "restore": {"reset", "restore", "fix", "repair", "reconnect", "troubleshoot", "recover", "resolve", "restart", "reboot"},
    "remove": {"delete", "remove", "uninstall", "revoke", "disable", "deactivate", "terminate", "wipe", "purge", "cancel",
               "reject", "deny", "decline", "block", "hide", "stop", "turn off", "opt out"},
    "create": {"create", "install", "add", "set up", "setup", "provision", "enable", "configure", "register"},
    "access": {"access", "view", "read", "see", "open", "download", "export", "share", "get"},
    "change": {"change", "update", "modify", "edit", "rotate", "rename", "move", "transfer"},
    "request": {"request", "apply", "ask", "submit", "book", "file"},
}


def _action(text: str) -> set[str]:
    t = text.lower()
    return {g for g, words in ACTION_GROUPS.items() if any(re.search(rf"\b{re.escape(w)}", t) for w in words)}


def _other_group(text: str) -> bool:
    """'the finance team's drive' belongs to someone else; 'my/our team's' does not."""
    return any(m.group(1).lower() not in ("my", "our") for m in GROUP_POSSESSIVE.finditer(text))


def slots(text: str) -> dict:
    return {
        "owner_other": bool(OTHER_OWNER.search(text) or NAME_POSSESSIVE.search(text) or _other_group(text)),
        "owner_self": bool(SELF_OWNER.search(text)),
        "broad_scope": bool(BROAD_SCOPE.search(text)),
        "negated": bool(NEGATION.search(text)),
        "timeframe": sorted({m.lower() for m in TIMEFRAME.findall(text)}),
        "numbers": sorted(set(NUMBER.findall(text))),
        "cves": sorted({c.upper() for c in CVE.findall(text)}),
        "credential_object": sorted({m.lower() for m in CREDENTIAL_OBJ.findall(text)}),
        "actions": sorted(_action(text)),
        "output_types": sorted(k for k, rx in OUTPUT_TYPE.items() if rx.search(text)),
    }


def slot_conflicts(a: dict, b: dict) -> list[str]:
    c = []
    if a["owner_other"] != b["owner_other"]:
        c.append("ownership: one request targets another person's resource")
    if a["broad_scope"] != b["broad_scope"]:
        c.append("scope/authorization: one request is organization-wide")
    if (a["owner_other"] or b["owner_other"] or a["broad_scope"] or b["broad_scope"]) and \
            (a["credential_object"] or b["credential_object"]):
        if a["owner_other"] != b["owner_other"] or a["broad_scope"] != b["broad_scope"]:
            c.append("authorization: credential operation on a different principal")
    if bool(a["credential_object"]) != bool(b["credential_object"]):
        c.append("object: credential vs non-credential target")
    if a["negated"] != b["negated"]:
        c.append("polarity: negation differs")
    if a["timeframe"] != b["timeframe"] and (a["timeframe"] or b["timeframe"]):
        c.append(f"timeframe: {a['timeframe']} vs {b['timeframe']}")
    if a["numbers"] != b["numbers"] and (a["numbers"] or b["numbers"]):
        c.append(f"entities: numbers {a['numbers']} vs {b['numbers']}")
    if a["cves"] != b["cves"]:
        c.append(f"entities: CVE {a['cves']} vs {b['cves']}")
    # Only destructive/negating actions are a hard conflict; other verb differences are left to the NLI model.
    if ("remove" in a["actions"]) != ("remove" in b["actions"]):
        c.append(f"intent: action {a['actions']} vs {b['actions']}")
    if a["output_types"] != b["output_types"]:
        c.append(f"output type: {a['output_types']} vs {b['output_types']}")
    return c


@dataclass
class Verdict:
    equivalent: bool
    score: float
    entail_ab: float
    entail_ba: float
    contra: float
    conflicts: list[str] = field(default_factory=list)
    slots_a: dict = field(default_factory=dict)
    slots_b: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {"equivalent": self.equivalent, "score": round(self.score, 4), "entail_ab": round(self.entail_ab, 4),
                "entail_ba": round(self.entail_ba, 4), "contradiction": round(self.contra, 4),
                "conflicts": self.conflicts, "slots_query": self.slots_a, "slots_candidate": self.slots_b}


class Verifier:
    def __init__(self, threshold: float = 0.5, use_slots: bool = True):
        self.threshold = threshold
        self.use_slots = use_slots

    def nli(self, pairs: list[tuple[str, str]]) -> np.ndarray:
        from .embeddings import nli_verifier
        m = nli_verifier()
        logits = np.asarray(m.predict(pairs, show_progress_bar=False, batch_size=32))
        e = np.exp(logits - logits.max(axis=1, keepdims=True))
        return e / e.sum(axis=1, keepdims=True)   # columns: contradiction, entailment, neutral

    def score_many(self, pairs: list[tuple[str, str]]) -> list[Verdict]:
        if not pairs:
            return []
        flat = []
        for a, b in pairs:
            flat += [(a, b), (b, a)]
        p = self.nli(flat)
        out = []
        for i, (a, b) in enumerate(pairs):
            ab, ba = p[2 * i], p[2 * i + 1]
            ent_ab, ent_ba = float(ab[1]), float(ba[1])
            contra = float(max(ab[0], ba[0]))
            score = max(ent_ab, ent_ba) * (1.0 - contra)
            sa, sb = slots(a), slots(b)
            conflicts = slot_conflicts(sa, sb) if self.use_slots else []
            out.append(Verdict(score >= self.threshold and not conflicts, score, ent_ab, ent_ba, contra, conflicts, sa, sb))
        return out

    def verify(self, a: str, b: str) -> Verdict:
        return self.score_many([(a, b)])[0]
