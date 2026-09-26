# NanoGate administrator guide

For the IT, security and finance teams who run NanoGate day to day. Everything below is done from the dashboard
(`https://<nanogate-host>:8080`) unless it says otherwise.

## Roles and sign-in
| Key kind | Who | Can |
|---|---|---|
| **Admin** | IT / platform owners | Everything, including keys, policies, data erasure and backups |
| **Auditor** | Internal or external auditors, security reviewers | See every page and export; change nothing |
| **App** | An application using the OpenAI-compatible API | Call `/v1/chat/completions`, `/v1/embeddings`, `/v1/models` |
| **Person** | An employee | Use the chat app at `/chat` |

Admins and auditors sign in to the dashboard with their key (it is exchanged for a 12-hour session; the key is not
kept in the browser). Employees sign in to `/chat` with their person key (8-hour session).

## Onboarding a department or an app (Access page)
1. **Departments tab → + Department**: name it and choose the policy it follows (routes, privacy rules, budget,
   retention). It takes effect on the next request.
2. **API keys tab → New key**: choose the kind, the department and an optional expiry. The key is shown **once**;
   NanoGate stores only a keyed hash. App keys come with a ready-to-paste SDK snippet.
3. **Revoke** stops a key immediately (your own signed-in key cannot be revoked by yourself).
4. **Activity log**: every change, who made it, and whether it came from the dashboard, the Assistant (after your
   confirmation) or an automatic job.

## Company knowledge (Knowledge page)
Create a source (e.g. "IT runbooks"), choose which departments may use it and its data class, then upload PDFs
(with a text layer), Markdown, text, CSV, JSON, YAML or HTML (15 MB each). Text is indexed on the device.
Answers built from a source name the document, and the source's data class applies to the request: a
Confidential source keeps those answers on-device even for departments that may use remote models. **Revoke**
stops its use at once and invalidates cached answers built from it.

## Alerts (Alerts page)
Add Slack, Microsoft Teams or generic webhook channels (the URL is stored on the device; only its host is shown).
Rules: budget threshold (and 100%), model down, secret blocked, impersonation attempt, sensitive egress, receipt
chain failure (checked every 10 minutes), error spike. Each fires at most once per 15 minutes per subject.
Messages carry codes, departments and ids, never prompt text. Use **Send test alert** after adding a channel.

## NanoGate Assistant (Assistant page, or "Ask" in the top bar)
Ask in plain language: *"Why were requests denied today?"*, *"Which department spent the most this month?"*,
*"Is everything healthy?"*, *"How do I connect my app?"*. It answers from live NanoGate data using the local
models on the device (nothing is sent out) and shows which data it checked. It cannot see anyone's prompt text.
It can also **prepare** tasks (create or revoke a key, add a department, change a budget, toggle an alert rule,
take a backup, erase data); each appears as a card you must **Confirm**. Only the admin who asked can confirm, a
proposal expires after 15 minutes, and the change is audit-logged with source "assistant". Auditors can ask but
not confirm.

## Operations page
- **HTTPS**: `scripts/make_tls_cert.sh` (self-signed) or your CA's certificate; set `NANOGATE_TLS_CERT` and
  `NANOGATE_TLS_KEY` in `.env`, then `scripts/gateway.sh restart`.
- **Backups**: scheduled (default daily, keep 7) and on demand. An archive holds the database, the key pepper, the
  receipt signing key and policies, so it is owner-only on disk and download is admin-only. Copy archives off the
  device. Restore: `scripts/restore.sh var/backups/<file>.tar.gz` (the current state is moved aside first).
- **Retention**: runs hourly. Each department's policy sets how long records are kept (`retention.receipt_days`).
  Old request rows are deleted; old receipts keep their hash, HMAC and chain links but lose their body, so
  **chain verification still proves nothing was altered or removed**. Prompt text is never stored unless a policy
  explicitly enables it.
- **Right to erasure**: everything recorded for one key (a person or an app) or one department: request rows,
  receipt bodies and cached answers. Type `ERASE` to confirm; it is audit-logged.
- **Exports**: request metadata as CSV (for spreadsheets) and sealed receipts as JSON Lines (each line has the
  canonical body, previous hash, hash and HMAC: `hash = SHA-256(previous_hash + body)`), for independent checks.
- **SIEM**: RFC 5424 syslog (UDP or TCP) with one JSON message per event: receipt sealed, policy published,
  service state change, alert, admin change. Works with Splunk, Microsoft Sentinel, QRadar and any syslog collector.
- **Models**: which models each tier serves and how they are doing; change them with the runtime (`zrt`).

## Finance (FinOps page → By department)
Monthly measured cost per department: requests, on-device share, tokens, cost at configured rates, the same
workload at the reference hosted price, the difference, and budget used. Download as CSV for chargeback.

## Running it as a service
```bash
scripts/install_service.sh      # user-level systemd units; starts at boot, restarts on failure
scripts/upgrade.sh              # backup -> pull -> build -> restart -> readiness check; rolls back on failure
```
If the account may not keep services running without a login ("linger"), the installer adds a `@reboot`
crontab entry instead and says so; an administrator can enable linger with `sudo loginctl enable-linger <user>`.

## For application developers
```python
from openai import OpenAI
client = OpenAI(base_url="https://<nanogate-host>:8080/v1", api_key="<app key>")
client.chat.completions.create(model="nanogate-auto", messages=[{"role": "user", "content": "…"}])
client.embeddings.create(model="nanogate-embed", input=["…"])   # 384-dim, on-device
```
Every response carries `x-nanogate-route`, `x-nanogate-reason` and `x-nanogate-receipt-id` headers.
