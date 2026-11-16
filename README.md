# TCC Seat Watcher, cloud version

Checks your Tarrant County College sections every 5 minutes and emails you
when a seat opens. It runs on GitHub's computers, free, so your own
computer can be off.

It emails you only when a section has at least 1 open seat, and it will
not email you twice about the same opening.

---

## Important: the repository must be PUBLIC

GitHub only gives unlimited free run time to public repositories. A
private one gets 2,000 free minutes a month, and this uses about 8,600.
So make the repository **public**.

This is safe, because:

- Your Gmail address and password go in **GitHub Secrets**, never in the
  repository. Secrets stay hidden even in a public repository.
- The only thing public is your list of class section numbers, which is
  already public information on the TCC website.

**Never type your password into any file in this folder.**

---

## Setup, about 15 minutes, one time

### Step 1. Make a Gmail app password

This is not your normal password. It is a special 16 letter password just
for this app, so your real password stays safe.

1. Go to your Google Account, then **Security**.
2. Turn on **2-Step Verification** if it is off. You must do this first.
3. Search your Google Account for **App passwords**.
4. Create one and name it "TCC Watcher".
5. Google shows 16 letters. Copy them and keep them somewhere safe for a
   few minutes.

### Step 2. Put this folder on GitHub

1. Go to github.com and sign in, or make a free account.
2. Click **New repository**.
3. Name it something like `tcc-seat-watcher`.
4. Choose **Public**.
5. Click **Create repository**.
6. On the next page, click **uploading an existing file**.
7. Drag in everything from this folder, including the hidden `.github`
   folder. Then click **Commit changes**.

If you do not see the `.github` folder when dragging, you may need to
show hidden files: press `Cmd + Shift + .` on Mac, or tick
"Hidden items" in the View tab on Windows.

### Step 3. Add your secrets

In your new repository, go to:

**Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these three, one at a time:

| Name | Value |
|------|-------|
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | the 16 letter app password from Step 1 |
| `ALERT_TO` | where the alert should go |

Only add `SMTP_HOST` and `SMTP_PORT` if you are not using Gmail.

### Step 4. Turn the schedule on

1. Click the **Actions** tab.
2. If it asks, click the green button to enable workflows.
3. Click **Check TCC seats** on the left.
4. Click **Run workflow**. Tick **Test run with no email sent**, then run it.
5. Wait about a minute and click the run to see the log. It should list
   your sections and their seat counts.

Then run it once more **without** the dry run box ticked, to confirm real
email works. You will only get an email if a seat is actually open, so if
your classes are full, no email is normal and correct.

From now on it runs by itself every 5 minutes.

---

## Changing which classes you watch

Open `watchlist.json` in your repository and click the pencil icon to edit.

```json
[
  "BIOL-2402-86005",
  "BIOL-2402-86006"
]
```

Add or remove lines, then click **Commit changes**. The next check uses
your new list. Use the full section name exactly as TCC writes it.

---

## Seeing what it is doing

Click the **Actions** tab. Every run is listed with a log showing each
section and its seat count. A green tick means it ran fine.

A red X means something went wrong. GitHub emails you about failed runs,
so you will know.

---

## Getting it as a text message

Set the `ALERT_TO` secret to your carrier's text-to-email address:

| Carrier  | Address to use          |
|----------|-------------------------|
| AT&T     | yournumber@txt.att.net  |
| Verizon  | yournumber@vtext.com    |
| T-Mobile | yournumber@tmomail.net  |

Use your 10 digit number with no dashes. This is free, but carriers
sometimes delay or block these, so test it before you rely on it.

---

## Honest limits, please read

- **GitHub runs can be late.** The 5 minute schedule is a request, not a
  promise. When GitHub is busy, a run can be 5 to 20 minutes late, and a
  run can occasionally be skipped. This is the main cost of the free
  option. If a class is in very high demand, keep checking TCC yourself
  too around registration dates.
- **A 5 minute check can still miss a seat.** If someone drops at 2:01 and
  another student takes it at 2:03, the 2:05 check sees nothing. This
  improves your odds, it does not guarantee a seat.
- **It does not register you.** It only tells you. You still log in to
  TCC and take the seat yourself, and you should do it fast.
- **Do not lower the 5 minute setting.** GitHub does not allow faster, and
  hitting the TCC website harder would be rude and could get you blocked.
- **GitHub switches off schedules in quiet repositories after 60 days.**
  The included `keep-alive` workflow makes a small change monthly to stop
  that happening.

---

## If something goes wrong

**The run is red and says "Missing email settings"**
One of your three secrets is missing or misspelled. Names are case
sensitive, so check for `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_TO`.

**The run is red with a Gmail login error**
You are probably using your normal Gmail password. You need the 16 letter
app password, and 2-Step Verification must be on.

**A section says NOT FOUND**
Check the spelling in `watchlist.json`. It must be the full name like
`BIOL-2402-86005`. Also, a section only appears while that term is open
for registration, and TCC sometimes removes sections.

**"Could not find the antiforgery token"**
TCC changed their website and the code needs a small update. Bring this
message back to Claude.

**Nothing has run for a while**
Open the Actions tab. If there is a banner saying the schedule was
disabled, click to re-enable it.

---

## The files

| File | What it does |
|------|--------------|
| `watchlist.json` | Your classes. This is the file you edit. |
| `check_once.py` | Runs one check and stops. This is what the cloud runs. |
| `tcc_checker.py` | Reads the seat numbers from TCC. |
| `notifier.py` | Sends the email and remembers what it already sent. |
| `state.json` | Remembers what was already alerted. Created automatically. Do not edit. |
| `.github/workflows/check-seats.yml` | The every-5-minutes schedule. |
| `.github/workflows/keep-alive.yml` | Stops GitHub disabling the schedule. |
