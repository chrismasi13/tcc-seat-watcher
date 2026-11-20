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
import time
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("America/Chicago")
WATCHLIST_FILE = Path(__file__).parent / "watchlist.json"
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
    while max_rounds is None or rounds < max_rounds:
        rounds += 1
        started = time.monotonic()
        stamp = now().strftime("%Y-%m-%d %H:%M:%S %Z")

        if in_quiet_hours(quiet, now()):
            print("[%s] Quiet hours. No check." % stamp)
            wait = interval
        else:
            print("[%s] Checking..." % stamp)
            ok = run()
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
    loop(interval, quiet)


if __name__ == "__main__":
    main()
