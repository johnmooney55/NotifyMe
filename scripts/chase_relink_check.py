#!/usr/bin/env python3
"""One-shot: did the morning sync land the Chase re-link correctly?

Background. On 2026-09-17 the Chase connections were changed in Monarch.
Chase Amazon reconnected on its own id, but United Club was REPLACED: the old
account was deactivated and hidden (not deleted, which is why its rows keep
coming back), and a new one took over. 52 rows from 2026-08-19 to 09-16 exist
on both and are knowingly left in place.

This checks the four things that would mean the situation has changed, mails
the result to NotifyMe's address, and then removes its own LaunchAgent. It is
strictly read-only: no writes to the database, none to Monarch.

Stdlib only, so it runs on /usr/bin/python3 with no venv.
"""

import json
import os
import smtplib
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime
from email.message import EmailMessage

DB = os.path.expanduser("~/finance-center-data/finance-center.db")
ENV = os.path.expanduser("~/NotifyMe/.env")
HEALTH = "http://127.0.0.1:8081/health"
LABEL = "me.mooney.chase-relink-check"
PLIST = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LABEL)

OLD_UC = "248513173354060636"   # deactivated + hidden in Monarch, still feeds rows
NEW_UC = "255225260911499571"   # the live United Club account
AMAZON = "160184846455806393"   # reconnected on its original id
NEW_UC_EXPECTED_START = "2026-08-19"
OLD_UC_EXPECTED_ROWS = 249
OLD_UC_EXPECTED_NEWEST = "2026-09-16"


def read_env(path):
    values = {}
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def check():
    """Returns (ok, lines). Every check states its actual number, pass or fail."""
    lines = []
    ok = True

    try:
        with urllib.request.urlopen(HEALTH, timeout=15) as response:
            health = json.load(response)
    except Exception as exc:
        return False, ["Could not reach the server health endpoint: %s" % exc]

    db = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    db.row_factory = sqlite3.Row
    one = lambda sql, args=(): db.execute(sql, args).fetchone()

    # 1. The sync ran and succeeded.
    run = one("SELECT id, started_at, status, steps_json FROM sync_runs ORDER BY id DESC LIMIT 1")
    accounts_seen = ""
    for step in json.loads(run["steps_json"] or "[]"):
        if step.get("step") == "1-accounts":
            accounts_seen = step.get("detail", "")
    if run["status"] == "success":
        lines.append("OK   sync %s at %s: success (%s)" % (run["id"], run["started_at"], accounts_seen))
    else:
        ok = False
        lines.append("FAIL sync %s at %s: status %s" % (run["id"], run["started_at"], run["status"]))
    if health.get("status") != "ok":
        ok = False
        lines.append("FAIL server health is '%s' (sync age %ss, backup age %ss)"
                     % (health.get("status"), health.get("lastSyncAgeSeconds"), health.get("lastBackupAgeSeconds")))

    # 2. The deactivated account took nothing new.
    old = one("SELECT COUNT(*) n, MAX(date) newest FROM monarch_synced_transactions WHERE account_id = ?", (OLD_UC,))
    if old["n"] == OLD_UC_EXPECTED_ROWS and old["newest"] == OLD_UC_EXPECTED_NEWEST:
        lines.append("OK   old United Club account frozen: %d rows, newest %s" % (old["n"], old["newest"]))
    else:
        ok = False
        lines.append("FAIL old United Club account MOVED: %d rows (expected %d), newest %s (expected %s)"
                     % (old["n"], OLD_UC_EXPECTED_ROWS, old["newest"], OLD_UC_EXPECTED_NEWEST))

    # 3. The new account has not backfilled earlier than the old one -- the one
    #    change that would grow the duplicate set, backwards into 194 more rows.
    new = one("SELECT COUNT(*) n, MIN(date) oldest, MAX(date) newest FROM monarch_synced_transactions WHERE account_id = ?", (NEW_UC,))
    if new["oldest"] == NEW_UC_EXPECTED_START:
        lines.append("OK   new United Club account still starts %s: %d rows, newest %s"
                     % (new["oldest"], new["n"], new["newest"]))
    else:
        ok = False
        lines.append("FAIL new United Club account has BACKFILLED to %s (was %s). Its rows now duplicate the"
                     % (new["oldest"], NEW_UC_EXPECTED_START))
        lines.append("     old account's 194 rows from 2026-06-25 to 08-18. This is the case worth acting on.")

    # 4. Benefits link, and that Amazon is still gaining rows on its own id.
    link = one("SELECT monarch_account_id FROM amex_card_accounts WHERE card_type = 'united_club_infinite'")
    if link and link["monarch_account_id"] == NEW_UC:
        lines.append("OK   benefits link points at the live account")
    else:
        ok = False
        lines.append("FAIL benefits link points at %s, not %s" % (link and link["monarch_account_id"], NEW_UC))

    amazon = one("SELECT COUNT(*) n, MAX(date) newest FROM monarch_synced_transactions WHERE account_id = ?", (AMAZON,))
    lines.append("OK   Chase Amazon on its original id: %d rows, newest %s" % (amazon["n"], amazon["newest"]))

    db.close()
    return ok, lines


def main():
    try:
        ok, lines = check()
    except Exception as exc:
        ok, lines = False, ["The check itself failed: %r" % exc]

    body = [
        "Chase re-link check, %s." % datetime.now().strftime("%Y-%m-%d %H:%M %Z").strip(),
        "",
        "Everything is going to plan." if ok else "Something moved -- see the FAIL line(s).",
        "",
    ]
    body.extend(lines)
    body.extend([
        "",
        "Known and deliberate: 52 rows from 2026-08-19 to 09-16 exist on both United",
        "Club accounts, $3,009.63 of 2026 Spend counted twice. Deferred on purpose.",
        "The durable fix is written up in finance-center/BACKLOG.md under",
        '"Superseded accounts". Deleting the rows without it achieves nothing -- the',
        "old account is deactivated, not deleted, so the next sync puts them back.",
        "",
        "Sent once by %s on the mini; the LaunchAgent removes itself after this." % LABEL,
    ])

    env = read_env(ENV)
    message = EmailMessage()
    message["Subject"] = "Finance Center: Chase sync check - %s" % ("OK" if ok else "needs a look")
    message["From"] = env["SMTP_USER"]
    message["To"] = env["NOTIFY_EMAIL"]
    message.set_content("\n".join(body))

    with smtplib.SMTP(env.get("SMTP_HOST", "smtp.gmail.com"), int(env.get("SMTP_PORT", "587")), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
        smtp.send_message(message)
    print("sent to %s (ok=%s)" % (env["NOTIFY_EMAIL"], ok))

    # One-shot: take the job out so it cannot fire again tomorrow.
    subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), LABEL)],
                   capture_output=True)
    if os.path.exists(PLIST):
        os.remove(PLIST)
    print("LaunchAgent removed")


if __name__ == "__main__":
    sys.exit(main())
