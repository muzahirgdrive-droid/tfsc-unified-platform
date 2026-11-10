"""Regression tests for the November 2026 audit fixes (run on SQLite and on PostgreSQL)."""
import io
import smtplib
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database as db
import entities
import esign
import esign_db as t
import esign_pdf
import esign_worker
import health
import housekeeping
import mailer
import portal
import security as sec
import server as legacy
import static_assets
import worker
from PIL import Image
from sqlalchemy import select, update

import test_esign
from test_esign import SCRIBE

Base = test_esign.Base


class SignIn(Base):
    def test_correct_sign_ins_never_count_against_an_office_address(self):
        # FN-01: 60 correct sign-ins from one address all succeed
        for _ in range(60):
            self.login(self.u1)
        r = self.api.get('/api/auth/me')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['session']['idle_seconds'], sec.IDLE)

    def test_wrong_passwords_are_still_stopped_per_account_and_unknown_accounts_take_as_long(self):
        for _ in range(10):
            self.assertEqual(self.api.post('/api/auth/login', json={'email': self.u1['email'], 'password': 'wrong-password-123'}).status_code, 401)
        r = self.api.post('/api/auth/login', json={'email': self.u1['email'], 'password': self.password})
        self.assertEqual(r.status_code, 429)
        self.login(self.u2)      # another person at the same address is not affected
        with patch.object(sec, 'verify_absent', wraps=sec.verify_absent) as absent:   # SEC-06
            self.assertEqual(self.api.post('/api/auth/login', json={'email': 'nobody@example.test', 'password': 'x' * 14}).status_code, 401)
            self.assertEqual(absent.call_count, 1)

    def test_idle_sessions_end_and_activity_moves_the_limit(self):
        # FN-06 / SEC-04
        self.login(self.u1)
        token = sec.digest(self.api.cookies.get('tfsc_session'))
        with db.engine.begin() as c:
            first = c.execute(select(db.sessions.c.expires, db.sessions.c.created).where(db.sessions.c.token == token)).one()
            self.assertLess(first[0] - time.time(), sec.IDLE + 5)
            # ten minutes later a request moves the idle limit forward
            c.execute(update(db.sessions).where(db.sessions.c.token == token).values(expires=time.time() + sec.IDLE - 600))
        self.assertEqual(self.api.get('/api/auth/me').status_code, 200)
        with db.engine.connect() as c:
            self.assertGreater(c.execute(select(db.sessions.c.expires).where(db.sessions.c.token == token)).scalar(), time.time() + sec.IDLE - 30)
        # ... but never past the absolute limit
        with db.engine.begin() as c:
            c.execute(update(db.sessions).where(db.sessions.c.token == token).values(created=time.time() - sec.ABSOLUTE + 120, expires=time.time() + 120))
        self.api.get('/api/auth/me')
        with db.engine.connect() as c:
            self.assertLess(c.execute(select(db.sessions.c.expires).where(db.sessions.c.token == token)).scalar(), time.time() + 130)
        with db.engine.begin() as c:
            c.execute(update(db.sessions).where(db.sessions.c.token == token).values(expires=time.time() - 1))
        self.assertEqual(self.api.get('/api/auth/me').status_code, 401)

    def test_disabling_an_admin_asks_who_takes_over_and_nobody_is_locked_out(self):
        # FN-12
        self.login(self.super)
        r = self.post(f"/api/users/{self.admin['id']}", {'active': False})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()['needs'], 'reassign_to')
        self.assertEqual(len(r.json()['team']), 2)
        self.assertEqual(self.post(f"/api/users/{self.admin['id']}", {'active': False, 'reassign_to': ''}).status_code, 200)
        self.login(self.u1)
        self.assertEqual(self.api.get('/api/config').status_code, 200)

    def test_folder_chooser_never_lists_storage_in_hosted_mode(self):
        # SEC-01
        (db.OUTPUT / 'Other Company Clinic').mkdir(parents=True)
        self.login(self.u1)
        with patch.object(db, 'LOCAL', False):
            r = self.post('/api/folders', {'path': str(db.OUTPUT)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['entries'], [])

    def test_security_headers_and_cached_static_files(self):
        # SEC-07, PERF-07
        self.login(self.u1)
        page = self.api.get('/')
        self.assertIn('camera=()', page.headers['permissions-policy'])
        self.assertIn(f'/app.js?v={static_assets.version()}', page.text)
        fresh = self.api.get(f'/app.js?v={static_assets.version()}')
        self.assertIn('immutable', fresh.headers['cache-control'])
        self.api.cookies.clear()
        self.assertEqual(self.api.get('/app.js').headers['cache-control'], 'no-cache')
        missing = self.api.get('/no-such-page', headers={'Accept': 'text/html'})
        self.assertEqual(missing.status_code, 404)
        self.assertIn("couldn't find that page", missing.text)

    def test_busy_database_gets_a_short_answer(self):
        from sqlalchemy.exc import TimeoutError as PoolTimeout
        self.assertEqual(portal.busy_response(PoolTimeout('QueuePool limit')).status_code, 503)
        self.assertIsNone(portal.busy_response(RuntimeError('other')))


class Companies(Base):
    def test_registry_survives_a_failed_read_and_never_caches_it(self):
        # PERF-11 / FN-29
        good = dict(entities._load(force=True))
        self.assertIn(SCRIBE, good)
        with patch.object(entities, '_read', side_effect=RuntimeError('database away')):
            entities.invalidate()
            self.assertEqual(set(entities._load()), set(good))      # the last good copy
        entities.invalidate()
        self.assertEqual(set(entities._load()), set(good))

    def test_one_refresh_at_a_time(self):
        calls, gate = [], threading.Event()
        real = entities._read

        def slow():
            calls.append(1)
            gate.wait(2)
            return real()
        entities._load(force=True)
        entities.invalidate()
        with patch.object(entities, '_read', side_effect=slow):
            threads = [threading.Thread(target=entities._load) for _ in range(8)]
            for th in threads:
                th.start()
            time.sleep(0.2)
            gate.set()
            for th in threads:
                th.join()
        self.assertEqual(len(calls), 1)


class Envelopes(Base):
    def sent(self):
        self.connect_mailbox()
        contract_id, client_id = self.contract(self.u1)
        self.login(self.u1)
        env = self.post('/api/esign/envelopes', {'contract_id': contract_id}).json()
        self.post(f"/api/esign/envelopes/{env['id']}/draft", {'recipients': [dict(env['recipients'][0], name='Jane Example')],
                                                              'fields': [dict(f, recipient=f['recipient_id']) for f in env['fields']]})
        r = self.post(f"/api/esign/envelopes/{env['id']}/send")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['envelope'], client_id

    def test_a_stale_correction_is_refused_and_corrections_reach_the_activity_log(self):
        # FN-03, FN-09
        env, client_id = self.sent()
        jane = env['recipients'][0]
        r = self.post(f"/api/esign/envelopes/{env['id']}/correct", {'recipients': [{'id': jane['id'], 'email': 'new@client.test'}], 'version': env['version']})
        self.assertEqual(r.status_code, 200, r.text)
        stale = self.post(f"/api/esign/envelopes/{env['id']}/correct", {'recipients': [{'id': jane['id'], 'email': 'old@client.test'}], 'version': env['version']})
        self.assertEqual(stale.status_code, 409)
        self.assertIn('Someone else changed', stale.json()['error'])
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(t.recipients.c.email).where(t.recipients.c.id == jane['id'])).scalar(), 'new@client.test')
            logged = c.execute(select(db.audit.c.detail).where(db.audit.c.action == 'esign.corrected')).scalars().all()
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]['changes'][0]['email_to'], 'n***@client.test')

    def test_a_template_keeps_roles_not_people(self):
        # FN-10
        env, _ = self.sent()
        template = self.post(f"/api/esign/envelopes/{env['id']}/copy", {'template_name': 'Standard'}).json()
        self.assertTrue(all(not r['name'] and not r['email'] for r in template['recipients']))
        self.assertEqual([r['role'] for r in template['recipients']], [r['role'] for r in env['recipients'] if r['status'] != 'reassigned'])
        self.assertEqual(len(template['fields']), len(env['fields']))

    def test_history_is_sealed_and_a_changed_entry_is_found(self):
        # SEC-05
        env, _ = self.sent()
        self.assertGreater(esign.seal_backfill(), -1)
        detail = self.api.get(f"/api/esign/envelopes/{env['id']}").json()
        self.assertTrue(detail['chain_ok'])
        self.assertIn(detail['sealed_ok'], (True, None))
        with db.engine.connect() as c:
            rows = [dict(r) for r in c.execute(select(t.events).where(t.events.c.envelope_id == env['id'])).mappings()]
        self.assertTrue(all(r['seal'] == esign.seal_of(r) for r in rows))
        forged = dict(rows[0], hash='0' * 64)
        self.assertNotEqual(esign.seal_of(forged), rows[0]['seal'])
        self.assertIs(esign.verify_seals([dict(rows[0], seal='f' * 64)], None), False)

    def test_settings_and_dates(self):
        # FN-18, FN-19
        self.assertEqual(esign.settings_value({'expire_days': 7, 'warn_days': 30})['warn_days'], 6)
        self.assertIsNotNone(esign.check_text('02/30/2026', {'validation': 'date'}, 'Date'))
        self.assertIsNone(esign.check_text('02/28/2026', {'validation': 'date'}, 'Date'))

    def test_page_images_come_from_the_cache_without_reading_the_pdf(self):
        # PERF-13
        env, _ = self.sent()
        doc = env['documents'][0]
        first = self.api.get(f"/api/esign/documents/{doc['id']}/page/1.png")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue((db.DATA / 'esign-cache' / doc['id'] / '1.png').is_file())
        if doc['pages'] > 1:
            self.assertTrue((db.DATA / 'esign-cache' / doc['id'] / '2.png').is_file(), 'the next pages are drawn in the same run')
        with patch.object(esign_pdf, 'render_pages', side_effect=AssertionError('must not render again')):
            self.assertEqual(self.api.get(f"/api/esign/documents/{doc['id']}/page/1.png").content, first.content)

    def test_interrupted_completion_is_taken_over_and_failures_are_reported(self):
        # REL-12
        env, _ = self.sent()
        with db.engine.begin() as c:
            c.execute(update(t.envelopes).where(t.envelopes.c.id == env['id']).values(status='completing', completion_next=0, lease_until=time.time() + 60))
        self.assertEqual(esign.complete_due(), 0, 'held by another worker')
        with db.engine.begin() as c:
            c.execute(update(t.envelopes).where(t.envelopes.c.id == env['id']).values(lease_until=time.time() - 1))
        with patch.object(esign, 'finalize', side_effect=RuntimeError('boom')):
            self.assertEqual(esign.complete_due(), 0)
        with db.engine.connect() as c:
            row = c.execute(select(t.envelopes.c.completion_attempts, t.envelopes.c.completion_error, t.envelopes.c.lease_until).where(t.envelopes.c.id == env['id'])).one()
        self.assertEqual(row[0], 1)
        self.assertNotIn('boom', row[1])
        self.assertIsNone(row[2])


