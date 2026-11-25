# Database migration review: TFSC Agreements 5.4 on legal.24-7consultancy.cloud

Production is on release 5.3 (`32267ca`), so its database is at revision **`0014_integration_feed`**. The update applies
exactly one migration: **`0015_reminders`**. Migrations 0001–0014 are already applied and are not run again (Alembic
starts from the recorded revision). The migration history is unchanged: no migration was removed, renamed or edited.

How it is applied: `agreements.sh update` runs `database.init_db()` from the **new** image in a one-off container with the
web service's own configuration, after the encrypted, verified copy. `init_db()` takes a PostgreSQL advisory lock (one
migrator at a time) and runs `alembic upgrade head` inside **one transaction**: if anything fails, PostgreSQL undoes all
of it and nothing is switched. Statement timeout off, lock timeout 60 s (if a running request holds a lock longer, the
migration stops cleanly and can be repeated).

## 0015_reminders (new in this update)

| | |
|---|---|
| Revises | `0014_integration_feed` |
| Tables created | `reminder_rules` (id, name, description, first_after, every, max_count, weekdays JSON, hour_from, hour_to, combine, skip_opened_hours, remind_viewers, allow_signer_stop, notify_sender_after_last, active, builtin, created, updated, updated_by); `reminder_optouts` (id, email, name, entity, reason, until, created, created_by, created_by_name, removed_at, removed_by) |
| Columns added | `esign_recipients.reminders_off` BOOLEAN NOT NULL DEFAULT false; `esign_recipients.reminders_off_source` VARCHAR(12) NULL; `esign_recipients.reminders_paused_until` FLOAT NULL; `esign_recipients.reminder_anchor` FLOAT NULL; `esign_recipients.reminder_final_notice_at` FLOAT NULL; `mail_outbox.handed_at` FLOAT NULL |
| Indexes created | `ix_reminder_optouts_email` on `reminder_optouts(email)` (`CREATE INDEX IF NOT EXISTS`, on the new, empty table) |
| Rows inserted | 4 rules in `reminder_rules`: Standard (2, 3, 4), Gentle (5, 7, 2), Firm (1, 2, 6), Off (max 0), all Mon–Fri 9–17 |
| Settings inserted | `settings['reminders:installed_at']` = the moment of the migration (no "not signed" notices for reminders sent before it) |
| Existing data affected | **None changed.** Existing recipient rows get `reminders_off = false` and NULL in the four other columns; existing outbox rows get `handed_at = NULL`. No row is updated or deleted, no type changes, no rename. |
| Additive | Yes: only CREATE TABLE, ADD COLUMN (nullable, or with a constant default), CREATE INDEX, INSERT of new rows. |
| Idempotent | Yes: each table, column, index, rule and the setting is created only when missing (checked by inspection first). |
| Locking | `ALTER TABLE ... ADD COLUMN` takes a brief exclusive lock on `esign_recipients` and `mail_outbox`; on PostgreSQL 16 a constant default needs no table rewrite, so it lasts milliseconds once granted. |
| Runs with 5.3 still serving | Yes (it did in the rehearsal): 5.3 inserts recipients without the new columns (defaults apply) and ignores the new tables. |
| Downgrade (never run automatically) | **Destroys reminder data:** drops `reminder_optouts`, `reminder_rules` (with any rules the owner added), the five recipient columns (who was switched off, paused, skipped, told), `mail_outbox.handed_at`, and deletes the `reminders:%` settings (defaults per company, pause for everyone, installed_at). |

Behaviour once 5.4 runs (not a schema change, but a consequence to know): envelopes already out for signature follow
their company's default rule (Standard) and keep their count of reminders already sent. An envelope whose last reminder
under 5.3 was long ago can receive its next reminder in the first sending window after the update (as 5.3 would have),
limited to one automatic email per person per company per day and two in all. Envelopes set to Off in 5.3 stay off.

## 0001–0014 (already applied in production; listed for completeness)

| Migration | Upgrade does | Additive | Downgrade removes |
|---|---|---|---|
| 0001_agreements_baseline | creates the 19 baseline tables and their indexes | yes | all 19 tables (everything) |
| 0002_esign | the e-signature tables and their indexes | yes | those tables |
| 0003_docusign_parity | new tables, columns and indexes for envelope features | yes | them |
| 0004_premium_portal | new tables and columns | yes | them |
| 0005_signature_size | one column | yes | it |
| 0006_custom_companies | companies table, one column | yes | them |
| 0007_test_marks | test markers (one column) | yes | it |
| 0008_client_records | client numbers (new column, filled for existing clients), past agreements, import staging tables | yes (fills only the new column) | them |
| 0009_five_companies | data only: five more companies (existing ones untouched) | yes | nothing |
| 0010_billing_nest_letterhead | data only: one company's letterhead where the old file was still in use | yes | nothing |
| 0011_client_trash | Trash columns on clients (nullable), client number registry, protective triggers | yes | them |
| 0012_audit_fixes | session start times, mailbox pause columns, an index, a protected signing history | yes | them |
| 0013_followups_margins | follow-up notes table; side margins set for future agreements only | yes | the table |
| 0014_integration_feed | the OCS feed tables (feed, offered clients, tokens) and two settings; indexes built later by the worker without blocking | yes | them |

## Rules for this server

- **No automatic downgrade, ever.** `agreements.sh rollback` and the automatic step-back switch the application image
  only. 5.3 runs on the 0015 schema: its migration step sees a newer revision, logs "Database revision 0015_reminders is
  newer than this release; starting without migrating" and starts (`migrate.py`; tested in the rehearsals).
- To truly undo the schema, restore the encrypted pre-update copy instead (UPDATE-AND-ROLLBACK.md). That also undoes
  every business change made after the copy, so it is a last resort, decided by the owner.
- A future release with a migration not listed in `prod.conf` (`KNOWN_MIGRATIONS`) is flagged by
  `validate-release.sh`; one containing a drop, alter, rename, DELETE or TRUNCATE in `upgrade()` is flagged for review.
