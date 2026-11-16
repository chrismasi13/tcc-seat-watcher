"""
Sends the alert email, and remembers what it already sent.

Rules:
  - Email only when a section has 1 or more open seats.
  - Send nothing when every section is full.
  - Do not send the same alert twice. Only send again if the section
    went back to 0 seats and then opened again.
"""

import json
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

STATE_FILE = Path(__file__).parent / "state.json"
TZ = ZoneInfo("America/Chicago")
SEARCH_LINK = "https://selfservice.tccd.edu/Student/Courses"


def _now():
    return datetime.now(TZ)


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _smtp_settings():
    """Read email settings from environment variables."""
    missing = [
        k
        for k in ("SMTP_USER", "SMTP_PASSWORD", "ALERT_TO")
        if not os.environ.get(k)
    ]
    if missing:
        raise RuntimeError(
            "Missing email settings: " + ", ".join(missing)
        )
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "465")),
        "user": os.environ["SMTP_USER"],
        "password": os.environ["SMTP_PASSWORD"],
        "to": os.environ["ALERT_TO"],
    }


def send_email(subject, body):
    cfg = _smtp_settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["user"]
    msg["To"] = cfg["to"]
    msg.set_content(body)

    context = ssl.create_default_context()
    if cfg["port"] == 587:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as server:
            server.starttls(context=context)
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
    else:
        with smtplib.SMTP_SSL(
            cfg["host"], cfg["port"], context=context, timeout=30
        ) as server:
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg)


def _build_body(opened):
    when = _now().strftime("%A, %B %d, %Y at %I:%M %p %Z")
    lines = ["A seat opened up. Go register now.", ""]
    for info in opened:
        lines.append(info["section"] + "  (" + (info.get("term") or "") + ")")
        lines.append(
            "    Open seats: {}  of {}".format(
                info.get("open"), info.get("capacity")
            )
        )
        if info.get("instructor"):
            lines.append("    Instructor: " + info["instructor"])
        lines.append("")
    lines.append("Checked at " + when)
    lines.append("")
    lines.append("Register here: " + SEARCH_LINK)
    lines.append("")
    lines.append("Seats can fill in minutes, so do not wait.")
    return "\n".join(lines)


def process_results(results, send=True):
    """
    results: dict of section name -> info dict (or None)

    Returns a dict describing what happened, so the web page can show it.
    """
    state = load_state()
    opened = []

    for name, info in results.items():
        prev_alerted = state.get(name, {}).get("alerted", False)
        open_now = bool(
            info
            and (
                info.get("unlimited")
                or (
                    isinstance(info.get("open"), int)
                    and info["open"] >= 1
                )
            )
        )

        if open_now and not prev_alerted:
            opened.append(info)

        # Deliberately NO timestamp here. In the cloud this file is saved
        # back to GitHub after each run, and a timestamp would change every
        # single time, creating thousands of pointless commits. Only real
        # changes in seats should cause a save.
        state[name] = {
            "alerted": open_now,
            "open": (info or {}).get("open"),
            "capacity": (info or {}).get("capacity"),
            "term": (info or {}).get("term"),
            "status": (info or {}).get("status"),
            "found": info is not None,
        }

    sent = False
    error = None
    if opened and send:
        names = ", ".join(i["section"] for i in opened)
        try:
            send_email("Seat open: " + names, _build_body(opened))
            sent = True
        except Exception as exc:  # keep the loop alive
            error = str(exc)
            # Do not mark as alerted, so the next run tries again.
            for info in opened:
                state[info["section"]]["alerted"] = False

    save_state(state)
    return {
        "opened": [i["section"] for i in opened],
        "email_sent": sent,
        "email_error": error,
        "checked_at": _now().isoformat(),
    }
