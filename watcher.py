"""
Runs the TCC seat check again and again, forever, on your own server.

You choose how many seconds to wait between checks with the setting
CHECK_INTERVAL_SECONDS. Example: 120 means one check every 2 minutes.

Settings (all optional):
  CHECK_INTERVAL_SECONDS  Seconds between checks. Default 300.
                          The lowest allowed is 60. A smaller number is
                          raised to 60, so a typo cannot flood TCC.
  QUIET_HOURS             Central time hours with no checks. Default "2-5"
                          (2:00am up to 5:00am). Use "off" to check all night.
  WATCHLIST_URL           Where to read your class list from. Default is the
                          watchlist.json in your GitHub repo. Use "off" to
                          use only the file on this server.

  STATUS_PORT             Port for your status web page. Default 8080.
                          Open  http://YOUR-SERVER-IP:8080  in a browser.
                          Use "off" to turn the page off.

Before each check, the watcher reads watchlist.json from GitHub. To add or
remove a class, edit watchlist.json on GitHub. The server picks it up on a
later check (GitHub can take about 5 minutes to show a new version).
If GitHub cannot be reached, or the new list looks wrong, the watcher keeps
the last good list and says so in the log.

If TCC has errors, the wait grows (2x, 4x, 8x ...) up to 15 minutes,
then goes back to normal after the first good check.

This file only repeats check_once.py. The seat check and the alerts
are the same code that GitHub runs today.
"""

import json
import os
import random
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("America/Chicago")
HERE = Path(__file__).parent
WATCHLIST_FILE = HERE / "watchlist.json"
STATE_FILE = HERE / "state.json"
PAGE_FILE = HERE / "index.html"
DEFAULT_PORT = 8080

# What the status page shows about the last check.
STATUS = {"checked_at": None, "ok": None, "interval": None,
          "quiet_hours": None, "quiet_now": False}
DEFAULT_WATCHLIST_URL = (
    "https://raw.githubusercontent.com/chrismasi13/tcc-seat-watcher/"
    "main/watchlist.json"
)

DEFAULT_INTERVAL = 300   # seconds
MIN_INTERVAL = 60        # never check faster than this
MAX_BACKOFF = 900        # longest wait after errors, in seconds
DEFAULT_QUIET = "2-5"


def read_interval(raw):
    """Turn the setting text into a safe number of seconds."""
    raw = (raw or "").strip()
    if not raw:
        return DEFAULT_INTERVAL
    try:
        seconds = int(raw)
    except ValueError:
        print("CHECK_INTERVAL_SECONDS must be a whole number, got %r. "
              "Using %d." % (raw, DEFAULT_INTERVAL))
        return DEFAULT_INTERVAL
    if seconds < MIN_INTERVAL:
        print("CHECK_INTERVAL_SECONDS=%d is too fast. Using %d."
              % (seconds, MIN_INTERVAL))
        return MIN_INTERVAL
    return seconds


def read_quiet(raw):
    """'2-5' -> (2, 5). 'off' -> None."""
    raw = (raw if raw is not None else DEFAULT_QUIET).strip().lower()
    if raw in ("", "off", "none", "no"):
        return None
    try:
        start, end = raw.split("-")
        start, end = int(start), int(end)
        if not (0 <= start <= 23 and 0 <= end <= 24):
            raise ValueError
        return (start, end)
    except ValueError:
        print("QUIET_HOURS must look like 2-5 or off, got %r. Using %s."
              % (raw, DEFAULT_QUIET))
        return read_quiet(DEFAULT_QUIET)


def in_quiet_hours(quiet, now):
    if quiet is None:
        return False
    start, end = quiet
    if start <= end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end   # window crosses midnight


def wait_after(interval, failures):
    """Seconds to wait before the next check."""
    if failures == 0:
        return interval
    longest = max(MAX_BACKOFF, interval)
    return min(interval * (2 ** failures), longest)


def build_status(watchlist_file=WATCHLIST_FILE, state_file=STATE_FILE):
    """Everything the web page needs, as one dictionary."""
    try:
        raw_list = json.loads(Path(watchlist_file).read_text())
    except (OSError, ValueError):
        raw_list = []
    names = []
    for item in raw_list:
        name = str(item).strip().upper()
        if name and not name.startswith("#"):
            names.append(name)
    try:
        state = json.loads(Path(state_file).read_text())
    except (OSError, ValueError):
        state = {}
    sections = {}
    for name in names:
        s = state.get(name) or {}
        sections[name] = {
            "found": s.get("found", False) if s else None,
            "open": s.get("open"),
            "capacity": s.get("capacity"),
            "term": s.get("term"),
            "status": s.get("status"),
        }
    out = dict(STATUS)
    out["watchlist"] = names
    out["sections"] = sections
    return out


