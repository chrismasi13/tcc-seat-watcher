"""
TCC seat checker.

Talks to the Tarrant County College public course search and returns
how many seats are open in each section you care about.

No sign-in is needed. This uses the same JSON requests that the
TCC web page itself uses, so it is fast and light.

The three steps are:
  1. GET  /Student/Courses/Search      -> read the antiforgery token
  2. POST /Student/Courses/PostSearchCriteria -> course id + section ids
  3. POST /Student/Courses/Sections    -> the seat numbers
"""

import re
import requests

BASE = "https://selfservice.tccd.edu"
SEARCH_PAGE = BASE + "/Student/Courses/Search"
CRITERIA_URL = BASE + "/Student/Courses/PostSearchCriteria"
SECTIONS_URL = BASE + "/Student/Courses/Sections"

TOKEN_RE = re.compile(
    r'name="__RequestVerificationToken"[^>]*value="([^"]+)"'
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class TccError(Exception):
    """Something went wrong talking to TCC."""


def _new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _get_token(session, course_code):
    """Open the search page and pull out the antiforgery token."""
    r = session.get(
        SEARCH_PAGE, params={"keyword": course_code}, timeout=30
    )
    r.raise_for_status()
    m = TOKEN_RE.search(r.text)
    if not m:
        raise TccError(
            "Could not find the antiforgery token on the search page. "
            "TCC may have changed their website."
        )
    return m.group(1)


def fetch_course(course_code, session=None):
    """
    Look up one course, for example "BIOL-2402".

    Returns a list of dicts, one per section:
        {section, term, open, capacity, waitlisted, status, instructor}
    """
    own_session = session is None
    session = session or _new_session()
    try:
        token = _get_token(session, course_code)
        headers = {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "__RequestVerificationToken": token,
            "Referer": SEARCH_PAGE + "?keyword=" + course_code,
        }

        # Step 2: which course and which sections match the keyword
        r = session.post(
            CRITERIA_URL,
            json={"keyword": course_code},
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        courses = r.json().get("Courses") or []
        if not courses:
            raise TccError("No course found for " + course_code)

        wanted = course_code.replace(" ", "").upper()
        course = None
        for c in courses:
            name = (c.get("SubjectCode", "") + "-" + c.get("Number", "")).upper()
            if name == wanted:
                course = c
                break
        course = course or courses[0]

        course_id = course.get("Id")
        section_ids = course.get("MatchingSectionIds") or []
        if not section_ids:
            return []

        # Step 3: the actual seat numbers
        r = session.post(
            SECTIONS_URL,
            json={"courseId": course_id, "sectionIds": section_ids},
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("SectionsRetrieved") or {}

        results = []
        for block in data.get("TermsAndSections") or []:
            term = (block.get("Term") or {}).get("Description", "")
            for item in block.get("Sections") or []:
                sec = item.get("Section") or {}
                results.append(
                    {
                        "section": sec.get("SectionNameDisplay", ""),
                        "term": term,
                        "open": sec.get("Available"),
                        "capacity": sec.get("Capacity"),
                        "waitlisted": sec.get("Waitlisted"),
                        "status": sec.get("AvailabilityStatus", ""),
                        "unlimited": bool(sec.get("HasUnlimitedSeats")),
                        "instructor": item.get("FacultyDisplay", ""),
                    }
                )
        return results
    finally:
        if own_session:
            session.close()


def check_sections(section_names):
    """
    Check a list of full section names, for example
    ["BIOL-2402-86005", "BIOL-2402-86006"].

    Sections of the same course are fetched in one request.
    Returns a dict: section name -> info dict (or None if not found).
    """
    by_course = {}
    for name in section_names:
        parts = name.strip().upper().split("-")
        if len(parts) < 3:
            raise TccError(
                "Section name should look like BIOL-2402-86005, got: " + name
            )
        course_code = parts[0] + "-" + parts[1]
        by_course.setdefault(course_code, []).append(name.strip().upper())

    found = {}
    session = _new_session()
    try:
        for course_code, names in by_course.items():
            sections = fetch_course(course_code, session=session)
            lookup = {s["section"].upper(): s for s in sections}
            for name in names:
                found[name] = lookup.get(name)
    finally:
        session.close()
    return found


def has_open_seat(info):
    """True when this section has at least one seat you could take."""
    if not info:
        return False
    if info.get("unlimited"):
        return True
    open_seats = info.get("open")
    return isinstance(open_seats, int) and open_seats >= 1


if __name__ == "__main__":
    import sys

    names = sys.argv[1:] or ["BIOL-2402-86005", "BIOL-2402-86006"]
    for name, info in check_sections(names).items():
        if not info:
            print(name + ": NOT FOUND")
        else:
            print(
                "{}: {} open of {} ({}) [{}]".format(
                    name,
                    info["open"],
                    info["capacity"],
                    info["status"],
                    info["term"],
                )
            )
