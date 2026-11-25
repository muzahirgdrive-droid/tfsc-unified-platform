"""Release 5.4: reminders you control, and an email is never sent twice.

Covers the cause of the repeated emails (an email the mail server took but confirmed slowly was sent again and again), the
reminder rules (working days, sending hours, time zones), the safety guards (daily limits, nothing new while an earlier
reminder waits, never to someone who bounced, signed or is on the do-not-remind list), the switches (off, pause, skip,
remind now), the signer's stop link, the final notice to the sender, permissions and the Reminder centre. All data is
fictional.
"""
import datetime as dt
import re
import sys
import time
import zoneinfo
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, update

import database as db
import esign
import esign_db as t
import esign_support
import mailer
import reminders
from test_esign import EHS, SCRIBE, Base

LA = zoneinfo.ZoneInfo('America/Los_Angeles')     # Scribe Align's time zone
JANE = 'jane@client.test'


def at(day: dt.date, hour: int, minute: int = 0) -> float:
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=LA).timestamp()


class Clock:
    """One clock for the e-signature engine, the mail queue and the reminder screens (the tests move it)."""
    def __init__(self, value: float):
        self.t = value

    def time(self):
        return self.t

    def __getattr__(self, name):
        return getattr(time, name)


class ReminderBase(Base):
    def setUp(self):
        super().setUp()
        start = dt.datetime.now(LA).date() + dt.timedelta(days=21)
        self.monday = start - dt.timedelta(days=start.weekday())           # a Monday three weeks ahead
        self.clock = Clock(at(self.monday, 10))
        self.patches = [patch.object(esign, 'now', side_effect=lambda: self.clock.t), patch.object(mailer, 'time', self.clock),
                        patch.object(reminders, 'time', self.clock)]
        for p in self.patches:
            p.start()
        with db.engine.begin() as c:       # reminders sent in these tests count as sent by this release
            reminders._put(c, reminders.SETTING_INSTALLED, 0)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def prepare(self, recipients=None, include_signature=True):
        self.connect_mailbox()
        contract_id, client_id = self.contract(self.u1, include_signature=include_signature)
        self.login(self.u1)
        r = self.post('/api/esign/envelopes', {'contract_id': contract_id})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json(), client_id

    def day(self, n: int) -> dt.date:
        return self.monday + dt.timedelta(days=n)

    def send(self, settings=None, people=None, mailbox=True):
        """An envelope from Scribe Align to Jane (or to people(envelope)), sent at the clock's time."""
        if mailbox:
            env, _ = self.prepare()
        else:
            contract_id, _ = self.contract(self.u1)
            self.login(self.u1)
            env = self.post('/api/esign/envelopes', {'contract_id': contract_id}).json()
        rs = people(env) if people else [dict(env['recipients'][0], name='Jane Example')]
        fields = [dict(f, recipient=f['recipient_id']) for f in env['fields'] if f['recipient_id'] in {r.get('id') for r in rs}]
        body = {'recipients': rs, 'fields': fields}
        if settings is not None:
            body['settings'] = settings
        r = self.post(f"/api/esign/envelopes/{env['id']}/draft", body)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.post(f"/api/esign/envelopes/{env['id']}/send")
        self.assertEqual(r.status_code, 200, r.text)
        self.drain()
        return self.api.get(f"/api/esign/envelopes/{env['id']}").json()

    def tick(self, when: float) -> int:
        self.clock.t = when
        n = reminders.run()
        self.drain()
        return n

    def reminders_to(self, address=JANE):
        return [m for m in self.smtp.to(address) if m['msg']['Subject'].startswith('Reminder:')]

    def recipient(self, rid):
        with db.engine.connect() as c:
            return dict(c.execute(select(t.recipients).where(t.recipients.c.id == rid)).mappings().one())

    def add_rule(self, rid, **values):
        base = dict(id=rid, name=rid.title(), description='', first_after=2, every=3, max_count=4, weekdays=[0, 1, 2, 3, 4], hour_from=9, hour_to=17,
                    combine=True, skip_opened_hours=24, remind_viewers=False, allow_signer_stop=True, notify_sender_after_last=True, active=True,
                    builtin=False, created=0, updated=0)
        with db.engine.begin() as c:
            c.execute(reminders.rules.insert().values(**(base | values)))