class StatusHandler(BaseHTTPRequestHandler):
    """Serves exactly two things: the page and its data. Nothing else."""

    def _send(self, code, ctype, body):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8",
                           PAGE_FILE.read_bytes())
            elif path == "/status.json":
                self._send(200, "application/json",
                           json.dumps(build_status()))
            else:
                self._send(404, "text/plain; charset=utf-8", "Not found")
        except Exception as exc:
            self._send(500, "text/plain; charset=utf-8",
                       "Error: %s" % exc)

    def log_message(self, *args):
        pass   # keep the log clean


def read_port(raw):
    raw = (raw or "").strip().lower()
    if not raw:
        return DEFAULT_PORT
    if raw in ("off", "none", "no"):
        return None
    try:
        port = int(raw)
        if not 1 <= port <= 65535:
            raise ValueError
        return port
    except ValueError:
        print("STATUS_PORT must be a number like 8080 or off, got %r. "
              "Using %d." % (raw, DEFAULT_PORT))
        return DEFAULT_PORT


def start_status_server(port, host="0.0.0.0"):
    """Start the web page in the background. Returns the server or None."""
    if port is None:
        return None
    try:
        server = ThreadingHTTPServer((host, port), StatusHandler)
    except OSError as exc:
        print("Could not start the status page on port %s (%s). "
              "Checking continues without it." % (port, exc))
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("Status page is on port %d." % server.server_address[1])
    return server


def read_watchlist_url(raw):
    raw = (raw or "").strip()
    if not raw:
        return DEFAULT_WATCHLIST_URL
    if raw.lower() in ("off", "none", "no"):
        return None
    return raw


def valid_watchlist(data):
    """Return an error text if the list looks wrong, or None if it is fine."""
    if not isinstance(data, list):
        return "it is not a list"
    names = [x for x in data if not str(x).strip().startswith("#")]
    if not names:
        return "it is empty"
    for item in names:
        if not isinstance(item, str):
            return "%r is not text" % (item,)
        if len(item.strip().split("-")) < 3:
            return "%r should look like BIOL-2402-86005" % item
    return None


def refresh_watchlist(url, path=WATCHLIST_FILE):
    """
    Copy the class list from GitHub to this server.
    Returns True if the list changed. Never raises: on any problem it
    keeps the old list and prints why.
    """
    if not url:
        return False
    try:
        r = requests.get(url, timeout=15,
                         headers={"Cache-Control": "no-cache"})
        if r.status_code != 200:
            raise RuntimeError("GitHub answered %s" % r.status_code)
        data = json.loads(r.text)
        problem = valid_watchlist(data)
        if problem:
            raise ValueError("new list rejected, " + problem)
        try:
            old = json.loads(path.read_text())
        except (OSError, ValueError):
            old = None
        if old == data:
            return False
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(path)
        print("Watch list updated from GitHub: " + ", ".join(
            str(x) for x in data))
        return True
    except Exception as exc:
        print("Could not update the watch list (%s). "
              "Keeping the last good list." % exc)
        return False


def run_one_check():
    """Run one check. True if it worked, False if it had a problem."""
    import check_once
    refresh_watchlist(read_watchlist_url(os.environ.get("WATCHLIST_URL")))
    try:
        return check_once.main() == 0
    except SystemExit:
        return False
    except Exception:
        traceback.print_exc()
        return False


def loop(interval, quiet, run=run_one_check, sleep=time.sleep,
         now=lambda: datetime.now(TZ), max_rounds=None, jitter=True):
    failures = 0
    rounds = 0
    STATUS["interval"] = interval
    STATUS["quiet_hours"] = None if quiet is None else "%d-%d" % quiet
    while max_rounds is None or rounds < max_rounds:
        rounds += 1
        started = time.monotonic()
        stamp = now().strftime("%Y-%m-%d %H:%M:%S %Z")

        if in_quiet_hours(quiet, now()):
            print("[%s] Quiet hours. No check." % stamp)
            STATUS["quiet_now"] = True
            wait = interval
        else:
            print("[%s] Checking..." % stamp)
            STATUS["quiet_now"] = False
            ok = run()
            STATUS["checked_at"] = now().isoformat()
            STATUS["ok"] = bool(ok)
            failures = 0 if ok else failures + 1
            wait = wait_after(interval, failures)
            if failures:
                print("Problem #%d. Waiting %d seconds before trying again."
                      % (failures, wait))
        sys.stdout.flush()

        spent = time.monotonic() - started
        pause = max(1, wait - spent)
        if jitter:
            pause += random.uniform(0, 5)
        sleep(pause)


def main():
    interval = read_interval(os.environ.get("CHECK_INTERVAL_SECONDS"))
    quiet = read_quiet(os.environ.get("QUIET_HOURS"))
    print("Watcher started. One check every %d seconds. Quiet hours: %s."
          % (interval, "none" if quiet is None else "%d-%d" % quiet))
    print("Watch list source: %s" % (
        read_watchlist_url(os.environ.get("WATCHLIST_URL")) or "this server only"))
    start_status_server(read_port(os.environ.get("STATUS_PORT")))
    loop(interval, quiet)


if __name__ == "__main__":
    main()