class MailFailures(Base):
    sent_envelope = test_esign.Mail.sent_envelope

    def test_daily_limit_pauses_the_mailbox_and_keeps_every_email(self):
        # REL-02
        def refuse(self_smtp, msg, to_addrs=None, **kw):
            raise smtplib.SMTPSenderRefused(550, b'5.4.5 Daily user sending limit exceeded', 'compliance@scribealign.test')
        with patch.object(smtplib.SMTP, 'send_message', refuse):
            env, row, box = self.sent_envelope()
        with db.engine.connect() as c:
            state = c.execute(select(t.outbox.c.state, t.outbox.c.attempts, t.outbox.c.last_error).where(t.outbox.c.id == row['id'])).one()
            pause = c.execute(select(t.mailboxes.c.pause_kind, t.mailboxes.c.paused_until)).one()
        self.assertEqual((state[0], state[1]), ('queued', 0))
        self.assertIn('daily sending limit', state[2])
        self.assertEqual(pause[0], 'daily')
        detail = self.api.get(f"/api/esign/envelopes/{env['id']}").json()
        self.assertEqual(detail['recipients'][0]['delivery'], 'pending')
        self.assertIn('daily sending limit', detail['recipients'][0]['delivery_error'])
        # the pause ends: the same email goes out
        with db.engine.begin() as c:
            c.execute(update(t.mailboxes).values(paused_until=time.time() - 1))
        self.drain()
        self.assertEqual(len(self.smtp.to('jane@client.test')), 1)
        with db.engine.connect() as c:
            self.assertIsNone(c.execute(select(t.mailboxes.c.paused_until)).scalar())

    def test_unreachable_server_pauses_only_that_mailbox(self):
        # REL-03
        self.assertFalse(mailer._quota(smtplib.SMTPDataError(552, b'5.3.4 Message size exceeds fixed limit')))
        self.assertTrue(mailer._quota(smtplib.SMTPDataError(421, b'4.7.0 Try again later')))
        self.assertTrue(mailer._connection_problem(TimeoutError()))
        self.assertFalse(mailer._connection_problem(smtplib.SMTPDataError(550, b'no')))
        with patch.object(mailer, '_smtp_session', side_effect=TimeoutError()):
            env, row, box = self.sent_envelope()
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(t.mailboxes.c.pause_kind)).scalar(), 'connect')
            self.assertEqual(c.execute(select(t.outbox.c.state, t.outbox.c.attempts).where(t.outbox.c.id == row['id'])).one(), ('queued', 0))

    def test_daily_cap(self):
        with patch.object(mailer, 'DAILY_CAP', 1):
            env, row, box = self.sent_envelope()
            self.post(f"/api/esign/envelopes/{env['id']}/resend")
            self.drain()
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(t.mailboxes.c.pause_kind)).scalar(), 'cap')
        self.assertEqual(len(self.smtp.to('jane@client.test')), 1)

    def test_a_reply_with_a_nul_character_is_stored_clean(self):
        # REL-01
        self.assertEqual(mailer.clean_text('Re: signed\x00copy\r\nnext', 300), 'Re: signed copy next')
        self.assertNotIn('\x00', mailer.clean_text('a\x00b\nc', 100, keep_newlines=True))

    def test_a_message_that_cannot_be_processed_is_set_aside_and_reading_goes_on(self):
        env, row, box = self.sent_envelope()
        with patch.object(mailer, 'process_inbound', side_effect=RuntimeError('unreadable')):
            mailer._quarantine(box, 1, 7, b'From: x@y.test\r\nSubject: odd\x00one\r\n\r\nbody', RuntimeError('unreadable'))
        with db.engine.connect() as c:
            kept = c.execute(select(t.inbound.c.kind, t.inbound.c.subject).where(t.inbound.c.uid == 7)).one()
            self.assertEqual(c.execute(select(t.mailboxes.c.imap_last_uid)).scalar(), 7)
        self.assertEqual(kept[0], 'quarantined')
        self.assertNotIn('\x00', kept[1])

    def test_private_mail_servers_are_refused_when_hosted(self):
        # SEC-10
        with patch.object(db, 'LOCAL', False):
            with self.assertRaises(ValueError):
                mailer.refuse_internal_host('127.0.0.1')
            with patch.dict('os.environ', {'TFSC_MAIL_PRIVATE_HOSTS': 'true'}):
                mailer.refuse_internal_host('127.0.0.1')