# ====================================================================== the cause of the repeated emails
class SlowHandler(esign_support._Handler):
    """A mail server that stores the message, then takes 2 seconds to say so."""
    def handle(self):
        write = self.wfile.write

        def slow(data):
            if data.startswith(b'250 queued'):
                time.sleep(2.0)
            try:
                return write(data)
            except OSError:
                return 0
        self.wfile.write = slow
        return super().handle()


class SentOnceTests(Base):
    def queue_one(self):
        self.connect_mailbox()
        with db.engine.begin() as c:
            return mailer.enqueue(c, SCRIBE, 'reminder', ['signer@client.test'], 'Reminder: please sign', 'Please sign.', '<p>Please sign.</p>')

    def test_an_email_the_server_took_but_did_not_confirm_is_never_sent_again(self):
        self.smtp.RequestHandlerClass = SlowHandler
        self.queue_one()
        with patch.object(mailer, 'SMTP_TIMEOUT', 1.0):
            for _ in range(4):
                mailer.dispatch_once()
                time.sleep(2.3)
                with db.engine.begin() as c:            # whatever pause the mailbox took is over
                    c.execute(update(t.mailboxes).values(paused_until=None))
        self.assertEqual(len(self.smtp.messages), 1)     # before 5.4: one copy per pass, for ever
        with db.engine.connect() as c:
            row = c.execute(select(t.outbox)).mappings().one()
        self.assertEqual((row['state'], row['attempts']), ('uncertain', 1))
        self.assertIsNotNone(row['handed_at'])

    def test_a_worker_that_stopped_after_handing_over_does_not_resend(self):
        self.queue_one()
        with db.engine.begin() as c:
            c.execute(update(t.outbox).values(state='sending', lease_until=time.time() - 5, handed_at=time.time() - 60))
        mailer.dispatch_once()
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(t.outbox.c.state)).scalar(), 'uncertain')
        self.assertEqual(self.smtp.messages, [])

    def test_a_worker_that_stopped_before_handing_over_still_sends_once(self):
        self.queue_one()
        with db.engine.begin() as c:
            c.execute(update(t.outbox).values(state='sending', lease_until=time.time() - 5, handed_at=None))
        for _ in range(3):
            mailer.dispatch_once()
        self.assertEqual(len(self.smtp.messages), 1)
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(t.outbox.c.state)).scalar(), 'sent')

    def test_a_refusal_by_the_server_is_not_uncertain(self):
        self.smtp.reject_rcpt = '550 No such user'
        self.queue_one()
        mailer.dispatch_once()
        with db.engine.connect() as c:
            self.assertNotEqual(c.execute(select(t.outbox.c.state)).scalar(), 'uncertain')

    def test_a_server_that_refuses_after_reading_the_message_is_retried_normally(self):
        class Refusing(esign_support._Handler):
            def handle(self):
                write = self.wfile.write
                self.wfile.write = lambda data: write(b'451 Try again later\r\n' if data.startswith(b'250 queued') else data)
                return super().handle()
        self.smtp.RequestHandlerClass = Refusing
        self.queue_one()
        mailer.dispatch_once()
        with db.engine.connect() as c:
            row = c.execute(select(t.outbox)).mappings().one()
        self.assertEqual(row['state'], 'queued')            # the server said "not accepted": sending again is right
        self.assertIsNone(row['handed_at'])

    def test_handover_timeout_grows_with_the_size_of_the_email(self):
        self.assertEqual(mailer.handover_timeout(1000), mailer.SMTP_TIMEOUT + 15 * 1000 / 1_000_000)
        self.assertGreater(mailer.handover_timeout(10_000_000), mailer.handover_timeout(1_000_000))
        self.assertEqual(mailer.handover_timeout(10 ** 10), 300)


