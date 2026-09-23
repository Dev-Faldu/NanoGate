"""Generate SYNTHETIC SECURITY EVALUATION DATA with ground-truth spans (deterministic, seeded).

Real public data is unsuitable for secrets and personal identifiers, so every sensitive value here
is randomly generated: format-valid (Luhn-valid cards on test BINs, structurally valid SSNs,
random API keys) but not tied to any person or account. Hard negatives are look-alikes that must
NOT be flagged (order numbers, ISBNs, versions, the word "password" without a value, non-Luhn
digit runs). Public negatives are sampled from MMLU questions.

Output: datasets/synthetic/dlp_eval.jsonl  ({id, text, spans:[{start,end,category}], kind, source})
"""
from __future__ import annotations

import base64
import json
import random
import string
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "datasets" / "synthetic" / "dlp_eval.jsonl"
SEED = 1337
FIRST = ["Maria", "James", "Aiko", "Omar", "Priya", "Lukas", "Chen", "Fatima", "Diego", "Grace", "Noah", "Ines"]
LAST = ["Lopez", "Okafor", "Tanaka", "Haddad", "Iyer", "Novak", "Wei", "Rahman", "Silva", "Kim", "Berg", "Moreau"]
DOMAINS = ["acme-corp.example", "globex.example", "initech.example", "example.org", "mail.example.com"]


def luhn_complete(prefix: str, length: int, rng: random.Random) -> str:
    digits = prefix + "".join(rng.choice(string.digits) for _ in range(length - len(prefix) - 1))
    s = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return digits + str((10 - s % 10) % 10)


def gen_values(rng: random.Random) -> dict:
    fn, ln = rng.choice(FIRST), rng.choice(LAST)
    area = rng.choice([n for n in range(1, 900) if n != 666])
    ssn = f"{area:03d}-{rng.randint(1, 99):02d}-{rng.randint(1, 9999):04d}"
    card = luhn_complete(rng.choice(["411111", "555555", "378282"]), 16, rng)
    return {
        "EMAIL": f"{fn.lower()}.{ln.lower()}{rng.randint(1, 99)}@{rng.choice(DOMAINS)}",
        "PHONE": f"({rng.randint(201, 989)}) 555-{rng.randint(100, 9999):04d}",
        "SSN": ssn,
        "CARD": " ".join(card[i:i + 4] for i in range(0, 16, 4)),
        "EMPLOYEE_ID": f"EMP-{rng.randint(100000, 999999)}",
        "IP": f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
        "OPENAI_KEY": "sk-proj-" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(40)),
        "AWS_KEY": "AKIA" + "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(16)),
        "GH_TOKEN": "ghp_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36)),
        "JWT": "eyJ" + base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")[3:] + "." +
               base64.urlsafe_b64encode(json.dumps({"sub": str(rng.randint(1, 10**9))}).encode()).decode().rstrip("=") + "." +
               "".join(rng.choice(string.ascii_letters + string.digits + "-_") for _ in range(43)),
        "PASSWORD": "".join(rng.choice(string.ascii_letters) for _ in range(6)) + str(rng.randint(10, 99)) + rng.choice("!@#$%"),
        "DB_URI": f"postgres://svc_{ln.lower()}:{''.join(rng.choice(string.ascii_letters + string.digits) for _ in range(12))}@db{rng.randint(1, 9)}.internal:5432/prod",
        "PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----",
        "NAME": f"{fn} {ln}",
        "EMAIL_OBF": f"{fn.lower()} [at] {rng.choice(DOMAINS).replace('.', ' [dot] ')}",
        "SSN_SPACED": " ".join(ssn.replace("-", "")),
    }