class Background(Base):
    def test_lanes_are_independent_and_report_a_heartbeat(self):
        # PERF-02 / REL-04
        ran = []
        tasks = (('a', 0, lambda: (_ for _ in ()).throw(RuntimeError('lane a broke'))), ('b', 0, lambda: ran.append('b') or 1))
        self.assertEqual(esign_worker.tick({}, tasks), 1)
        self.assertEqual(ran, ['b'])
        esign_worker.heartbeat('mail', {})
        with db.engine.connect() as c:
            items = {i['key']: i for i in health.check_workers(c, time.time())}
        self.assertEqual(items['worker:mail']['state'], 'ok')
        with db.engine.connect() as c:
            late = {i['key']: i for i in health.check_workers(c, time.time() + 600)}
        self.assertEqual(late['worker:mail']['state'], 'bad')

    def test_system_status_is_for_the_super_admin(self):
        self.login(self.u1)
        self.assertEqual(self.api.get('/api/system-status').status_code, 403)
        self.login(self.super)
        r = self.api.get('/api/system-status?fresh=1')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('database', [i['key'] for i in r.json()['items']])
        self.assertIn(self.api.get('/api/health/deep').status_code, (200, 503))

    def test_scanner_signature_age(self):
        now = time.time()
        version = 'ClamAV 1.4.3/27800/' + time.strftime('%a %b %d %H:%M:%S %Y', time.gmtime(now - 10 * 86400))
        self.assertAlmostEqual(health.signature_age(version, now), 10, delta=0.1)
        with patch.dict('os.environ', {'TFSC_CLAMAV': 'clamav:3310'}), patch.object(health, 'clamav', return_value=version):
            self.assertEqual(health.check_scanner(now)[0]['state'], 'warn')
        with patch.dict('os.environ', {'TFSC_CLAMAV': 'clamav:3310'}), patch.object(health, 'clamav', side_effect=OSError()):
            self.assertEqual(health.check_scanner(now)[0]['state'], 'bad')

    def test_backup_report(self):
        now = time.time()
        with db.engine.begin() as c:
            c.execute(db.settings.insert().values(key='ops:backups', value={'at': now, 'offsite': False, 'last_backup': {'at': now - 3600, 'ok': True},
                                                                         'last_restore_test': {'at': now - 3000, 'ok': False}, 'archiving': {'failing': True, 'waiting_mb': 120}}))
        with db.engine.connect() as c:
            items = {i['key']: i['state'] for i in health.check_backups(c, now)}
        self.assertEqual(items, {'backup': 'bad', 'backup:archive': 'bad', 'backup:offsite': 'warn'})

    def test_an_interrupted_generation_is_started_again(self):
        # REL-08
        now = time.time()
        with db.engine.begin() as c:
            c.execute(db.clients.insert().values(id='c' * 32, owner_id=self.u1['id'], name='Clinic', name_key='k', address='', draft={}, updated=now))
            c.execute(db.jobs.insert().values(id='j' * 32, owner_id=self.u1['id'], client_id='c' * 32, key='k' * 20, state='running', payload={'entity': SCRIBE},
                                              created=now, updated=now, lease='dead', lease_until=now - 5, attempts=1))
        job = worker.claim()
        self.assertEqual((job['id'], job['attempts']), ('j' * 32, 2))
        self.assertNotIn('soffice', worker.friendly(__import__('subprocess').TimeoutExpired(['soffice', '/tmp/x'], 1)))

    def test_tidy_up_removes_only_leftovers(self):
        # REL-20, REL-21
        db.OUTPUT.mkdir(parents=True, exist_ok=True)
        old, new = db.OUTPUT / '.tfsc-old', db.OUTPUT / '.tfsc-new'
        old.mkdir()
        new.mkdir()
        keep = db.OUTPUT / 'Clinic'
        keep.mkdir()
        import os
        os.utime(old, (time.time() - 3 * 86400,) * 2)
        os.utime(keep, (time.time() - 30 * 86400,) * 2)
        housekeeping.leftovers()
        self.assertEqual((old.exists(), new.exists(), keep.exists()), (False, True, True))
        with db.engine.begin() as c:
            c.execute(db.throttle.insert().values(key='x', count=3, until=time.time() - 7200))
        self.assertGreaterEqual(housekeeping.database_rows(), 1)