# ====================================================================== schedule: working days, hours, time zones
class ScheduleTests(ReminderBase):
    standard = dict(first_after=2, every=3, max_count=4, weekdays=[0, 1, 2, 3, 4], hour_from=9, hour_to=17)

    def test_working_days_and_sending_hours(self):
        r = self.standard
        # sent Friday 16:30: two working days later is Tuesday 16:30, inside 9-17
        friday = self.day(4)
        self.assertEqual(reminders.schedule_after(at(friday, 16, 30), 2, r, SCRIBE), at(self.day(8), 16, 30))
        # sent Saturday 20:00: Tuesday 20:00 is after hours, so Wednesday 9:00
        self.assertEqual(reminders.schedule_after(at(self.day(5), 20), 2, r, SCRIBE), at(self.day(9), 9))
        # sent Monday 7:00: Wednesday 7:00 is before hours, so Wednesday 9:00
        self.assertEqual(reminders.schedule_after(at(self.day(0), 7), 2, r, SCRIBE), at(self.day(2), 9))
        self.assertTrue(reminders.in_window(dt.datetime(2030, 1, 7, 9, 0, tzinfo=LA), r))
        self.assertFalse(reminders.in_window(dt.datetime(2030, 1, 7, 17, 0, tzinfo=LA), r))
        self.assertFalse(reminders.in_window(dt.datetime(2030, 1, 5, 12, 0, tzinfo=LA), r))     # a Saturday

    def test_company_time_zone_and_daylight_saving(self):
        r = self.standard
        # 2030-03-08 is the Friday before clocks go forward in the US: 10:00 local stays 10:00 local
        before = dt.datetime(2030, 3, 8, 10, tzinfo=LA).timestamp()
        due = dt.datetime.fromtimestamp(reminders.schedule_after(before, 2, r, SCRIBE), LA)
        self.assertEqual((due.date(), due.hour, due.minute), (dt.date(2030, 3, 12), 10, 0))
        # the same moment for Extended Health Services (Chicago) lands at 10:00 Chicago time
        chicago = zoneinfo.ZoneInfo('America/Chicago')
        due = dt.datetime.fromtimestamp(reminders.schedule_after(dt.datetime(2030, 3, 8, 10, tzinfo=chicago).timestamp(), 2, r, EHS), chicago)
        self.assertEqual((due.date(), due.hour), (dt.date(2030, 3, 12), 10))

    def test_standard_rule_end_to_end(self):
        env = self.send()
        jane = env['recipients'][0]
        self.assertEqual(self.tick(at(self.day(1), 10)), 0)          # Tuesday: too early
        self.assertEqual(self.tick(at(self.day(2), 9, 59)), 0)       # Wednesday 9:59: not yet
        self.assertEqual(self.tick(at(self.day(2), 10)), 1)          # Wednesday 10:00: reminder 1
        self.assertEqual(self.tick(at(self.day(2), 15)), 0)          # never twice the same day
        msg = self.reminders_to()[0]
        text = msg['msg'].get_body(('plain',)).get_content()
        self.assertIn('Reminder 1 of 4', text)
        self.assertIn('/reminders/stop/', text)
        self.assertEqual(self.tick(at(self.day(7), 10)), 1)          # Monday: 3 working days later
        self.assertEqual(self.tick(at(self.day(10), 10)), 1)         # Thursday
        self.assertEqual(self.tick(at(self.day(15), 10)), 1)         # Tuesday: the 4th and last
        self.assertEqual(len(self.reminders_to()), 4)
        self.assertEqual(self.recipient(jane['id'])['reminders_sent'], 4)
        # three working days after the last reminder the sender is told, once; Jane gets nothing more
        self.assertEqual(self.tick(at(self.day(18), 10)), 1)
        told = [m for m in self.smtp.to('sender@scribealign.test') if m['msg']['Subject'].startswith('Not signed after reminders:')]
        self.assertEqual(len(told), 1)
        self.assertIn('4 reminders', told[0]['msg'].get_body(('plain',)).get_content())
        for n in (19, 22, 30):
            self.assertEqual(self.tick(at(self.day(n), 11)), 0)
        self.assertEqual(len(self.reminders_to()), 4)
        self.assertEqual(len([m for m in self.smtp.to('sender@scribealign.test') if m['msg']['Subject'].startswith('Not signed after reminders:')]), 1)
        # the whole story is in the envelope's history
        kinds = [h['kind'] for h in self.api.get(f"/api/esign/envelopes/{env['id']}").json()['history']]
        self.assertEqual(kinds.count('reminder'), 4)
        self.assertEqual(kinds.count('reminders_exhausted'), 1)

    def test_nothing_after_signing_and_nothing_on_a_finished_envelope(self):
        env = self.send()
        jane = env['recipients'][0]
        with db.engine.begin() as c:
            c.execute(update(t.recipients).where(t.recipients.c.id == jane['id']).values(status='signed'))
        self.assertEqual(self.tick(at(self.day(2), 10)), 0)
        env2 = self.send()
        self.post(f"/api/esign/envelopes/{env2['id']}/void", {'reason': 'Sent by mistake'})
        self.assertEqual(self.tick(at(self.day(4), 10)), 0)
        self.assertEqual(self.reminders_to(), [])

    def test_opened_recently_moves_the_reminder(self):
        env = self.send()
        jane = env['recipients'][0]
        self.clock.t = at(self.day(2), 8)     # Jane opened the envelope at 8:00 on the day of her first reminder
        with db.engine.begin() as c:
            esign.record(c, env['id'], 'delivered', 'Jane Example', recipient_id=jane['id'])
        self.assertEqual(self.tick(at(self.day(2), 10)), 0)
        self.assertEqual(self.tick(at(self.day(3), 8, 30)), 0)      # 24 hours after opening is 8:00, but sending starts at 9:00
        self.assertEqual(self.tick(at(self.day(3), 9)), 1)

    def test_an_envelope_sent_before_5_4_with_reminders_off_stays_off(self):
        env = self.send(settings={'reminder_every': 0})
        self.assertEqual(self.tick(at(self.day(9), 10)), 0)
        person = self.api.get(f"/api/reminders/envelopes/{env['id']}").json()['people'][0]
        self.assertEqual(person['state'], 'rule_off')

    def test_rule_chosen_for_the_envelope_and_company_default(self):
        self.add_rule('next-day', first_after=1)
        env = self.send(settings={'reminder_rule': 'next-day'})
        self.assertEqual(self.tick(at(self.day(1), 10)), 1)
        # a Super Admin makes Gentle (first after 5 working days) the default for Scribe Align: a new envelope follows it
        self.login(self.super)
        r = self.post('/api/reminders/defaults', {'defaults': {SCRIBE: 'gentle'}})
        self.assertEqual(r.status_code, 200, r.text)
        self.login(self.u1)
        self.clock.t = at(self.day(7), 10)
        env2 = self.send()
        view = self.api.get(f"/api/reminders/envelopes/{env2['id']}").json()
        self.assertEqual(view['rule']['id'], 'gentle')
        self.assertEqual(view['people'][0]['due'], at(self.day(14), 10))
        del env


