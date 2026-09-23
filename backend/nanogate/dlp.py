"""Layered DLP: normalization -> regex/checksum recognizers -> secret patterns -> Presidio NER.

Never stores raw sensitive values: findings carry entity type, offsets, the detecting layer
and a salted SHA-256 fingerprint. Detector availability is explicit so the policy engine
can fail closed.
"""
from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from typing import Callable

SECRET_TYPES = {"API_KEY", "PRIVATE_KEY", "PASSWORD", "JWT", "CLOUD_CREDENTIAL", "DB_CREDENTIAL", "AUTH_TOKEN"}
RESTRICTED_TYPES = {"US_SSN", "CREDIT_CARD", "IBAN_CODE", "US_PASSPORT"}
CONFIDENTIAL_TYPES = {"EMAIL_ADDRESS", "PHONE_NUMBER", "EMPLOYEE_ID", "IP_ADDRESS", "DATE_OF_BIRTH", "US_BANK_NUMBER"}
PII_TYPES = RESTRICTED_TYPES | CONFIDENTIAL_TYPES | {"PERSON"}


def luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    s = 0
    for i, x in enumerate(reversed(d)):
        if i % 2:
            x *= 2
            if x > 9:
                x -= 9
        s += x
    return s % 10 == 0


def ssn_ok(s: str) -> bool:
    d = re.sub(r"\D", "", s)
    if len(d) != 9:
        return False
    a, g, n = d[:3], d[3:5], d[5:]
    return a not in ("000", "666") and not a.startswith("9") and g != "00" and n != "0000"


@dataclass
class Finding:
    type: str
    start: int
    end: int
    layer: str
    fingerprint: str
    score: float = 1.0

    def public(self) -> dict:
        return {"type": self.type, "start": self.start, "end": self.end, "layer": self.layer,
                "fingerprint": self.fingerprint[:16], "score": round(self.score, 3)}