class Uploads(Base):
    def test_image_size_is_checked_before_the_pixels_are_decoded(self):
        # SEC-02
        big = io.BytesIO()
        Image.new('L', (7000, 7000)).save(big, format='PNG')
        with patch('PIL.ImageFile.ImageFile.load', side_effect=AssertionError('decoded before the size check')):
            with self.assertRaises(ValueError):
                esign_pdf.image_to_pdf(big.getvalue())

    def test_owner_password_only_pdf_is_accepted_and_crop_box_becomes_the_page(self):
        # FN-24, FN-25
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import ArrayObject, FloatObject, NameObject
        from esign_support import agreement_pdf
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(agreement_pdf())))
        writer.pages[0][NameObject('/CropBox')] = ArrayObject([FloatObject(v) for v in (36, 36, 500, 700)])
        writer.encrypt(user_password='', owner_password='owner-only')
        out = io.BytesIO()
        writer.write(out)
        pdf, _ = esign_pdf.to_pdf('Agreement.pdf', out.getvalue())
        reader = PdfReader(io.BytesIO(pdf))
        self.assertFalse(reader.is_encrypted)
        self.assertEqual([round(float(v)) for v in reader.pages[0].mediabox], [36, 36, 500, 700])
        plain = agreement_pdf()
        self.assertEqual(esign_pdf.normalize(plain), plain, 'an ordinary PDF is stored byte for byte')