# ====================================================================== the safety guards
class GuardTests(ReminderBase):
    def test_several_envelopes_to_one_person_go_in_one_email(self):
        a, b = self.send(), self.send()
        self.assertEqual(self.tick(at(self.day(2), 10)), 2)
        got = self.reminders_to()
        self.assertEqual(len(got), 1)
        text = got[0]['msg'].get_body(('plain',)).get_content()
        self.assertEqual(text.count('/reminders/stop/'), 2)
        for env in (a, b):
            self.assertEqual(self.recipient(env['recipients'][0]['id'])['reminders_sent'], 1)
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(t.outbox.c.kind).where(t.outbox.c.subject.like('Reminder:%'))).scalars().all(), ['reminder_digest'])

    def test_at_most_one_reminder_a_day_per_company_when_not_combined(self):
        self.add_rule('single', combine=False)
        a = self.send(settings={'reminder_rule': 'single'})
        b = self.send(settings={'reminder_rule': 'single'})
        self.assertEqual(self.tick(at(self.day(2), 10)), 1)
        self.assertEqual(self.tick(at(self.day(2), 16)), 0)          # the other one waits for tomorrow
        self.assertEqual(self.tick(at(self.day(3), 10, 1)), 1)
        self.assertEqual(sorted(self.recipient(e['recipients'][0]['id'])['reminders_sent'] for e in (a, b)), [1, 1])

    def test_nothing_new_while_an_earlier_reminder_waits_to_go_out(self):
        env = self.send()
        with db.engine.begin() as c:    # the mailbox is down for days
            c.execute(update(t.mailboxes).values(paused_until=at(self.day(30), 0)))
        self.assertEqual(self.tick(at(self.day(2), 10)), 1)
        for n in (7, 10, 15):
            self.assertEqual(self.tick(at(self.day(n), 10)), 0)
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(t.outbox.c.state).where(t.outbox.c.kind == 'reminder')).scalars().all(), ['queued'])
        self.assertEqual(self.recipient(env['recipients'][0]['id'])['reminders_sent'], 1)

    def test_never_to_an_address_that_bounced(self):
        env = self.send()
        with db.engine.begin() as c:
            c.execute(update(t.recipients).where(t.recipients.c.id == env['recipients'][0]['id']).values(delivery='bounced'))
        self.assertEqual(self.tick(at(self.day(2), 10)), 0)
        self.assertEqual(self.api.get(f"/api/reminders/envelopes/{env['id']}").json()['people'][0]['state'], 'undeliverable')

    def test_viewers_are_not_reminded_unless_the_rule_says_so(self):
        viewer = {'key': 'v', 'role': 'viewer', 'routing_order': 1, 'name': 'Vera Viewer', 'email': 'vera@client.test'}
        sent = self.send(people=lambda env: [dict(env['recipients'][0], name='Jane Example'), viewer])
        self.assertEqual(self.tick(at(self.day(2), 10)), 1)
        self.assertEqual(self.reminders_to('vera@client.test'), [])
        states = {p['email']: p['state'] for p in self.api.get(f"/api/reminders/envelopes/{sent['id']}").json()['people']}
        self.assertEqual(states['vera@client.test'], 'not_reminded')

    def test_pause_everything(self):
        self.send()
        self.login(self.super)
        self.assertEqual(self.post('/api/reminders/pause', {'until': at(self.day(9), 0)}).status_code, 200)
        self.assertEqual(self.tick(at(self.day(2), 10)), 0)
        self.assertEqual(self.tick(at(self.day(9), 10)), 1)        # resumes by itself