@dataclass
class DlpResult:
    findings: list[Finding] = field(default_factory=list)
    available: bool = True
    unavailable_reason: str | None = None
    layers_used: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def counts(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for f in self.findings:
            c[f.type] = c.get(f.type, 0) + 1
        return c

    @property
    def has_secret(self) -> bool:
        return any(f.type in SECRET_TYPES for f in self.findings)

    @property
    def has_pii(self) -> bool:
        types = {f.type for f in self.findings}
        if types & (RESTRICTED_TYPES | CONFIDENTIAL_TYPES):
            return True
        return False

    @property
    def data_class(self) -> str:
        types = {f.type for f in self.findings}
        if types & SECRET_TYPES:
            return "Secret"
        if types & RESTRICTED_TYPES:
            return "Restricted"
        if types & CONFIDENTIAL_TYPES:
            return "Confidential"
        return "Internal"

    def public(self) -> dict:
        return {"available": self.available, "unavailable_reason": self.unavailable_reason,
                "data_class": self.data_class if self.available else "Unknown", "counts": self.counts,
                "has_secret": self.has_secret, "has_pii": self.has_pii, "layers": self.layers_used,
                "findings": [f.public() for f in self.findings], "elapsed_ms": round(self.elapsed_ms, 2)}


# ---- recognizers -----------------------------------------------------------------------------
R = re.compile
REGEX_RULES: list[tuple[str, re.Pattern, Callable[[str], bool] | None]] = [
    ("EMAIL_ADDRESS", R(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), None),
    ("US_SSN", R(r"\b\d{3}[- .]\d{2}[- .]\d{4}\b"), ssn_ok),
    ("US_SSN", R(r"(?i)(?:ssn|social security(?: number)?)\s*(?:is|:|#)?\s*(\d{9})\b"), ssn_ok),
    ("CREDIT_CARD", R(r"\b(?:\d[ -]?){12,18}\d\b"), luhn_ok),
    ("PHONE_NUMBER", R(r"(?<!\d)(?:\+?1[ .\-]?)?\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4}(?!\d)"), None),
    ("PHONE_NUMBER", R(r"(?<!\w)\+\d{1,3}[ .\-]?\d{2,4}[ .\-]?\d{3,4}[ .\-]?\d{3,4}(?!\d)"), None),
    ("IP_ADDRESS", R(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"), None),
    ("EMPLOYEE_ID", R(r"\b(?:EMP|E)-?\d{5,7}\b"), None),
    ("DATE_OF_BIRTH", R(r"(?i)\b(?:dob|date of birth|born on)\s*[:\-]?\s*\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}"), None),
    ("IBAN_CODE", R(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b"), None),
]

SECRET_RULES: list[tuple[str, re.Pattern]] = [
    ("PRIVATE_KEY", R(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----")),
    ("CLOUD_CREDENTIAL", R(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("CLOUD_CREDENTIAL", R(r"(?i)aws_secret_access_key\s*[=:]\s*[A-Za-z0-9/+=]{30,}")),
    ("CLOUD_CREDENTIAL", R(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("API_KEY", R(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}\b")),
    ("API_KEY", R(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("API_KEY", R(r"\bng_live_[a-f0-9]{8}_[A-Za-z0-9_\-]{16,}\b")),
    ("CLOUD_CREDENTIAL", R(r"(?i)\bAccountKey=[A-Za-z0-9+/=]{20,}")),
    ("CLOUD_CREDENTIAL", R(r"(?i)\bSharedAccessSignature=|\bsig=[A-Za-z0-9%+/=]{30,}")),
    ("API_KEY", R(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b")),
    ("API_KEY", R(r"\b(?:hf_|glpat-|npm_)[A-Za-z0-9_\-]{20,}\b")),
    ("AUTH_TOKEN", R(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("AUTH_TOKEN", R(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")),
    ("AUTH_TOKEN", R(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    ("JWT", R(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("DB_CREDENTIAL", R(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mssql|amqp)://[^\s:/@]+:[^\s@/]+@[^\s]+")),
    ("PASSWORD", R(r"(?i)\b(?:password|passwd|pwd|passphrase|pass)\b\s*(?:is|=|:)\s*['\"]?(?!\s)(?=[^\s'\"]*[\d\W_])[^\s'\"]{6,}")),
    ("API_KEY", R(r"(?i)\b(?:api[_\- ]?key|secret[_\- ]?key|access[_\- ]?token|client[_\- ]?secret)\b\s*(?:is|=|:)\s*['\"]?[A-Za-z0-9_\-/+=]{16,}")),
]

# De-obfuscation: "jane [at] acme [dot] com", "j.doe at acme dot com", digit-spacing tricks.
OBF_AT = R(r"\s*(?:\[at\]|\(at\)|\{at\}|\s+at\s+)\s*", re.I)
OBF_DOT = R(r"\s*(?:\[dot\]|\(dot\)|\{dot\}|\s+dot\s+)\s*", re.I)
SPACED_DIGITS = R(r"(?<!\d)(?:\d[\s_]){8,18}\d(?!\d)")
WORD_DIGITS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
               "seven": "7", "eight": "8", "nine": "9", "oh": "0"}
WORD_DIGIT_RUN = R(r"\b(?:(?:zero|one|two|three|four|five|six|seven|eight|nine|oh)[\s\-]+){8,}(?:zero|one|two|three|four|five|six|seven|eight|nine|oh)\b", re.I)


def _fp(salt: bytes, value: str) -> str:
    return hashlib.sha256(salt + value.encode()).hexdigest()


class LayeredDLP:
    PRESIDIO_ENTITIES = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN", "CREDIT_CARD", "IP_ADDRESS",
                         "IBAN_CODE", "US_PASSPORT", "US_BANK_NUMBER"]

    def __init__(self, salt: bytes = b"nanogate-dlp", use_presidio: bool = True, use_regex: bool = True,
                 use_secrets: bool = True, use_deobfuscation: bool = True, presidio_min_score: float = 0.6):
        self.salt = salt
        self.use_presidio, self.use_regex, self.use_secrets = use_presidio, use_regex, use_secrets
        self.use_deobfuscation = use_deobfuscation
        self.presidio_min_score = presidio_min_score
        self._analyzer = None
        self._lock = threading.Lock()
        self.presidio_error: str | None = None
        self.forced_unavailable: str | None = None   # test hook to exercise fail-closed paths

    def load(self) -> None:
        if not self.use_presidio or self._analyzer is not None:
            return
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider
            nlp = NlpEngineProvider(nlp_configuration={
                "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]}).create_engine()
            self._analyzer = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
        except Exception as e:
            self.presidio_error = f"{type(e).__name__}: {e}"[:300]

    @property
    def available(self) -> bool:
        return self.forced_unavailable is None and (not self.use_presidio or self._analyzer is not None or
                                                     self.presidio_error is None)

    def status(self) -> dict:
        return {"available": self.available and self.forced_unavailable is None,
                "layers": {"regex": self.use_regex, "secrets": self.use_secrets, "deobfuscation": self.use_deobfuscation,
                           "presidio": self._analyzer is not None},
                "presidio_error": self.presidio_error, "forced_unavailable": self.forced_unavailable,
                "nlp_model": "spaCy en_core_web_sm" if self._analyzer is not None else None}

    def scan(self, text: str) -> DlpResult:
        import time
        t0 = time.perf_counter()
        res = DlpResult()
        if self.forced_unavailable:
            res.available, res.unavailable_reason = False, self.forced_unavailable
            return res
        if self.use_presidio and self._analyzer is None:
            self.load()
            if self._analyzer is None:
                res.available, res.unavailable_reason = False, f"Presidio unavailable: {self.presidio_error}"
                return res
        found: list[Finding] = []

        def add(t: str, s: int, e: int, layer: str, score: float = 1.0, value: str | None = None):
            found.append(Finding(t, s, e, layer, _fp(self.salt, value if value is not None else text[s:e]), score))

        if self.use_secrets:
            res.layers_used.append("secrets")
            for t, rx in SECRET_RULES:
                for m in rx.finditer(text):
                    add(t, m.start(), m.end(), "secrets")
        if self.use_regex:
            res.layers_used.append("regex")
            for t, rx, check in REGEX_RULES:
                for m in rx.finditer(text):
                    val = m.group(1) if m.groups() else m.group(0)
                    if check and not check(val):
                        continue
                    add(t, m.start(), m.end(), "regex")
        if self.use_deobfuscation:
            res.layers_used.append("deobfuscation")
            self._deobfuscated(text, add)
        if self._analyzer is not None:
            res.layers_used.append("presidio")
            with self._lock:
                for r in self._analyzer.analyze(text=text, language="en", entities=self.PRESIDIO_ENTITIES):
                    if r.score < self.presidio_min_score:
                        continue
                    if r.entity_type == "CREDIT_CARD" and not luhn_ok(text[r.start:r.end]):
                        continue
                    if r.entity_type == "US_SSN" and not ssn_ok(text[r.start:r.end]):
                        continue
                    add(r.entity_type, r.start, r.end, "presidio", r.score)
        res.findings = self._merge(found)
        res.elapsed_ms = (time.perf_counter() - t0) * 1000
        return res

    def _deobfuscated(self, text: str, add) -> None:
        # Obfuscated emails: normalize "[at]"/"dot" then look for an email spanning the region.
        if OBF_AT.search(text):
            norm = OBF_DOT.sub(".", OBF_AT.sub("@", text))
            for m in REGEX_RULES[0][1].finditer(norm):
                raw_start = max(0, text.lower().find(m.group(0).split("@")[0].lower()))
                add("EMAIL_ADDRESS", raw_start, min(len(text), raw_start + len(m.group(0)) + 12), "deobfuscation",
                    value=m.group(0))
        for m in SPACED_DIGITS.finditer(text):
            d = re.sub(r"\D", "", m.group(0))
            if len(d) == 9 and ssn_ok(d):
                add("US_SSN", m.start(), m.end(), "deobfuscation", value=d)
            elif luhn_ok(d):
                add("CREDIT_CARD", m.start(), m.end(), "deobfuscation", value=d)
        for m in WORD_DIGIT_RUN.finditer(text):
            d = "".join(WORD_DIGITS[w.lower()] for w in re.findall(r"[A-Za-z]+", m.group(0)))
            if len(d) == 9 and ssn_ok(d):
                add("US_SSN", m.start(), m.end(), "deobfuscation", value=d)
            elif luhn_ok(d):
                add("CREDIT_CARD", m.start(), m.end(), "deobfuscation", value=d)

    @staticmethod
    def _merge(found: list[Finding]) -> list[Finding]:
        """Resolve overlaps: secrets > restricted > confidential > person; longer span wins ties."""
        prio = lambda f: (0 if f.type in SECRET_TYPES else 1 if f.type in RESTRICTED_TYPES else
                          2 if f.type in CONFIDENTIAL_TYPES else 3, -(f.end - f.start))
        out: list[Finding] = []
        for f in sorted(found, key=prio):
            if any(not (f.end <= g.start or f.start >= g.end) for g in out):
                continue
            out.append(f)
        return sorted(out, key=lambda f: f.start)

    # ---- transformations ------------------------------------------------------------------
    @staticmethod
    def redact(text: str, findings: list[Finding], types: set[str] | None = None) -> str:
        out, last = [], 0
        for f in sorted(findings, key=lambda f: f.start):
            if types is not None and f.type not in types:
                continue
            out.append(text[last:f.start])
            out.append(f"[{f.type}]")
            last = f.end
        out.append(text[last:])
        return "".join(out)

    @staticmethod
    def tokenize(text: str, findings: list[Finding], types: set[str] | None = None) -> tuple[str, dict[str, str]]:
        """Reversible placeholders; the mapping lives only in request memory (never persisted)."""
        mapping: dict[str, str] = {}
        counters: dict[str, int] = {}
        out, last = [], 0
        for f in sorted(findings, key=lambda f: f.start):
            if types is not None and f.type not in types:
                continue
            counters[f.type] = counters.get(f.type, 0) + 1
            tok = f"<{f.type}_{counters[f.type]}>"
            mapping[tok] = text[f.start:f.end]
            out.append(text[last:f.start])
            out.append(tok)
            last = f.end
        out.append(text[last:])
        return "".join(out), mapping

    @staticmethod
    def detokenize(text: str, mapping: dict[str, str]) -> str:
        for tok, val in mapping.items():
            text = text.replace(tok, val)
        return text