class Builder(Base):
    def test_names_are_kept_as_typed_and_numbers_are_checked(self):
        # FN-08, FN-21
        self.assertEqual(legacy.clean('Smith–Jones Clinic — East', 'Client', True, 200, typography=False), 'Smith–Jones Clinic — East')
        self.assertEqual(legacy.clean('a--b', 'Terms', True, 200), 'a, b')
        with self.assertRaises(ValueError):
            legacy.whole('30.9', 'notice days', 30, 1, 180)
        with self.assertRaises(ValueError):
            legacy.date_value('0001-01-01', 'Effective date')
        try:
            legacy.number('5000000000', 'Rate')
        except ValueError as exc:
            self.assertIn('1,000,000,000', str(exc))

    def test_a_limited_user_cannot_add_a_custom_service(self):
        # FN-14
        limited = dict(self.u1, services=['some-service'], custom_services=False)
        with self.assertRaises(Exception) as raised:
            portal.check_services(limited, {'services': [], 'custom_services': [{'id': 'custom-1', 'name': 'Anything'}]})
        self.assertEqual(raised.exception.status_code, 403)
        portal.check_services(dict(limited, custom_services=True), {'services': [], 'custom_services': [{'id': 'custom-1'}]})
        portal.check_services(dict(self.u1, services=None), {'services': [{'id': 'x'}], 'custom_services': [{'id': 'c'}]})

    def test_bulk_first_name_skips_titles_and_client_names_match(self):
        # FN-16, FN-02
        import clients
        import esign_bulk
        self.assertEqual(esign_bulk._fill('Hello {{first_name}}', {'name': 'Dr. Laura Bennett, MD', 'client': ''}), 'Hello Laura')
        with db.engine.begin() as c:
            c.execute(db.clients.insert().values(id='a' * 32, owner_id=self.u1['id'], name='Hill Country Allergy & Asthma, PLLC',
                                                 name_key=clients.name_key('Hill Country Allergy & Asthma, PLLC'), address='', draft={}, updated=time.time(), client_number='CL-0007'))
        self.login(self.u1)
        r = self.post('/api/esign/bulk/check', {'clients': ['Hill Country Allergy & Asthma, PLLC', 'Hill Country Allergy & Asthma', 'Brand New Clinic']}).json()['clients']
        self.assertEqual(r['Hill Country Allergy & Asthma, PLLC']['match']['client_number'], 'CL-0007')
        self.assertIsNone(r['Hill Country Allergy & Asthma']['match'])
        self.assertEqual(r['Hill Country Allergy & Asthma']['similar'][0]['client_number'], 'CL-0007')
        self.assertEqual(r['Brand New Clinic'], {'match': None, 'similar': []})