# ====================================================================== switches on one person or envelope
class SwitchTests(ReminderBase):
    def person(self, env, action, **extra):
        rid = env['recipients'][0]['id']
        return self.post(f'/api/reminders/recipients/{rid}', {'action': action} | extra)

    def test_switch_off_and_back_on(self):
        env = self.send()
        self.assertEqual(self.person(env, 'off').status_code, 200)
        self.assertEqual(self.tick(at(self.day(2), 10)), 0)
        r = self.person(env, 'remind_now')
        self.assertEqual(r.status_code, 400)
        self.assertIn('switched off', r.json()['error'])
        self.clock.t = at(self.day(3), 10)
        self.assertEqual(self.person(env, 'on').status_code, 200)
        # back on: the schedule restarts from that moment (3 working days), not at once
        self.assertEqual(self.tick(at(self.day(3), 11)), 0)
        self.assertEqual(self.tick(at(self.day(8), 10)), 1)

    def test_switched_off_when_sending(self):
        sent = self.send(people=lambda env: [dict(env['recipients'][0], name='Jane Example', reminders_off=True)])
        self.assertEqual(self.tick(at(self.day(2), 10)), 0)
        self.assertEqual(self.recipient(sent['recipients'][0]['id'])['reminders_off_source'], 'sender')

    def test_pause_until_a_date_and_skip_the_next(self):
        env = self.send()
        self.assertEqual(self.person(env, 'pause', until=at(self.day(8), 0)).status_code, 200)
        self.assertEqual(self.tick(at(self.day(2), 10)), 0)
        self.assertEqual(self.tick(at(self.day(8), 9)), 1)          # the morning the pause ends
        self.clock.t = at(self.day(8), 12)
        self.assertEqual(self.person(env, 'skip').status_code, 200)
        self.assertEqual(self.tick(at(self.day(11), 9)), 0)         # skipped (was due 3 working days after the 1st)
        self.assertEqual(self.tick(at(self.day(16), 9)), 1)         # 3 working days after the skipped one

    def test_pause_validation(self):
        env = self.send()
        self.assertEqual(self.person(env, 'pause', until=self.clock.t - 60).status_code, 400)
        self.assertEqual(self.person(env, 'explode').status_code, 400)

    def test_remind_now_at_most_once_a_day(self):
        env = self.send()
        r = self.person(env, 'remind_now')
        self.assertEqual(r.status_code, 400)                            # invited less than 24 hours ago
        self.clock.t = at(self.day(1), 11)
        self.assertEqual(self.person(env, 'remind_now').status_code, 200)
        self.drain()
        self.assertEqual(len(self.reminders_to()), 1)
        self.clock.t = at(self.day(1), 15)
        self.assertEqual(self.person(env, 'remind_now').status_code, 400)
        kinds = [h for h in self.api.get(f"/api/esign/envelopes/{env['id']}").json()['history'] if h['kind'] == 'reminder']
        self.assertTrue(kinds[0]['detail']['manual'])

    def test_envelope_rule_and_pause_from_the_card(self):
        env = self.send()
        r = self.post(f"/api/reminders/envelopes/{env['id']}", {'rule': 'off'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['people'][0]['state'], 'rule_off')
        self.assertEqual(self.post(f"/api/reminders/envelopes/{env['id']}", {'rule': 'no-such-rule'}).status_code, 400)
        r = self.post(f"/api/reminders/envelopes/{env['id']}", {'rule': '', 'paused_until': 'forever'})
        self.assertEqual(r.json()['people'][0]['state'], 'paused')
        self.assertEqual(self.tick(at(self.day(9), 10)), 0)
        self.post(f"/api/reminders/envelopes/{env['id']}", {'paused_until': None})
        self.assertEqual(self.tick(at(self.day(9), 11)), 1)

    def test_bulk_actions(self):
        a, b = self.send(), self.send()
        ids = [a['recipients'][0]['id'], b['recipients'][0]['id']]
        r = self.post('/api/reminders/bulk', {'recipient_ids': ids, 'action': 'off'})
        self.assertEqual(r.json()['done'], 2)
        self.assertEqual(self.post('/api/reminders/bulk', {'recipient_ids': ids, 'action': 'delete'}).status_code, 400)
        self.assertEqual(self.tick(at(self.day(2), 10)), 0)


# ====================================================================== the do-not-remind list
class OptoutTests(ReminderBase):
    def add_optout(self, **body):
        self.login(self.super)
        return self.post('/api/reminders/optouts', {'name': 'Jane Example', 'email': 'Jane@Client.test', 'reason': 'Asked by phone to stop reminders'} | body)

    def test_a_person_on_the_list_is_never_reminded(self):
        env = self.send()
        self.assertEqual(self.add_optout().status_code, 200)
        self.assertEqual(self.add_optout().status_code, 409)              # already on the list
        self.assertEqual(self.tick(at(self.day(2), 10)), 0)
        self.login(self.u1)
        r = self.post(f"/api/reminders/recipients/{env['recipients'][0]['id']}", {'action': 'remind_now'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('do-not-remind', r.json()['error'])
        held = self.api.get('/api/reminders/centre').json()['held']
        self.assertEqual([h['state'] for h in held], ['opted_out'])
        # removed from the list: reminders start again
        self.login(self.super)
        oid = self.api.get('/api/reminders/optouts').json()['optouts'][0]['id']
        self.assertEqual(self.post(f'/api/reminders/optouts/{oid}/remove').status_code, 200)
        self.assertEqual(self.tick(at(self.day(2), 11)), 1)

    def test_only_for_one_company_and_until_a_date(self):
        self.send()
        self.assertEqual(self.add_optout(entity=EHS).status_code, 200)         # another company only: Scribe Align still reminds
        self.assertEqual(self.tick(at(self.day(2), 10)), 1)
        self.send()
        r = self.add_optout(entity=SCRIBE, until=at(self.day(6), 0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.tick(at(self.day(5), 10)), 0)              # Saturday anyway, and on the list
        self.assertEqual(self.tick(at(self.day(7), 10)), 2)              # list entry ended: both envelopes, one email

    def test_reason_and_permissions(self):
        self.assertEqual(self.add_optout(reason='').status_code, 400)
        self.assertEqual(self.add_optout(email='not-an-email').status_code, 400)
        self.login(self.u1)
        self.assertEqual(self.post('/api/reminders/optouts', {'email': 'x@client.test', 'reason': 'Because'}).status_code, 403)
        self.assertEqual(self.api.get('/api/reminders/optouts').status_code, 403)
        self.login(self.admin)
        r = self.api.get('/api/reminders/optouts')
        self.assertEqual((r.status_code, r.json()['can_edit']), (200, False))

    def test_expiration_warning_respects_the_list(self):
        self.send(settings={'expire_days': 10, 'warn_days': 3})
        self.add_optout()
        self.clock.t = at(self.day(8), 10)
        esign.sweep()
        self.drain()
        self.assertFalse(any(m['msg']['Subject'].startswith('Expiring soon:') for m in self.smtp.to(JANE)))


# ====================================================================== the signer's stop link
class StopLinkTests(ReminderBase):
    def link(self):
        self.send()
        self.tick(at(self.day(2), 10))
        text = self.reminders_to()[0]['msg'].get_body(('plain',)).get_content()
        return re.search(r'https?://[^\s]+(/reminders/stop/[a-f0-9]{64})', text).group(1)

    def test_opening_the_link_changes_nothing_pressing_the_button_stops(self):
        path = self.link()
        page = self.api.get(path)
        self.assertEqual(page.status_code, 200)
        self.assertIn('<form method="post"', page.text)
        self.assertNotIn('<script', page.text)                       # strict content security policy
        self.assertNotIn('style=', page.text)
        rid = path.rsplit('/', 1)[1][:32]
        self.assertFalse(self.recipient(rid)['reminders_off'])       # a mail scanner opening the link stops nothing
        page = self.api.post(path)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Reminders stopped', page.text)
        r = self.recipient(rid)
        self.assertEqual((r['reminders_off'], r['reminders_off_source']), (True, 'signer'))
        self.drain()
        told = [m for m in self.smtp.to('sender@scribealign.test') if m['msg']['Subject'].startswith('Reminders stopped:')]
        self.assertEqual(len(told), 1)
        self.api.post(path)                                          # pressed twice: the sender is told once
        self.drain()
        self.assertEqual(len([m for m in self.smtp.to('sender@scribealign.test') if m['msg']['Subject'].startswith('Reminders stopped:')]), 1)
        self.assertEqual(self.tick(at(self.day(7), 10)), 0)

    def test_a_changed_or_made_up_link_does_nothing(self):
        path = self.link()
        token = path.rsplit('/', 1)[1]
        forged = token[:32] + ('0' if token[32] != '0' else '1') + token[33:]
        self.assertEqual(self.api.get('/reminders/stop/' + forged).status_code, 404)
        self.assertEqual(self.api.post('/reminders/stop/' + forged).status_code, 404)
        self.assertEqual(self.api.get('/reminders/stop/not-a-token').status_code, 404)
        self.assertFalse(self.recipient(token[:32])['reminders_off'])

    def test_no_stop_link_when_the_rule_does_not_allow_it(self):
        self.add_rule('no-stop', allow_signer_stop=False)
        self.send(settings={'reminder_rule': 'no-stop'})
        self.tick(at(self.day(2), 10))
        self.assertNotIn('/reminders/stop/', self.reminders_to()[0]['msg'].get_body(('plain',)).get_content())


# ====================================================================== rules, permissions and screens
class RuleAndScreenTests(ReminderBase):
    def test_only_the_super_admin_changes_rules(self):
        self.login(self.u1)
        self.assertEqual(self.api.get('/api/reminders/rules').status_code, 200)
        self.assertEqual(self.post('/api/reminders/rules', {'name': 'Mine'}).status_code, 403)
        self.assertEqual(self.post('/api/reminders/rules/standard', {'max_count': 20}).status_code, 403)
        self.assertEqual(self.post('/api/reminders/pause', {'until': 'forever'}).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.post('/api/reminders/defaults', {'defaults': {SCRIBE: 'firm'}}).status_code, 403)

    def test_rule_validation_and_guards(self):
        self.login(self.super)
        bad = [{'name': '', 'first_after': 2, 'every': 3, 'max_count': 4},
               {'name': 'X', 'first_after': 0, 'every': 3, 'max_count': 4},
               {'name': 'X', 'first_after': 2, 'every': 3, 'max_count': 99},
               {'name': 'X', 'first_after': 2, 'every': 3, 'max_count': 4, 'hour_from': 17, 'hour_to': 9},
               {'name': 'X', 'first_after': 2, 'every': 3, 'max_count': 4, 'weekdays': []}]
        for body in bad:
            self.assertEqual(self.post('/api/reminders/rules', body).status_code, 400, body)
        r = self.post('/api/reminders/rules', {'name': 'Twice a week', 'first_after': 2, 'every': 2, 'max_count': 3, 'weekdays': [1, 3], 'hour_from': 8, 'hour_to': 12})
        self.assertEqual(r.status_code, 200, r.text)
        rid = r.json()['id']
        self.assertEqual(self.post('/api/reminders/rules/standard', {'active': False}).status_code, 400)       # built-in
        self.assertEqual(self.post('/api/reminders/defaults', {'defaults': {SCRIBE: rid}}).status_code, 200)
        self.assertEqual(self.post(f'/api/reminders/rules/{rid}', {'active': False}).status_code, 400)          # a company default
        self.post('/api/reminders/defaults', {'defaults': {SCRIBE: 'standard'}})
        self.assertEqual(self.post(f'/api/reminders/rules/{rid}', {'active': False}).status_code, 200)
        self.assertEqual(self.post('/api/reminders/defaults', {'defaults': {SCRIBE: rid}}).status_code, 400)    # retired
        r = self.post('/api/reminders/rules/off', {'max_count': 5})
        self.assertEqual(next(x for x in r.json()['rules'] if x['id'] == 'off')['max_count'], 0)               # Off stays off

    def test_a_rule_change_applies_to_envelopes_already_out(self):
        env = self.send()
        self.login(self.super)
        self.post('/api/reminders/rules/standard', {'first_after': 5})
        self.login(self.u1)
        self.assertEqual(self.tick(at(self.day(2), 10)), 0)
        self.assertEqual(self.tick(at(self.day(7), 10)), 1)
        del env

    def test_people_see_their_own_envelopes_admins_their_team(self):
        env = self.send()
        rid = env['recipients'][0]['id']
        self.assertEqual(len(self.api.get('/api/reminders/centre').json()['upcoming']), 1)
        self.login(self.u2)                      # another sender in the same team
        self.assertEqual(self.api.get('/api/reminders/centre').json()['upcoming'], [])
        self.assertEqual(self.api.get(f"/api/reminders/envelopes/{env['id']}").status_code, 404)
        self.assertEqual(self.post(f'/api/reminders/recipients/{rid}', {'action': 'off'}).status_code, 404)
        self.login(self.ehs_user)
        self.assertEqual(self.api.get('/api/reminders/centre').json()['upcoming'], [])
        self.login(self.admin)
        self.assertEqual(len(self.api.get('/api/reminders/centre').json()['upcoming']), 1)
        self.login(self.super)
        self.assertEqual(len(self.api.get('/api/reminders/centre').json()['upcoming']), 1)

    def test_needs_a_look_mark_delivered_or_send_once_more(self):
        env = self.send()
        self.tick(at(self.day(2), 10))
        with db.engine.begin() as c:
            c.execute(update(t.outbox).where(t.outbox.c.kind == 'reminder').values(state='uncertain', handed_at=self.clock.t))
        d = self.api.get('/api/reminders/centre').json()
        self.assertEqual(d['counts']['attention'], 1)
        oid = d['attention'][0]['id']
        self.assertEqual(self.tick(at(self.day(7), 10)), 0)       # unconfirmed: nothing new piles up on top
        r = self.post(f'/api/reminders/mail/{oid}', {'action': 'send_again'})
        self.assertEqual(r.status_code, 200, r.text)
        self.drain()
        self.assertEqual(len(self.reminders_to()), 2)
        self.assertEqual(self.post(f'/api/reminders/mail/{oid}', {'action': 'delivered'}).status_code, 409)
        self.login(self.u2)
        self.assertEqual(self.post(f'/api/reminders/mail/{oid}', {'action': 'delivered'}).status_code, 404)
        del env

    def test_system_check_lists_unconfirmed_emails(self):
        with db.engine.begin() as c:
            items = reminders.health_items(c, time.time())
        self.assertEqual(items, [])
        self.connect_mailbox()
        with db.engine.begin() as c:
            oid = mailer.enqueue(c, SCRIBE, 'reminder', [JANE], 'Reminder: x', 'x', '<p>x</p>')
            c.execute(update(t.outbox).where(t.outbox.c.id == oid).values(state='uncertain'))
            items = reminders.health_items(c, time.time())
        self.assertEqual(len(items), 1)
        self.assertIn('#/reminders/attention', str(items))

    def test_final_notice_only_for_reminders_sent_by_this_release(self):
        env = self.send()
        with db.engine.begin() as c:       # an envelope that reached its last reminder before the update
            c.execute(update(t.recipients).where(t.recipients.c.id == env['recipients'][0]['id'])
                      .values(reminders_sent=4, last_reminder_at=at(self.day(0), 10)))
            reminders._put(c, reminders.SETTING_INSTALLED, at(self.day(1), 0))
        self.assertEqual(self.tick(at(self.day(9), 10)), 0)
        self.assertFalse([m for m in self.smtp.to('sender@scribealign.test') if m['msg']['Subject'].startswith('Not signed after reminders:')])