TEMPLATES = [
    ("Please update the file for {NAME}, email {EMAIL}, before Friday.", ["EMAIL"]),
    ("Call the candidate back on {PHONE} about the offer.", ["PHONE"]),
    ("Employee {EMPLOYEE_ID} submitted a leave request; SSN on file is {SSN}.", ["EMPLOYEE_ID", "SSN"]),
    ("Customer paid with card {CARD} and wants a refund.", ["CARD"]),
    ("The build fails with OPENAI_API_KEY={OPENAI_KEY} in the env file.", ["SECRET"]),
    ("Here is the access key {AWS_KEY} for the backup bucket.", ["SECRET"]),
    ("git push failed, token {GH_TOKEN} might be expired.", ["SECRET"]),
    ("Decode this session token: {JWT}", ["SECRET"]),
    ("My password is {PASSWORD} and it keeps getting locked.", ["SECRET"]),
    ("Connection string: {DB_URI}", ["SECRET"]),
    ("I pasted the key below\n{PRIVATE_KEY}\nMIIEowIBAAKCAQEA", ["SECRET"]),
    ("Send the contract to {EMAIL_OBF} please.", ["EMAIL"]),
    ("For verification my social is {SSN_SPACED}.", ["SSN"]),
    ("Server {IP} is throwing 500s since the deploy.", ["IP"]),
    ("Reach {NAME} at {PHONE} or {EMAIL} regarding invoice follow-up.", ["PHONE", "EMAIL"]),
]
KEY_FOR = {"EMAIL": ["EMAIL", "EMAIL_OBF"], "PHONE": ["PHONE"], "SSN": ["SSN", "SSN_SPACED"], "CARD": ["CARD"],
           "EMPLOYEE_ID": ["EMPLOYEE_ID"], "IP": ["IP"],
           "SECRET": ["OPENAI_KEY", "AWS_KEY", "GH_TOKEN", "JWT", "PASSWORD", "DB_URI", "PRIVATE_KEY"]}

NEGATIVES = [
    "How do I reset another employee's VPN password?",
    "Order #{n7} shipped on 2026-09-01 with tracking 1Z{n8}.",
    "The ISBN of the handbook is 978-0-{n3}-{n5}-{d}.",
    "Upgrade the agent from version 4.{d}.{n2} to 5.0.1 before Monday.",
    "The password policy requires 12 characters and a symbol.",
    "Room capacity is {n2} people; the meeting starts at 14:30.",
    "Reference number {n13} was assigned to the ticket.",
    "Our API key rotation schedule is every 90 days.",
    "Quarterly revenue grew {n2}% compared to Q{q} last year.",
    "Please summarize the security awareness training in three bullets.",
    "What is the difference between a private key and a public key?",
    "The server rack in building {n2} uses 208 V power.",
]


def fill_negative(t: str, rng: random.Random) -> str:
    r = lambda k: "".join(rng.choice(string.digits) for _ in range(k))
    return t.format(n2=r(2), n3=r(3), n5=r(5), n7=r(7), n8=r(8), n13=r(13), d=rng.randint(0, 9), q=rng.randint(1, 4))


def main() -> None:
    rng = random.Random(SEED)
    rows = []
    for i in range(300):
        tpl, cats = TEMPLATES[i % len(TEMPLATES)]
        vals = gen_values(rng)
        text, spans, pos = "", [], 0
        # build text while recording spans of sensitive placeholders
        parts = tpl.replace("{", "\x00{").split("\x00")
        for part in parts:
            if part.startswith("{"):
                key, rest = part[1:].split("}", 1)
                v = vals[key]
                cat = next((c for c, ks in KEY_FOR.items() if key in ks), None)
                if cat and cat in cats:
                    spans.append({"start": len(text), "end": len(text) + len(v), "category": cat})
                text += v + rest
            else:
                text += part
        rows.append({"id": f"syn-pos-{i}", "text": text, "spans": spans, "kind": "positive", "source": "synthetic"})
    for i in range(120):
        rows.append({"id": f"syn-neg-{i}", "text": fill_negative(NEGATIVES[i % len(NEGATIVES)], rng), "spans": [],
                     "kind": "hard_negative", "source": "synthetic"})
    mmlu = ROOT / "datasets/raw/mmlu/mmlu_subset_test.jsonl"
    if mmlu.exists():
        qs = [json.loads(l) for l in mmlu.read_text().split("\n") if l.strip()]
        rng.shuffle(qs)
        for q in qs[:200]:
            rows.append({"id": f"pub-neg-{q['id']}", "text": q["question"], "spans": [], "kind": "public_negative",
                         "source": "MMLU (public, MIT)"})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} rows -> {OUT.relative_to(ROOT)} (seed {SEED}; label: Synthetic security evaluation data)")


if __name__ == "__main__":
    main()
