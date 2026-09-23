# Two-minute video script

**0:00–0:12 — The problem.** *(Overview page, empty state → first request)*
"Enterprises want AI, but every request is a trade-off between privacy, quality, cost and auditability — and today nobody
can prove which trade-off was made. NanoGate makes that decision explicit, on this HP ZGX Nano."

**0:12–0:30 — Drop-in.** *(Terminal: examples/openai_client.py)*
"Any OpenAI SDK app changes two lines — base URL and API key. This request just ran on the local Qwen model on the GB10;
the headers tell us the route, the reason and the receipt."

**0:30–0:50 — Verified reuse vs hard negative.** *(Overview presets 2 and 3, then Verified Cache pair tester)*
"A paraphrase is reused only after a second model verifies it means the same thing. 'Reset *another employee's* VPN password'
is similar — and rejected, with the ownership evidence right here."

**0:50–1:10 — Privacy.** *(HR preset → receipt Classification + Decision sections)*
"HR data with (synthetic) personal identifiers: classified Restricted, every remote route removed, executed locally. The egress
meter on the connector recorded zero bytes."

**1:10–1:30 — Risk, not confidence.** *(Router Lab)*
"Token confidence isn't correctness — the raw log-prob baseline is badly calibrated. Our router is trained on graded local
answers and calibrated on held-out data; the threshold comes from validation, and the weak spots are in the results table."

**1:30–1:45 — The hardware.** *(Infrastructure: device proof + offline verification)*
"Every hardware number has a source — NVML, /proc, the runtime. And here is a real offline run: outbound network blocked, full
pipeline, PASS."

**1:45–2:00 — Proof.** *(Receipt → Verify → Tamper test)*
"Every decision, answered or denied, is sealed into a hash chain. Change one field — integrity failure. That's NanoGate:
every AI request takes the cheapest safe route, with proof."
