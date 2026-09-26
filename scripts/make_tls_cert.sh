#!/usr/bin/env bash
# Create a TLS certificate for the gateway (self-signed, 825 days) covering this host's names and addresses.
# For production, use your company CA instead: put its cert/key paths in .env (NANOGATE_TLS_CERT / NANOGATE_TLS_KEY).
# Usage: scripts/make_tls_cert.sh [extra-dns-name ...]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/var/tls"
mkdir -p "$OUT" && chmod 700 "$OUT"
host="$(hostname)"
san="DNS:localhost,DNS:$host,IP:127.0.0.1"
for ip in $(hostname -I 2>/dev/null); do [[ "$ip" == *:* ]] || san="$san,IP:$ip"; done
command -v tailscale >/dev/null && for ip in $(tailscale ip -4 2>/dev/null); do san="$san,IP:$ip"; done
for n in "$@"; do san="$san,DNS:$n"; done
umask 077
openssl req -x509 -newkey rsa:3072 -sha256 -days 825 -nodes -keyout "$OUT/nanogate.key" -out "$OUT/nanogate.crt" \
  -subj "/CN=$host/O=NanoGate" -addext "subjectAltName=$san" -addext "extendedKeyUsage=serverAuth" 2>/dev/null
chmod 600 "$OUT/nanogate.key"; chmod 644 "$OUT/nanogate.crt"
echo "certificate: $OUT/nanogate.crt  ($san)"
echo "private key: $OUT/nanogate.key  (owner-only)"
cat <<MSG

Add to .env, then run scripts/gateway.sh restart:
  NANOGATE_TLS_CERT=var/tls/nanogate.crt
  NANOGATE_TLS_KEY=var/tls/nanogate.key
Clients must trust this certificate (or your CA's): e.g. OpenAI(..., http_client=httpx.Client(verify="nanogate.crt")).
MSG
