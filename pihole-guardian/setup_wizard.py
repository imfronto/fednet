#!/usr/bin/env python3
"""Pi Guard interactive setup wizard.

Deliberately zero third-party dependencies — this has to run with a bare
`python3` before anything is pip-installed or any Docker image is built,
so anyone can clone this folder and get a working .env in two minutes
without first fighting a Python environment. Everything here uses only
the standard library (urllib for the live Pi-hole connection test, secrets
for token generation, getpass for hidden password entry).

Run it with:  python3 setup_wizard.py
"""
from __future__ import annotations

import getpass
import json
import re
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

BANNER = r"""
  ___ _   ___                     _
 / _ (_) / _ \ _   _  __ _ _ __ __| |
| |_) | | | | | | | |/ _` | '__/ _` |
|  __/| | |_| | |_| | (_| | | | (_| |
|_|   |_|\__\_\\__,_|\__,_|_|  \__,_|

Safety-first automated Pi-hole blocklist maintenance.
This wizard writes a .env file — it does not touch Pi-hole or start
anything itself. Run it as many times as you like.
"""

_WORDS = [
    "cedar", "otter", "comet", "maple", "harbor", "willow", "ember", "garnet",
    "falcon", "meadow", "quartz", "cobalt", "amber", "cider", "pebble", "spruce",
]

MODES = {
    "1": ("AUDIT", "Observe and classify only. Never touches Pi-hole. Safest — start here."),
    "2": ("DRY_RUN", "Computes full decisions and logs exactly what would happen. Still never touches Pi-hole."),
    "3": ("RECOMMEND", "Generates recommendations that require your explicit approval before anything changes."),
    "4": ("ENFORCE", "Automatically applies only high-confidence decisions. Use only after you trust the classifications on your own network."),
}


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("  (this can't be empty)")


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    value = input(f"{prompt} {suffix}: ").strip().lower()
    if not value:
        return default
    return value.startswith("y")


def _generate_short_password() -> str:
    w1, w2 = secrets.choice(_WORDS), secrets.choice(_WORDS)
    num = secrets.randbelow(90) + 10
    return f"{w1}-{w2}{num}"


def _test_pihole_connection(url: str, password: str) -> tuple[bool, str]:
    payload = json.dumps({"password": password}).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/api/auth", data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read())
    except urllib.error.URLError as exc:
        return False, f"Could not reach {url}: {exc}"
    except (json.JSONDecodeError, TimeoutError) as exc:
        return False, f"Pi-hole responded unexpectedly: {exc}"

    session = body.get("session", {})
    if session.get("valid"):
        return True, "Authenticated successfully."
    return False, session.get("message") or "Pi-hole rejected the password."


def _step_pihole() -> tuple[str, str]:
    print("\n--- Step 1: Pi-hole connection ---")
    print("Examples: http://192.168.1.10  http://pihole.local  http://pihole:80")
    while True:
        url = _ask("Pi-hole URL (scheme + host, no trailing /admin)")
        if not re.match(r"^https?://", url):
            print("  Include the scheme, e.g. http://192.168.1.10")
            continue
        password = getpass.getpass("Pi-hole admin password (hidden): ")
        print("  Testing connection...", end=" ", flush=True)
        ok, message = _test_pihole_connection(url, password)
        print("OK" if ok else "FAILED")
        if ok:
            print(f"  {message}")
            return url, password
        print(f"  {message}")
        if not _ask_yes_no("  Try again?", default=True):
            print("  Continuing without a verified connection — Pi Guard will retry once it starts,")
            print("  but it will stay in a degraded state until this is reachable.")
            return url, password


def _step_dashboard() -> tuple[str, str, str]:
    print("\n--- Step 2: Dashboard & API access ---")
    username = _ask("Dashboard username", default="admin")
    if _ask_yes_no("Auto-generate a short dashboard password?", default=True):
        password = _generate_short_password()
        print(f"  Generated password: {password}")
        print("  Write this down now — it is only shown here.")
    else:
        while True:
            password = getpass.getpass("Dashboard password (hidden): ")
            confirm = getpass.getpass("Confirm: ")
            if password and password == confirm:
                break
            print("  Passwords didn't match (or were empty) — try again.")
    api_token = secrets.token_hex(32)
    print("  Generated a random API bearer token for programmatic access.")
    return username, password, api_token


def _step_mode() -> str:
    print("\n--- Step 3: Operating mode ---")
    for key, (name, description) in MODES.items():
        print(f"  {key}) {name} — {description}")
    while True:
        choice = _ask("Choose a mode by number", default="1")
        if choice in MODES:
            return MODES[choice][0]
        print("  Enter 1, 2, 3, or 4.")


def _step_extras() -> dict[str, str]:
    print("\n--- Step 4: Optional extras (press Enter to skip any of these) ---")
    vt_key = input("VirusTotal API key (optional reputation source): ").strip()
    client_tracking = _ask_yes_no(
        "Store a salted per-domain client count? (off by default; see docs/ARCHITECTURE.md)", default=False
    )
    client_salt = secrets.token_hex(16) if client_tracking else ""
    return {
        "GUARDIAN_VIRUSTOTAL_API_KEY": vt_key,
        "GUARDIAN_CLIENT_TRACKING_ENABLED": "true" if client_tracking else "false",
        "GUARDIAN_CLIENT_HASH_SALT": client_salt,
    }


def _write_env(path: Path, values: dict[str, str]) -> None:
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(path.read_text())
        print(f"\nExisting {path} backed up to {backup}")
    lines = ["# Generated by Pi Guard's setup_wizard.py", ""]
    for key, value in values.items():
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    print(BANNER)
    pihole_url, pihole_password = _step_pihole()
    dashboard_user, dashboard_password, api_token = _step_dashboard()
    mode = _step_mode()
    extras = _step_extras()

    values = {
        "GUARDIAN_PIHOLE_URL": pihole_url,
        "GUARDIAN_PIHOLE_PASSWORD": pihole_password,
        "GUARDIAN_MODE": mode,
        "GUARDIAN_DASHBOARD_USERNAME": dashboard_user,
        "GUARDIAN_DASHBOARD_PASSWORD": dashboard_password,
        "GUARDIAN_API_TOKEN": api_token,
        **extras,
    }

    default_target = Path(__file__).parent / ".env"
    target = Path(_ask("\nWhere should the .env file be written?", default=str(default_target)))
    _write_env(target, values)

    print(f"\nWrote {target}")
    print("\nNext steps:")
    print(f"  cd {target.parent}")
    print("  docker compose build")
    print("  docker compose up -d")
    print(f"\nDashboard: http://localhost:8930  (login: {dashboard_user} / the password shown above)")
    print(f"Mode: {mode} — see README.md's \"Operating modes\" table before raising it.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled — no file was written (except any earlier backup).")
        sys.exit(1)
