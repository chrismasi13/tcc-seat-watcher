"""
Sends the alert, and remembers what it already sent.

Two ways to get alerted. You can use either one, or both:
  - Email  (SMTP_USER, SMTP_PASSWORD, ALERT_TO)
  - ntfy   (NTFY_TOPIC)  -> a push notification on your phone

Rules:
  - Alert only when a section has 1 or more open seats.
  - Send nothing when every section is full.
  - Do not alert twice. Only alert again if the section went back to
    0 seats and then opened again.
"""

import json
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

STATE_FILE = Path(__file__).parent / "state.json"
TZ = ZoneInfo("America/Chicago")
SEARCH_BASE = "https://selfservice.tccd.edu/Student/Courses/Search"


def _now():
    return datetime.now(TZ)


def section_link(section_name):
    """A link straight to that one section on the TCC site."""
    return SEARCH_BASE + "?keyword=" + quote(section_name)


# ---------------------------------------------------------------- state

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------- email

def email_configured():
    return all(
        os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASSWORD", "ALERT_TO")
    )


def _smtp_settings():
    missing = [
        k for k in ("SMTP_USER", "SMTP_PASSWORD", "ALERT_TO")
        if not os.environ.get(k)
    ]
    if missing:
        raise RuntimeError("Missing email settings: " + ", ".join(missing))
    return {
        # Use "or", not a default. GitHub sets these to an EMPTY string
        # when the secret does not exist, and an empty string would
        # otherwise beat the default and crash int().
        "host": os.environ.get("SMTP_HOST") or "smtp.gmail.com",
        "port": int(os.environ.get("SMTP_PORT") or 465),
        "user": os.environ["SMTP_USER"],
        "password": os.environ["SMTP_PASSWORD"],
        "to": os.environ["ALERT_TO"],
    }


def send_email(subject, body):
    cfg = _smtp_settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["user"]
    # A comma separated ALERT_TO reaches several people.
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


# ----------------------------------------------------------------- ntfy

def ntfy_configured():
    return bool((os.environ.get("NTFY_TOPIC") or "").strip())


def _ascii(text):
    """HTTP headers must be plain ASCII, so strip anything unusual."""
    return text.encode("ascii", "replace").decode("ascii")


def send_ntfy(subject, body, click_url=None):
    topic = (os.environ.get("NTFY_TOPIC") or "").strip()
    if not topic:
        raise RuntimeError("NTFY_TOPIC is not set")

    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    headers = {
        "Title": _ascii(subject),
        # urgent makes the phone alert loudly, which is the point here.
        "Priority": (os.environ.get("NTFY_PRIORITY") or "urgent").strip(),
        "Tags": "rotating_light",
    }
    if click_url:
        # Tapping the notification opens the class page.
        headers["Click"] = click_url

    token = (os.environ.get("NTFY_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token

    r = requests.post(
        server + "/" + topic,
        data=body.encode("utf-8"),
        headers=headers,
        timeout=20,
    )
    r.raise_for_status()


# ------------------------------------------------------------- delivery

def deliver(subject, body, click_url=None):
    """
    Try every alert method that is set up.
    Returns (did_any_work, {method: result}).
    """
    if not email_configured() and not ntfy_configured():
        raise RuntimeError(
            "No alert method is set up. Add SMTP_USER, SMTP_PASSWORD and "
            "ALERT_TO for email, or NTFY_TOPIC for phone notifications."
        )

    results = {}
    if email_configured():
        try:
            send_email(subject, body)
            results["email"] = "sent"
        except Exception as exc:
            results["email"] = "FAILED: " + str(exc)

    if ntfy_configured():
        try:
            send_ntfy(subject, body, click_url)
            results["ntfy"] = "sent"
        except Exception as exc:
            results["ntfy"] = "FAILED: " + str(exc)

    worked = any(v == "sent" for v in results.values())
    return worked, results


def _build_body(opened):
    when = _now().strftime("%A, %B %d, %Y at %I:%M %p %Z")
    lines = ["A seat opened up. Go register now.", ""]
    for info in opened:
        name = info["section"]
        lines.append(name + "  (" + (info.get("term") or "") + ")")
        lines.append(
            "    Open seats: {} of {}".format(
                info.get("open"), info.get("capacity")
            )
        )
        if info.get("instructor"):
            lines.append("    Instructor: " + info["instructor"])
        lines.append("    " + section_link(name))
        lines.append("")
    lines.append('On that page, click "View Available Sections" to see it.')
    lines.append("")
    lines.append("Checked at " + when)
    lines.append("")
    lines.append("Seats can fill in minutes, so do not wait.")
    return "\n".join(lines)


# -------------------------------------------------------------- main job

def process_results(results, send=True):
    """
    results: dict of section name -> info dict (or None)
    Returns a dict describing what happened.
    """
    state = load_state()
    opened = []

    for name, info in results.items():
        prev_alerted = state.get(name, {}).get("alerted", False)
        open_now = bool(
            info
            and (
                info.get("unlimited")
                or (isinstance(info.get("open"), int) and info["open"] >= 1)
            )
        )

        if open_now and not prev_alerted:
            opened.append(info)

        # Deliberately NO timestamp. In the cloud this file is saved back
        # to GitHub after each run, and a timestamp would change every
        # time, making thousands of pointless commits.
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
    detail = {}
    if opened and send:
        names = ", ".join(i["section"] for i in opened)
        click = section_link(opened[0]["section"])
        try:
            sent, detail = deliver(
                "Seat open: " + names, _build_body(opened), click
            )
        except Exception as exc:
            error = str(exc)

        if not sent:
            # Nothing got through. Do not mark as alerted, so the next
            # run tries again instead of losing the alert.
            if error is None:
                error = "; ".join(
                    "%s %s" % (k, v) for k, v in detail.items()
                )
            for info in opened:
                state[info["section"]]["alerted"] = False

    save_state(state)
    return {
        "opened": [i["section"] for i in opened],
        "email_sent": sent,
        "email_error": error,
        "delivery": detail,
        "checked_at": _now().isoformat(),
    }
