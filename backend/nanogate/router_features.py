"""Router feature schema + extraction. Shared verbatim by training (bench/) and serving.

Every feature has a value and, when it can be missing, a `<name>__missing` indicator.
Missing values are imputed with a fixed constant recorded in the schema.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .inference import GenerationResult, logprob_stats

SCHEMA_VERSION = "rf-1.1"

INTENTS = ["mcq", "math", "code", "lookup", "howto", "general"]
DATA_CLASSES = ["Public", "Internal", "Confidential", "Restricted", "Secret"]

REFUSAL_RE = re.compile(r"\b(i can(?:'|no)t|i am unable|i'm unable|i cannot|i do not have (?:access|information)|as an ai)\b", re.I)
HEDGE_RE = re.compile(r"\b(might|may|possibly|perhaps|not sure|unclear|i think|likely|approximately|it depends)\b", re.I)
CODE_RE = re.compile(r"```|\bdef |\bclass |\bfunction\b|\bSELECT\b|\bpython\b|\bjavascript\b|\bregex\b|\bscript\b", re.I)
MCQ_RE = re.compile(r"(^|\n)\s*[A-D][\).:]\s", re.M)
MATH_RE = re.compile(r"\d+(\.\d+)?\s*(%|\$|dollars|hours|minutes|per|each|times|\+|\-|\*|/)|how (many|much)", re.I)
LOOKUP_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b|\bKEV\b|\bwhich vendor\b|\bwhat product\b|\bwhen was\b", re.I)
HOWTO_RE = re.compile(r"^\s*(how (do|can|should) i|what steps|steps to|how to)\b", re.I)
LENGTH_RE = re.compile(r"\b(\d{2,4})\s*words\b|\b(brief|short|concise|one sentence|detailed|in depth|comprehensive)\b", re.I)


def intent_of(text: str) -> str:
    if MCQ_RE.search(text):
        return "mcq"
    if CODE_RE.search(text):
        return "code"
    if LOOKUP_RE.search(text):
        return "lookup"
    if MATH_RE.search(text):
        return "math"
    if HOWTO_RE.search(text):
        return "howto"
    return "general"


def requested_length(text: str) -> float:
    m = LENGTH_RE.search(text)
    if not m:
        return 0.0
    if m.group(1):
        return min(int(m.group(1)) / 500.0, 2.0)
    w = m.group(2).lower()
    return 1.5 if w in ("detailed", "in depth", "comprehensive") else 0.2


# (name, missing_allowed, impute_value)
NUMERIC_FEATURES: list[tuple[str, bool, float]] = [
    ("req_tokens_log", False, 0.0), ("req_messages", False, 1.0), ("req_structured", False, 0.0),
    ("req_tool", False, 0.0), ("req_code", False, 0.0), ("req_length", False, 0.0), ("req_has_system", False, 0.0),
    ("ret_top_sim", True, 0.0), ("ret_margin", True, 0.0), ("ret_coverage", True, 0.0), ("ret_verifier", True, 0.0),
    ("ret_sources", True, 0.0), ("ret_age_days_log", True, 0.0), ("ret_mismatch", True, 0.0),
    ("gen_mean_logprob", True, 0.0), ("gen_min_logprob", True, 0.0), ("gen_p10_logprob", True, 0.0),
    ("gen_mean_entropy", True, 0.0), ("gen_mean_margin", True, 0.0), ("gen_frac_low_conf", True, 0.0),
    ("gen_stop_length", False, 0.0), ("gen_out_tokens_log", False, 0.0), ("gen_schema_valid", True, 0.0),
    ("gen_refusal", False, 0.0), ("gen_hedge_rate", False, 0.0), ("gen_truncated_ctx", False, 0.0),
    ("gen_answer_marker", False, 0.0), ("gen_self_contradiction", False, 0.0),
    ("gen_answer_logprob", True, 0.0), ("gen_answer_margin", True, 0.0),
    ("sys_queue_depth", True, 0.0), ("sys_ttft_log", True, 0.0), ("sys_mem_pressure", True, 0.0),
]
CATEGORICAL = {"intent": INTENTS, "data_class": DATA_CLASSES}


def feature_names() -> list[str]:
    names = []
    for n, miss, _ in NUMERIC_FEATURES:
        names.append(n)
        if miss:
            names.append(f"{n}__missing")
    for k, vals in CATEGORICAL.items():
        names += [f"{k}={v}" for v in vals]
    return names


def schema() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "features": feature_names(),
            "impute": {n: v for n, _, v in NUMERIC_FEATURES}, "categorical": CATEGORICAL}


def _log1p(x: float | None) -> float | None:
    import math
    return None if x is None else math.log1p(max(0.0, x))


def extract(messages: list[dict], params: dict[str, Any], gen: GenerationResult | None,
            retrieval: dict[str, Any] | None = None, system: dict[str, Any] | None = None,
            data_class: str | None = None) -> dict[str, Any]:
    """Raw feature dict (None = missing)."""
    user_text = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
    last_user = next((str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"), "")
    approx_tokens = max(1, len(user_text) // 4)
    rf = params.get("response_format") or {}
    structured = 1.0 if (isinstance(rf, dict) and rf.get("type") in ("json_object", "json_schema")) or \
        re.search(r"\bjson\b", last_user, re.I) else 0.0
    f: dict[str, Any] = {
        "req_tokens_log": _log1p(gen.prompt_tokens if gen and gen.prompt_tokens else approx_tokens),
        "req_messages": float(len(messages)),
        "req_structured": structured,
        "req_tool": 1.0 if params.get("tools") else 0.0,
        "req_code": 1.0 if CODE_RE.search(last_user) else 0.0,
        "req_length": requested_length(last_user),
        "req_has_system": 1.0 if any(m.get("role") == "system" for m in messages) else 0.0,
        "intent": intent_of(last_user),
        "data_class": data_class or "Internal",
    }
    r = retrieval or {}
    f.update({
        "ret_top_sim": r.get("top_sim"), "ret_margin": r.get("margin"), "ret_coverage": r.get("coverage"),
        "ret_verifier": r.get("verifier"), "ret_sources": r.get("sources"),
        "ret_age_days_log": _log1p(r.get("age_days")), "ret_mismatch": r.get("mismatch"),
    })
    if gen is not None:
        st = logprob_stats(gen)
        text = gen.text or ""
        words = max(1, len(text.split()))
        schema_valid = None
        if structured:
            try:
                json.loads(text.strip().strip("`").removeprefix("json").strip())
                schema_valid = 1.0
            except Exception:
                schema_valid = 0.0
        f.update({
            "gen_mean_logprob": st["mean_logprob"], "gen_min_logprob": st["min_logprob"],
            "gen_p10_logprob": st["p10_logprob"], "gen_mean_entropy": st["mean_entropy"],
            "gen_mean_margin": st["mean_margin"], "gen_frac_low_conf": st["frac_low_conf"],
            "gen_stop_length": 1.0 if gen.finish_reason == "length" else 0.0,
            "gen_out_tokens_log": _log1p(gen.completion_tokens or len(text) // 4),
            "gen_schema_valid": schema_valid,
            "gen_refusal": 1.0 if REFUSAL_RE.search(text) else 0.0,
            "gen_hedge_rate": min(1.0, len(HEDGE_RE.findall(text)) / words * 20),
            "gen_truncated_ctx": 0.0,
            "gen_answer_marker": 1.0 if re.search(r"answer\s*[:：]", text, re.I) else 0.0,
            "gen_self_contradiction": 1.0 if _self_contradiction(text) else 0.0,
        })
        alp, amg = answer_token_stats(gen)
        f.update({"gen_answer_logprob": alp, "gen_answer_margin": amg})
    s = system or {}
    f.update({"sys_queue_depth": s.get("queue_depth"), "sys_ttft_log": _log1p(gen.ttft_ms if gen else None),
              "sys_mem_pressure": s.get("mem_pressure")})
    return f


ANSWER_MARK = re.compile(r"answer\s*(?:is)?\s*[:：]?[\s*(<\[]*", re.I)


def answer_token_stats(gen: GenerationResult) -> tuple[float | None, float | None]:
    """Log-prob and top-1/top-2 margin of the first content token after the final 'Answer:' marker."""
    if not gen.tokens or len(gen.tokens) != len(gen.token_logprobs):
        return None, None
    text = "".join(gen.tokens)
    marks = list(ANSWER_MARK.finditer(text))
    if not marks:
        return None, None
    pos, start = marks[-1].end(), 0
    for i, tok in enumerate(gen.tokens):
        end = start + len(tok)
        if end > pos and tok.strip(" *(<[:"):
            tops = sorted(gen.top_logprobs[i], reverse=True) if i < len(gen.top_logprobs) else []
            margin = (tops[0] - tops[1]) if len(tops) > 1 else None
            return gen.token_logprobs[i], margin
        start = end
    return None, None


def _self_contradiction(text: str) -> bool:
    """Multiple distinct final-answer markers (e.g. 'Answer: B' ... 'Answer: C')."""
    ans = re.findall(r"answer\s*(?:is)?\s*[:：]?\s*\(?([A-D])\)?\b", text, re.I)
    return len(set(a.upper() for a in ans)) > 1


def vectorize(f: dict[str, Any]) -> list[float]:
    out: list[float] = []
    for n, miss, imp in NUMERIC_FEATURES:
        v = f.get(n)
        out.append(float(imp if v is None else v))
        if miss:
            out.append(1.0 if v is None else 0.0)
    for k, vals in CATEGORICAL.items():
        out += [1.0 if f.get(k) == v else 0.0 for v in vals]
    return out
