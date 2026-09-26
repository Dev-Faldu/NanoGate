#!/usr/bin/env bash
# Start NanoGate automatically at boot and restart it if it crashes (no root needed).
# Installs user-level systemd units: nanogate-models (ZRT / vLLM tiers) and nanogate-gateway (API + dashboard).
# "Linger" lets user services run without anyone logged in; if it cannot be enabled, a crontab @reboot entry is used.
# Usage: scripts/install_service.sh [--uninstall]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNITS="$HOME/.config/systemd/user"
if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now nanogate-gateway.service nanogate-models.service 2>/dev/null || true
  rm -f "$UNITS/nanogate-gateway.service" "$UNITS/nanogate-models.service"; systemctl --user daemon-reload || true
  crontab -l 2>/dev/null | grep -v "nanogate-boot" | crontab - || true
  echo "removed"; exit 0
fi
mkdir -p "$UNITS"
cat > "$UNITS/nanogate-models.service" <<UNIT
[Unit]
Description=NanoGate model servers (HP Z Runtime / vLLM)
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart="$ROOT/scripts/runtime.sh" start
ExecStop="$ROOT/scripts/runtime.sh" stop
TimeoutStartSec=1200

[Install]
WantedBy=default.target
UNIT
cat > "$UNITS/nanogate-gateway.service" <<UNIT
[Unit]
Description=NanoGate gateway (API + dashboard)
After=nanogate-models.service
Wants=nanogate-models.service

[Service]
ExecStart="$ROOT/scripts/gateway.sh" run
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable nanogate-models.service nanogate-gateway.service
if loginctl enable-linger "$USER" 2>/dev/null || [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" == "yes" ]]; then
  echo "linger enabled: services start at boot without a login"
else
  line="@reboot sleep 60 && systemctl --user start nanogate-models.service nanogate-gateway.service # nanogate-boot"
  (crontab -l 2>/dev/null | grep -v "nanogate-boot"; echo "$line") | crontab -
  echo "linger not permitted for this user: added a crontab @reboot entry instead"
  echo "(an administrator can run: sudo loginctl enable-linger $USER)"
fi
echo "installed. Take over from the manual processes with:"
echo "  scripts/gateway.sh stop && systemctl --user start nanogate-models nanogate-gateway"
echo "status: systemctl --user status nanogate-gateway   logs: journalctl --user -u nanogate-gateway -f"
