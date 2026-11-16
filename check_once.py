"""
Checks your TCC sections one time, then stops.

This is what GitHub runs every 5 minutes in the cloud.
It does not need your computer to be on.

It emails you only when a section has at least one open seat,
and it will not email twice about the same opening.
"""

import json
import os
import sys
import time
from pathlib import Path

import notifier
from tcc_checker import TccError, check_sections

HERE = Path(__file__).parent
WATCHLIST_FILE = HERE / "watchlist.json"

# TCC is sometimes briefly unreachable. Try a few times before giving up,
# so a one second hiccup does not send you a failure email from GitHub.
TRIES = 3
WAIT_BETWEEN_TRIES = 20  # seconds


def load_watchlist():
    if not WATCHLIST_FILE.exists():
        print("No watchlist.json found. Nothing to check.")
        return []
    try:
        raw = json.loads(WATCHLIST_FILE.read_text())
    except ValueError as exc:
        print("watchlist.json is not valid JSON: %s" % exc)
        print('It should look like:  ["BIOL-2402-86005", "BIOL-2402-86006"]')
        sys.exit(1)

    sections = []
    for item in raw:
        name = str(item).strip().upper()
        if name and not name.startswith("#"):
            sections.append(name)
    return sections


def check_with_retries(sections):
    last_error = None
    for attempt in range(1, TRIES + 1):
        try:
            return check_sections(sections)
        except Exception as exc:
            last_error = exc
            print("Attempt %d of %d failed: %s" % (attempt, TRIES, exc))
            if attempt < TRIES:
                time.sleep(WAIT_BETWEEN_TRIES)
    raise last_error


def main():
    sections = load_watchlist()
    if not sections:
        print("Watch list is empty. Add sections to watchlist.json.")
        return 0

    print("Checking %d section(s): %s" % (len(sections), ", ".join(sections)))

    try:
        results = check_with_retries(sections)
    except TccError as exc:
        print("ERROR: " + str(exc))
        return 1
    except Exception as exc:
        print("ERROR: could not reach TCC: %s" % exc)
        return 1

    # Show every section in the run log, so you can look back later.
    missing = []
    for name in sections:
        info = results.get(name)
        if info is None:
            missing.append(name)
            print("  %-20s NOT FOUND on the TCC site" % name)
        else:
            print(
                "  %-20s %s open of %s   (%s)"
                % (name, info["open"], info["capacity"], info["status"])
            )

    send = os.environ.get("DRY_RUN", "").lower() not in ("1", "true", "yes")
    outcome = notifier.process_results(results, send=send)

    if outcome["opened"]:
        if outcome["email_sent"]:
            print("EMAIL SENT for: " + ", ".join(outcome["opened"]))
        elif not send:
            print("DRY RUN, no email. Would have alerted: "
                  + ", ".join(outcome["opened"]))
        else:
            print("EMAIL FAILED: " + str(outcome["email_error"]))
            print("It will try again on the next run.")
            return 1
    else:
        print("No new openings. No email sent.")

    if missing:
        print(
            "Note: %d section(s) were not found. Check the spelling in "
            "watchlist.json, or the section may have been removed." % len(missing)
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
