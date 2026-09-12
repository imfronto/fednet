# Rack status display

A small always-on screen mounted in the rack, showing the "Server Overview"
Grafana dashboard 24/7. Runs on its own Raspberry Pi, separate from the
homelab host — nothing in `docker-compose.yml` changes for this.

## Shopping list (~$60-90)

- **Raspberry Pi 4 (2GB is enough)** — a Pi Zero 2 W is cheaper but noticeably
  laggy running Chromium; the Pi 4 keeps the kiosk smooth.
- **7" HDMI panel, ~1024x600** — look for ones sold in a metal or acrylic
  frame (marketed for kiosks/car dashboards/CNC), not a bare bonded-cable
  board — the frame is what lets you actually mount it.
- **microSD card, 16-32GB.**
- **1U vented rack shelf** (if the panel doesn't have its own VESA/rack
  mounting) to sit the Pi + screen assembly on.
- USB-C power for the Pi (from a rack PDU outlet); panels are usually either
  USB-powered separately or powered off the Pi's GPIO 5V — check the specific
  panel's manual.

## Setup

1. Flash **Raspberry Pi OS Lite (64-bit)** with the Raspberry Pi Imager.
   In the imager's advanced options (gear icon), set a hostname, enable SSH,
   and set a username/password — this avoids needing a keyboard/monitor for
   first boot.
2. Boot it, `ssh` in, `git clone` (or `scp`) this repo just to grab the
   script, or copy `scripts/rack_display_kiosk.sh` over directly.
3. Decide how the kiosk will authenticate to Grafana — read the TODO block
   at the top of `scripts/rack_display_kiosk.sh`. The dashboard is
   login-protected by default, so this isn't optional.
4. Run it: `KIOSK_URL="<your chosen url>" ./rack_display_kiosk.sh`
5. `sudo reboot` — it should come up straight into the dashboard, fullscreen,
   no cursor, no login prompts.

## Notes

- The script relaunches Chromium automatically if it's ever killed, and
  force-restarts it once nightly (04:00) to avoid long-uptime memory bloat.
- A "Rack Display" playlist already exists in Grafana (uid `cfxzf50kaf7y8e`),
  rotating Server Overview -> Honeypot every 1 minute — that's what
  `KIOSK_URL` points at by default. Change the rotation speed or add more
  dashboards via Dashboards > Playlists > Rack Display in the Grafana UI.
- If the panel's image is upside-down or mirrored, add
  `xrandr --output <output-name> --rotate <normal|inverted|left|right>` to
  `~/.xinitrc` before the Chromium loop (find `<output-name>` via
  `xrandr --query` over SSH with X running).
