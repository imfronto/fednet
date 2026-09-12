#!/usr/bin/env bash
set -euo pipefail

# Run this ON THE PI ITSELF (Raspberry Pi OS Lite, 64-bit), after first boot
# over SSH — not on the homelab host. Turns the Pi into an unattended kiosk
# that boots straight into a fullscreen browser showing a Grafana dashboard,
# and keeps itself alive (auto-restarts the browser if it crashes or is
# killed by the nightly cron restart below).

# ---------------------------------------------------------------------------
# TODO(you): decide how the kiosk authenticates to Grafana, then set KIOSK_URL.
#
# Grafana isn't anonymous by default here (services/monitoring.yml only sets
# GF_SECURITY_ADMIN_PASSWORD) — pointing a bare dashboard URL at it will show
# a login screen instead of your stats. Pick ONE approach:
#
#   (a) Public dashboard (recommended, most scoped): in Grafana, open
#       "Server Overview" > Share > "Public dashboard" > Enable. This mints
#       a read-only, unauthenticated link for THIS dashboard only — nothing
#       else in Grafana becomes reachable without login. Paste that link in.
#   (b) Anonymous Viewer, org-wide: add to services/monitoring.yml's grafana
#       `environment:` block:
#         GF_AUTH_ANONYMOUS_ENABLED=true
#         GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
#       Simpler, but anyone on your LAN can view (not edit) every dashboard
#       without logging in. Fine for a private LAN — but that's a real
#       security trade-off, not a default worth flipping silently.
#   (c) Logged-in session: leave the normal dashboard URL below and log in
#       by hand once on the Pi's own screen (keyboard/mouse plugged in
#       temporarily) with a dedicated Viewer-role account. The Chromium
#       profile this script creates is persistent (not incognito), so the
#       session cookie survives reboots — but Grafana still expires it
#       eventually (30-day default), so you'll periodically need to log
#       back in on-screen.
#
# The URL below points at the "Rack Display" playlist (already created in
# Grafana, uid cfxzf50kaf7y8e), which rotates Server Overview -> Honeypot
# every 1m. Swap in a public-dashboard link instead if you go with (a) and
# want to skip auth entirely; edit the playlist's interval/items via
# Dashboards > Playlists in the Grafana UI, or the /api/playlists API.
KIOSK_URL="${KIOSK_URL:-http://lab.lan:3000/playlists/play/cfxzf50kaf7y8e?kiosk}"
# ---------------------------------------------------------------------------

KIOSK_USER="$(whoami)"
HOME_DIR="$HOME"

echo "Installing X11 + Chromium kiosk stack..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  xserver-xorg x11-xserver-utils xinit openbox unclutter chromium-browser

echo "Enabling console autologin for $KIOSK_USER..."
sudo raspi-config nonint do_boot_behaviour B2

# Start X automatically on the console autologin shell, only on the real
# console (tty1) and only if X isn't already running.
if ! grep -q "exec startx" "$HOME_DIR/.bash_profile" 2>/dev/null; then
  cat >>"$HOME_DIR/.bash_profile" <<'EOF'
if [ -z "${DISPLAY:-}" ] && [ "$(tty)" = "/dev/tty1" ]; then
  exec startx -- -nocursor
fi
EOF
fi

cat >"$HOME_DIR/.xinitrc" <<EOF
#!/usr/bin/env bash
openbox-session &
xset s off
xset s noblank
xset -dpms
unclutter -idle 0.5 -root &

while true; do
  chromium-browser \\
    --kiosk \\
    --noerrdialogs \\
    --disable-infobars \\
    --no-first-run \\
    --disable-translate \\
    --overscroll-history-navigation=0 \\
    --autoplay-policy=no-user-gesture-required \\
    --user-data-dir="$HOME_DIR/.kiosk-profile" \\
    "$KIOSK_URL"
  sleep 5
done
EOF
chmod +x "$HOME_DIR/.xinitrc"

# Force a fresh Chromium every night so a months-long uptime never runs into
# memory bloat or an unattended crash loop; the `while true` above relaunches
# it within 5s.
CRON_LINE="0 4 * * * pkill chromium-browser || true"
(crontab -l 2>/dev/null | grep -vF "chromium-browser"; echo "$CRON_LINE") | crontab -

echo "Done. Reboot to start the kiosk: sudo reboot"
echo "Kiosk URL baked into ~/.xinitrc: $KIOSK_URL"
