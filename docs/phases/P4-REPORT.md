# P4 report: TFS Agreements 5.4, reminders you control, and an email is never sent twice

Branch `claude/tfs-agreements-5.4-reminders`, started from release 5.3 (`111530d`; the shipped files are identical to
the installed 5.3 ZIP `32267ca`). The release commit is `a221398`; this report and the guide's test notes follow in a
documentation-only commit, and the update ZIP is built from that one.

## Failures and gaps first

- **The first rehearsal run failed one step**: the worker did not send the next reminder within 150 seconds. The fault
  was in my rehearsal script, not the release. I had moved the last reminder two days back but left the invitation dated
  today, and reminders count from whichever is later, so the next reminder was correctly planned for the next day. The
  script now dates the envelope 9 days back. In the second run, from scratch, the worker sent it after 60 seconds.
- **The same first run**: the helper that creates the "reminders Off" envelope could not import its shared module, so
  that envelope was missing. Fixed; the second run includes it, and it stayed Off after the update.
- **Bugs found and fixed while building** (none shipped):
  1. a reminder stuck in the queue for more than 7 days was no longer seen by the "nothing new while one waits" guard,
     so a second one could pile up (found by a test);
  2. the Reminder centre showed the due time in the viewer's own time zone while labelling it with the company's (found
     on the screenshots);
  3. a company's time zone change would only have applied after a restart;
  4. when the mail server explicitly refused an email after receiving it, the "handed over" mark stayed on the copy
     waiting to be retried (found on review; a test now covers it).
- **Not tested here**: real Google Workspace mailboxes (a test mail server played their part), the public HTTPS
  certificate, and reminders over real working days (the schedule is tested with a simulated clock, and once live in
  Docker under a rule that may send at any hour).
- **One existing test was changed on purpose**: `test_esign.py::test_reminders_expiry_warning_and_expiry`. Reminders
  no longer come from per-envelope day counts; the test now uses a rule with the schedule it had before (after 1 day,
  then every 2, any day and hour). Its expectations are unchanged.

## The repeated emails: cause and fix

When the mail server took an email but sent its "received" answer after the app stopped waiting (20 seconds), the
email was treated as not sent and put back in the queue without counting the try. It was sent again on the next pass,
and the next, with no end. Reproduced before the fix: one copy per pass.

Fix (`mailer.py`): the app records the moment the email's content is handed over. If it fails after that point
without an explicit "not accepted" from the server, the email is marked **uncertain** ("Not confirmed"), its try is
counted, and it is never sent again by itself. If the worker stops after handing over, the next start marks it
uncertain too. Uncertain emails wait under **Agreements › Reminders › Needs a look** (Mark as delivered / Send once
more), and Admin › System check shows a line while any exist. Large emails get longer to be confirmed (20 s + 15 s per
MB, at most 5 minutes). A server that explicitly refuses is retried as before. On screen, buttons ignore a second press
while their action runs (no double invitation from a double click).

## What was built

- **Rules** (`reminders.py`, Admin › Reminders, Super Admin): Standard (after 2 working days, then every 3, at most 4,
  Mon–Fri 9–17 company time, then the sender is told), Gentle (5, 7, 2), Firm (1, 2, 6), Off, and any new rule. Options:
  one email per person per day, skip if opened recently, remind viewers, signer stop link, tell the sender after the
  last. Built-in rules cannot be retired; a company default cannot be retired; Off always stays at 0.
- **Company defaults** (Standard for all to start), and a rule per envelope chosen when sending or later on the
  envelope's Reminders card.
- **Per person**: off/on, pause until a date, skip the next, Remind now (at most once a day; never to someone on the
  list, switched off, undeliverable, or with a reminder still waiting). **Per envelope**: pause. **Everyone**: pause all.
  **Do-not-remind list** (Super Admin): every company or one, always or until a date, with a reason.
- **Reminder centre** (Agreements › Reminders): upcoming 7 days (with bulk skip, pause, switch off), sent, paused and
  off, needs a look. Senders see their own envelopes, Admins their team's, the Super Admin everything.
- **Emails**: "Reminder N of M" badge, a Stop reminders link (a page with a confirm button: opening the link does
  nothing, so mail scanners cannot trigger it; the sender is told), one combined email when several envelopes from a
  company are due for the same person, and the sender's notices "Reminders stopped" and "Not signed after reminders".
- **Guards no rule can switch off**: 1 automatic email per person per company per day, 2 in all; nothing new while an
  earlier reminder waits or is unconfirmed; never to a bounced address, after signing, declining or reassignment, or on
  a finished envelope; the expiry warning respects the same switches.
- **Existing envelopes**: follow their company default with their reminders counted; Off stays Off; no "not signed"
  notices for envelopes that reached their last reminder before the update.
- **Migration 0015**: two tables, five nullable or defaulted recipient columns, one outbox column, four rules, one
  setting; idempotent, with a downgrade.

## Tests

New: `apps/agreements/tests/test_reminders.py`, 40 tests (repeated-email cause and the worker-stop cases, the explicit
refusal case, working days, hours, time zones and daylight saving, the Standard rule end to end, signing and void,
opened recently, legacy Off, envelope rule and company default, combined email, daily limits, the waiting guard,
bounced addresses, viewers, pause all, off/on, off when sending, pause and skip, remind now, envelope card, bulk, the
do-not-remind list and its permissions, the expiry warning, the stop link (open, confirm, twice, forged, not allowed by
the rule), rule validation and permissions, rule changes on envelopes already out, who sees what, Needs a look, the
System check, the final notice only for reminders after the update).

| Run (final code) | Result |
|---|---|
| SQLite | 352 passed (312 from 5.3 + 40 new), 270 subtests passed |
| PostgreSQL 16 (`tfsc_test`, UTF-8) | 352 passed (312 from 5.3 + 40 new), 270 subtests passed |

`ruff check .` passes. The screens (Reminder centre, envelope card, send settings, Admin › Reminders, the stop page)
were checked in Chromium at 1400 px and 390 px with no console errors and no sideways scrolling.

## Update rehearsal (5.3 → 5.4, Docker, from scratch, fictional data; second run)

5.3 ZIP installed with its own installer; client signed; envelope out for signature dated 9 days back with 2 reminders;
envelope with reminders Off; OCS token; checker **39 passed, 0 failed**. Update as the guide says: **19 s**, database
`0014_integration_feed → 0015_reminders`. Every row and file that existed before (185, compared on the columns they
had) was unchanged except six settings that change every minute anyway (four worker heartbeats, the feed publisher's
last run and watermark); what was offered to OCS was identical; feed `head_seq` 1 before and after. The out envelope
showed Standard, 2 of 4 sent, next on the following working day at 9:00 company time; the Off envelope showed Off.
Under a test rule the worker sent "Reminder 3 of 6" after 60 s; the stop link page showed a button and changed
nothing; pressing it stopped reminders and the sender got "Reminders stopped"; no further reminder in the next 75 s.
The signing link from before the update was signed and the envelope completed. A new client signed and reached the
feed; checker after: **47 passed, 0 failed**. Rollback to 5.3 on the updated database: up, signed in, 4 envelopes
listed; update again: up; checker **47 passed, 0 failed**.

## Not changed

The OCS integration feed (`integration_v1.py`) and its contract (`packages/integration-contract`) are byte for byte
unchanged; the CRM bridge is untouched; no setting, table or column was removed or altered; no SMS or WhatsApp.
