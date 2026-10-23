"""Company compliance mailboxes: settings, outgoing mail (transactional outbox + SMTP) and incoming mail (IMAP).

Every email is written to `mail_outbox` in the same transaction as the change that causes it, then sent by the worker,
so a rolled-back change sends nothing and a crash never loses a queued email. Each company sends through its own
mailbox (for example compliance@scribealign.com on Google Workspace): with an app password, or with a Google service
account that has domain-wide delegation (XOAUTH2). Replies from clients and bounce notices are read back from that
mailbox's inbox over IMAP (read-only; nothing is marked, moved or deleted) and attached to the envelope.

Incoming email is data. Nothing in a message is ever treated as an instruction.
"""
from __future__ import annotations

import base64
import datetime as dt
import email
import email.policy
import functools
import hashlib
import html
import imaplib
import io
import ipaddress
import json
import logging
import os
import re
import smtplib
import socket
import ssl
import struct
import threading
import time
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, getaddresses, parseaddr

from sqlalchemy import or_, select, update

import database as db
import secretbox
from entities import ENTITIES, get_entity
from esign_db import envelopes, inbound, inbound_files, mailboxes, outbox, recipients

log = logging.getLogger('tfsc.mail')
EMAIL_RE = re.compile(r'[^\s@<>"\',;]+@[^\s@<>"\',;]+\.[A-Za-z0-9-]{2,}')
OUR_ID = re.compile(r'<(tfsc\.([0-9a-f]{32})\.[0-9a-z]{6,})@[^>\s]+>')
GOOGLE = {'smtp_host': 'smtp.gmail.com', 'smtp_port': 587, 'smtp_security': 'starttls', 'imap_host': 'imap.gmail.com', 'imap_port': 993, 'imap_security': 'ssl'}
NOTIFY_EVENTS = ('viewed', 'signed', 'completed', 'declined', 'bounced', 'replied', 'voided', 'expired', 'auth_failed', 'reassigned')
DEFAULT_NOTIFY = list(NOTIFY_EVENTS)
DEFAULT_ADDRESSES = {'SCRIBE ALIGN LLC': 'compliance@scribealign.com', 'EXTENDED HEALTH SERVICES INC.': 'compliance@extendedhealthservices.us',
                     '24-7 CONSULTANCY LLC': 'compliance@24-7consultancyllc.com'}
MAX_ATTEMPTS = 10
ATTACHMENT_LIMIT = 18_000_000


class MailboxNotReady(Exception):
    pass


def valid_email(value: str) -> str:
    value = (value or '').strip()
    if len(value) > 200 or not EMAIL_RE.fullmatch(value):
        raise ValueError(f'"{value[:60]}" is not a valid email address.')
    return value


def origin() -> str:
    return os.getenv('TFSC_ORIGIN', 'http://127.0.0.1:8765').rstrip('/')


# ------------------------------------------------------------------ mailbox settings
def defaults(entity_name: str) -> dict:
    entity = get_entity(entity_name)
    address = DEFAULT_ADDRESSES.get(entity_name, '')
    return dict(entity=entity_name, address=address, display_name=f"{entity['short']} Compliance", mode='off', auth='password', login=address,
                secret=None, sa_key=None, imap_enabled=True, signer_email='', notify=DEFAULT_NOTIFY, imap_uidvalidity=None, imap_last_uid=None,
                imap_last_poll=None, imap_error=None, imap_lease=None, smtp_error=None, last_test={}, updated=0.0, updated_by=None, **GOOGLE)


def load(c, entity_name: str) -> dict:
    row = c.execute(select(mailboxes).where(mailboxes.c.entity == entity_name)).mappings().first()
    return dict(row) if row else defaults(entity_name)


def public(box: dict, full: bool) -> dict:
    """What the settings screen may show. Secrets never leave the server."""
    out = {k: box[k] for k in ('entity', 'address', 'display_name', 'mode', 'imap_enabled', 'imap_last_poll', 'imap_error', 'smtp_error', 'last_test', 'updated')}
    out['index'] = list(ENTITIES).index(box['entity'])
    out['short'] = get_entity(box['entity'])['short']
    out['ready'] = ready(box)
    if full:
        out.update({k: box[k] for k in ('auth', 'login', 'smtp_host', 'smtp_port', 'smtp_security', 'imap_host', 'imap_port', 'imap_security', 'signer_email', 'notify')})
        out['has_secret'] = bool(box.get('secret'))
        out['has_sa_key'] = bool(box.get('sa_key'))
        out['sa_client_email'] = ''
        if box.get('sa_key'):
            try:
                out['sa_client_email'] = json.loads(secretbox.open_(box['sa_key'], 'sa:' + box['entity'])).get('client_email', '')
            except ValueError:
                out['sa_client_email'] = '(stored key cannot be read; upload it again)'
        out['relay_available'] = bool(os.getenv('SMTP_HOST'))
        out['secrets_available'] = secretbox.available()
    return out


def ready(box: dict) -> bool:
    if box['mode'] == 'relay':
        return bool(os.getenv('SMTP_HOST')) and bool(box['address'])
    if box['mode'] == 'mailbox':
        return bool(box['address']) and bool(box['secret'] if box['auth'] == 'password' else box['sa_key'])
    return False


def _plain_host_allowed(host: str) -> bool:
    """Unencrypted connections only to servers on this machine or a private network (test servers)."""
    if host in ('localhost',) or '.' not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or address.is_private
    except ValueError:
        return False


def save(c, actor: dict, entity_name: str, payload: dict) -> dict:
    get_entity(entity_name)
    box = load(c, entity_name)
    text = lambda key, limit: str(payload.get(key, box[key]) or '').strip()[:limit]  # noqa: E731
    box['mode'] = payload.get('mode', box['mode'])
    if box['mode'] not in ('mailbox', 'relay', 'off'):
        raise ValueError('Choose how this company sends email.')
    previous = dict(box)
    box['address'] = valid_email(text('address', 200)) if text('address', 200) else ''
    box['display_name'] = text('display_name', 150) or f"{get_entity(entity_name)['short']} Compliance"
    if re.search(r'[\r\n<>"]', box['display_name']):
        raise ValueError('The sender name cannot contain line breaks, quotes or angle brackets.')
    box['auth'] = payload.get('auth', box['auth'])
    if box['auth'] not in ('password', 'google_sa'):
        raise ValueError('Choose app password or Google service account.')
    # The sign-in name follows the address unless it was set to something else on purpose.
    login = str(payload.get('login') or '').strip()[:200]
    box['login'] = box['address'] if not login or (login == previous['login'] and previous['login'] == previous['address']) else login
    for key in ('smtp_host', 'imap_host'):
        box[key] = text(key, 200)
        if box[key] and not re.fullmatch(r'[A-Za-z0-9.-]{1,200}', box[key]):
            raise ValueError('Enter a valid server name.')
    for key in ('smtp_port', 'imap_port'):
        try:
            box[key] = int(payload.get(key, box[key]))
        except (TypeError, ValueError):
            raise ValueError('Enter a valid port number.') from None
        if not 1 <= box[key] <= 65535:
            raise ValueError('Enter a valid port number.')
    box['smtp_security'] = payload.get('smtp_security', box['smtp_security'])
    box['imap_security'] = payload.get('imap_security', box['imap_security'])
    if box['smtp_security'] not in ('starttls', 'ssl', 'none') or box['imap_security'] not in ('ssl', 'starttls', 'none'):
        raise ValueError('Choose the connection security.')
    for key, security in (('smtp_host', 'smtp_security'), ('imap_host', 'imap_security')):
        if box[security] == 'none' and box[key] and not _plain_host_allowed(box[key]):
            raise ValueError('Unencrypted connections are allowed only to a test server on this machine or private network.')
    box['imap_enabled'] = bool(payload.get('imap_enabled', box['imap_enabled']))
    signer = text('signer_email', 200)
    box['signer_email'] = valid_email(signer) if signer else ''
    notify = payload.get('notify', box['notify'])
    if not isinstance(notify, list) or any(x not in NOTIFY_EVENTS for x in notify):
        raise ValueError('Choose valid notification events.')
    box['notify'] = list(dict.fromkeys(notify))
    if payload.get('secret'):
        secret = str(payload['secret']).replace(' ', '') if box['smtp_host'].endswith('gmail.com') else str(payload['secret'])
        if len(secret) > 500:
            raise ValueError('The password is too long.')
        box['secret'] = secretbox.seal(secret, 'smtp:' + entity_name)
    if payload.get('sa_key'):
        key = _validate_sa(payload['sa_key'])
        box['sa_key'] = secretbox.seal(json.dumps(key), 'sa:' + entity_name)
    if payload.get('clear_secret'):
        box['secret'] = None
    if box['mode'] == 'mailbox':
        if not box['address'] or not box['smtp_host']:
            raise ValueError('Enter the mailbox address and its SMTP server.')
        if box['auth'] == 'password' and not box['secret']:
            raise ValueError('Enter the app password for this mailbox.')
        if box['auth'] == 'google_sa' and not box['sa_key']:
            raise ValueError('Upload the Google service account key (JSON).')
    if box['mode'] == 'relay' and not box['address']:
        raise ValueError('Enter the address that receives replies.')
    if box['address'] != previous['address'] or box['login'] != previous['login'] or not box['imap_enabled'] or box['mode'] != previous['mode']:
        box['imap_uidvalidity'] = box['imap_last_uid'] = box['imap_last_poll'] = None
    box.update(updated=time.time(), updated_by=actor['id'], smtp_error=None, imap_error=None)
    values = {k: v for k, v in box.items() if k in mailboxes.c}
    if c.execute(select(mailboxes.c.entity).where(mailboxes.c.entity == entity_name)).first():
        c.execute(update(mailboxes).where(mailboxes.c.entity == entity_name).values(**values))
    else:
        c.execute(mailboxes.insert().values(**values))
    db.log(c, actor, 'mailbox.updated', detail={'entity': entity_name, 'mode': box['mode'], 'address': box['address'],
                                                 'credentials_changed': bool(payload.get('secret') or payload.get('sa_key'))})
    return box


def _validate_sa(raw) -> dict:
    try:
        key = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        raise ValueError('The service account key is not valid JSON.') from None
    if not isinstance(key, dict) or key.get('type') != 'service_account' or not key.get('client_email') or 'PRIVATE KEY' not in str(key.get('private_key', '')):
        raise ValueError('Upload the JSON key of a Google service account.')
    if key.get('token_uri', 'https://oauth2.googleapis.com/token') != 'https://oauth2.googleapis.com/token':
        raise ValueError('The service account key must use Google\'s token endpoint.')
    return {k: key[k] for k in ('type', 'client_email', 'private_key', 'private_key_id', 'client_id') if k in key}


# ------------------------------------------------------------------ Google service account (XOAUTH2)
_tokens: dict[tuple[str, str], tuple[str, float]] = {}
_token_lock = threading.Lock()


def google_token(sa: dict, user: str) -> str:
    """Access token for `user` via domain-wide delegation (JWT bearer grant, scope https://mail.google.com/)."""
    import httpx
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    cache_key = (sa['client_email'], user)
    with _token_lock:
        token = _tokens.get(cache_key)
        if token and token[1] > time.time() + 120:
            return token[0]
    now = int(time.time())
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b'=')  # noqa: E731
    header = {'alg': 'RS256', 'typ': 'JWT'}
    if sa.get('private_key_id'):
        header['kid'] = sa['private_key_id']
    claims = {'iss': sa['client_email'], 'sub': user, 'scope': 'https://mail.google.com/', 'aud': 'https://oauth2.googleapis.com/token', 'iat': now, 'exp': now + 3600}
    signing_input = b64(json.dumps(header, separators=(',', ':')).encode()) + b'.' + b64(json.dumps(claims, separators=(',', ':')).encode())
    key = serialization.load_pem_private_key(sa['private_key'].encode(), password=None)
    assertion = signing_input + b'.' + b64(key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256()))
    response = httpx.post('https://oauth2.googleapis.com/token', timeout=20,
                          data={'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': assertion.decode()})
    if response.status_code != 200:
        detail = response.json().get('error_description', '') if response.headers.get('content-type', '').startswith('application/json') else ''
        raise MailboxNotReady(f'Google refused the service account sign-in ({response.status_code}). {detail} '
                              'Check domain-wide delegation for scope https://mail.google.com/ in the Google Admin console.')
    data = response.json()
    with _token_lock:
        _tokens[cache_key] = (data['access_token'], time.time() + int(data.get('expires_in', 3600)))
    return data['access_token']


def _xoauth2(user: str, token: str) -> str:
    return f'user={user}\x01auth=Bearer {token}\x01\x01'


# ------------------------------------------------------------------ sending
def transport(c, entity_name: str) -> dict:
    box = load(c, entity_name)
    if box['mode'] == 'off' or not ready(box):
        raise MailboxNotReady(f"The {get_entity(entity_name)['short']} mailbox is not connected. A Super Admin can connect it under Company mailboxes.")
    return box


def _context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    bundle = os.getenv('TFSC_MAIL_CA_BUNDLE')
    if bundle:
        context.load_verify_locations(bundle)
    return context


def _smtp_session(box: dict):
    if box['mode'] == 'relay':
        host, port = os.getenv('SMTP_HOST', ''), int(os.getenv('SMTP_PORT', '587'))
        security = 'ssl' if os.getenv('SMTP_SSL', 'false') == 'true' else 'starttls' if os.getenv('SMTP_STARTTLS', 'false') == 'true' else 'none'
        user, password = os.getenv('SMTP_USER', ''), os.getenv('SMTP_PASSWORD', '')
        auth = 'password' if os.getenv('SMTP_AUTH', 'false') == 'true' and user else 'none'
    else:
        host, port, security = box['smtp_host'], box['smtp_port'], box['smtp_security']
        user, auth = box['login'] or box['address'], box['auth']
        password = secretbox.open_(box['secret'], 'smtp:' + box['entity']) if auth == 'password' else ''
    if security == 'ssl':
        smtp = smtplib.SMTP_SSL(host, port, timeout=30, context=_context())
    else:
        smtp = smtplib.SMTP(host, port, timeout=30)
        if security == 'starttls':
            smtp.starttls(context=_context())
    try:
        smtp.ehlo()
        if auth == 'password':
            smtp.login(user, password)
        elif auth == 'google_sa':
            sa = json.loads(secretbox.open_(box['sa_key'], 'sa:' + box['entity']))
            token = google_token(sa, user)
            smtp.auth('XOAUTH2', lambda challenge=None: _xoauth2(user, token), initial_response_ok=True)
    except Exception:
        smtp.close()
        raise
    return smtp


def sender_addresses(box: dict) -> tuple[str, str]:
    """(From, Reply-To). Relay mode cannot send as the company domain (SPF/DMARC), so it uses the platform sender."""
    if box['mode'] == 'relay':
        return formataddr((box['display_name'], os.getenv('SMTP_FROM', 'no-reply@localhost'))), box['address']
    return formataddr((box['display_name'], box['address'])), box['address']


def build_message(box: dict, row: dict, attachments: list[tuple[str, bytes]]) -> EmailMessage:
    msg = EmailMessage(policy=email.policy.SMTP)
    sender, reply_to = sender_addresses(box)
    msg['From'] = sender
    msg['Reply-To'] = reply_to
    msg['To'] = ', '.join(row['to_addrs'])
    if row['cc_addrs']:
        msg['Cc'] = ', '.join(row['cc_addrs'])
    msg['Subject'] = re.sub(r'[\r\n]+', ' ', row['subject'])[:300]
    msg['Message-ID'] = row['message_id']
    msg['Date'] = format_datetime(dt.datetime.now(dt.UTC))
    msg['Auto-Submitted'] = 'auto-generated'
    msg['X-TFSC-Mail'] = row['id']
    if row.get('envelope_ref'):
        msg['X-TFSC-Envelope'] = row['envelope_ref']
    msg.set_content(row['text'])
    msg.add_alternative(row['html'], subtype='html')
    if 'cid:' in row['html'] and box.get('entity') in ENTITIES:
        html_part = msg.get_payload()[-1]
        for cid, image in inline_images(box['entity']):
            if f'cid:{cid}' in row['html']:
                html_part.add_related(image, maintype='image', subtype='png', cid=f'<{cid}>', disposition='inline', filename=cid.split('@')[0] + '.png')
    for name, body in attachments:
        msg.add_attachment(body, maintype='application', subtype='pdf', filename=name)
    return msg


def deliver(box: dict, msg: EmailMessage, rcpts: list[str]) -> None:
    smtp = _smtp_session(box)
    try:
        refused = smtp.send_message(msg, to_addrs=rcpts)
        if refused and len(refused) == len(rcpts):
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        try:
            smtp.quit()
        except Exception:  # noqa: BLE001
            smtp.close()


def message_id(box_address: str, outbox_id: str) -> str:
    domain = (box_address.rsplit('@', 1)[-1] if box_address else '') or re.sub(r':\d+$', '', origin().split('//', 1)[-1]) or 'localhost'
    return f'<tfsc.{outbox_id}.{base64.b32encode(os.urandom(5)).decode().lower()}@{domain}>'


def enqueue(c, entity_name: str, kind: str, to: list[str], subject: str, text: str, html_body: str, *, envelope_id=None, recipient_id=None,
            cc=(), bcc=(), attachments=()) -> str:
    """Queue an email in the caller's transaction. Addresses are de-duplicated (To wins over Cc wins over Bcc)."""
    to = list(dict.fromkeys(a.strip() for a in to if a and a.strip()))
    seen = {a.casefold() for a in to}
    cc_list = [a for a in dict.fromkeys(cc) if a and a.casefold() not in seen]
    seen |= {a.casefold() for a in cc_list}
    bcc_list = [a for a in dict.fromkeys(bcc) if a and a.casefold() not in seen]
    if not to:
        to, bcc_list = bcc_list[:1], bcc_list[1:]
    if not to:
        return ''
    box = load(c, entity_name)
    oid = db.uid()
    now = time.time()
    c.execute(outbox.insert().values(id=oid, entity=entity_name, envelope_id=envelope_id, recipient_id=recipient_id, kind=kind, to_addrs=to, cc_addrs=cc_list,
                                     bcc_addrs=bcc_list, subject=subject[:300], text=text, html=html_body, attachments=list(attachments),
                                     message_id=message_id(box['address'], oid), state='queued', attempts=0, next_attempt=now, created=now))
    wake.set()
    return oid


def _permanent(exc: Exception) -> bool:
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return all(code >= 500 for code, _ in exc.recipients.values())
    if isinstance(exc, smtplib.SMTPResponseException):
        return exc.smtp_code >= 500 and not isinstance(exc, smtplib.SMTPAuthenticationError)
    return False


def _describe(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return 'The mail server rejected the sign-in. Check the app password (Google: 16-character app password with 2-Step Verification on).'
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return 'The mail server refused the recipient: ' + '; '.join(f'{k} ({v[0]} {v[1].decode(errors="replace")[:120]})' for k, v in exc.recipients.items())
    if isinstance(exc, imaplib.IMAP4.error):
        detail = str(exc)
        detail = detail[2:-1] if detail.startswith(("b'", 'b"')) else detail
        return 'The mail server refused the request: ' + detail[:300]
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return 'The mail server did not respond in time.'
    if isinstance(exc, (OSError, smtplib.SMTPException, MailboxNotReady, ValueError)):
        return str(exc)[:500] or exc.__class__.__name__
    return exc.__class__.__name__


def attachment_bytes(c, refs: list[dict]) -> list[tuple[str, bytes]]:
    from esign_db import documents
    out = []
    for ref in refs:
        if ref['kind'] == 'final':
            row = c.execute(select(documents.c.name, documents.c.final_pdf).where(documents.c.id == ref['id'])).first()
            if row and row[1]:
                out.append((ref.get('name') or row[0], bytes(row[1])))
        elif ref['kind'] == 'certificate':
            row = c.execute(select(envelopes.c.ref, envelopes.c.certificate).where(envelopes.c.id == ref['id'])).first()
            if row and row[1]:
                out.append((f'Certificate of Completion {row[0]}.pdf', bytes(row[1])))
    return out


def dispatch_once(limit: int = 20) -> int:
    """Send due emails. Returns how many were attempted."""
    now = time.time()
    with db.engine.begin() as c:
        c.execute(update(outbox).where(outbox.c.state == 'sending', outbox.c.lease_until < now).values(state='queued'))
        q = select(outbox).where(outbox.c.state == 'queued', outbox.c.next_attempt <= now).order_by(outbox.c.next_attempt).limit(limit)
        if db.engine.dialect.name == 'postgresql':
            q = q.with_for_update(skip_locked=True)
        rows = [dict(r) for r in c.execute(q).mappings()]
        for r in rows:
            c.execute(update(outbox).where(outbox.c.id == r['id']).values(state='sending', lease_until=now + 300))
    for r in rows:
        _send_one(r)
    return len(rows)


def _send_one(r: dict) -> None:
    try:
        with db.engine.connect() as c:
            box = transport(c, r['entity'])
            files = attachment_bytes(c, r['attachments'])
            ref = c.execute(select(envelopes.c.ref).where(envelopes.c.id == r['envelope_id'])).scalar() if r['envelope_id'] else None
        msg = build_message(box, dict(r, envelope_ref=ref), files)
        deliver(box, msg, r['to_addrs'] + r['cc_addrs'] + r['bcc_addrs'])
    except Exception as exc:  # noqa: BLE001
        attempts = r['attempts'] + 1
        final = _permanent(exc) or attempts >= MAX_ATTEMPTS
        reason = _describe(exc)
        log.warning('email %s (%s) failed: %s', r['id'], r['kind'], reason)
        with db.engine.begin() as c:
            c.execute(update(outbox).where(outbox.c.id == r['id']).values(
                state='failed' if final else 'queued', attempts=attempts, last_error=reason[:2000], lease_until=None,
                next_attempt=time.time() + min(6 * 3600, 60 * 2 ** attempts)))
            if not isinstance(exc, smtplib.SMTPRecipientsRefused):
                c.execute(update(mailboxes).where(mailboxes.c.entity == r['entity']).values(smtp_error=reason[:1000]))
            if final and r['recipient_id']:
                c.execute(update(recipients).where(recipients.c.id == r['recipient_id']).values(delivery='failed', delivery_error=reason[:1000]))
                import esign
                esign.mail_failed(c, r, reason)
        return
    link = re.compile(r'/sign/[A-Za-z0-9_-]{30,60}')
    with db.engine.begin() as c:
        # The stored copy keeps no working signing link once the email is out.
        c.execute(update(outbox).where(outbox.c.id == r['id']).values(state='sent', attempts=r['attempts'] + 1, sent_at=time.time(), lease_until=None, last_error=None,
                                                                      text=link.sub('/sign/[link removed after sending]', r['text']),
                                                                      html=link.sub('/sign/[link removed after sending]', r['html'])))
        c.execute(update(mailboxes).where(mailboxes.c.entity == r['entity']).values(smtp_error=None))
        if r['recipient_id']:
            c.execute(update(recipients).where(recipients.c.id == r['recipient_id'], recipients.c.delivery.in_(['pending', 'failed'])).values(delivery='sent', delivery_error=None))


def send_test(c, entity_name: str, to: str) -> None:
    """Send a test email right away (not queued), so the Super Admin sees the real result."""
    box = transport(c, entity_name)
    entity = get_entity(entity_name)
    row = {'id': db.uid(), 'to_addrs': [valid_email(to)], 'cc_addrs': [], 'subject': f"Test email from {box['display_name']}",
           'text': f"This is a test email from TFSC Agreements for {entity['name'].rstrip('.')}.\n\nIf you can read this, sending from {box['address']} works.",
           'html': layout(entity, 'Your mailbox is connected.', f"<p>This is a test email from TFSC Agreements for {esc(entity['name'].rstrip('.'))}.</p><p>Sending from {esc(box['address'])} works.</p>", box=box),
           'message_id': message_id(box['address'], db.uid())}
    deliver(box, build_message(box, row, []), row['to_addrs'])


# ------------------------------------------------------------------ email templates
def esc(value) -> str:
    return html.escape(str(value or ''), quote=True)


def _paragraphs(text: str) -> str:
    return ''.join(f'<p style="margin:0 0 12px 0;">{esc(p).replace(chr(10), "<br>")}</p>' for p in str(text).split('\n\n') if p.strip())


# Each company's logo and a document icon travel inside the email (multipart/related, cid: links), so they show even when
# the mail client blocks images from the web. The layout follows the familiar e-signature email: logo, a banner in the
# company colour with the call to action, the sender and their message, then guidance on security and questions.
BRAND_DIR = db.APP / 'assets' / 'brand'
INK = '#0B1F33'


def logo_png(entity: dict) -> bytes:
    return (BRAND_DIR / entity['logo']).read_bytes()


@functools.lru_cache(maxsize=8)
def document_icon(accent: str) -> bytes:
    """A white document with a signature line, drawn once per accent colour (PNG, 2x for sharp display)."""
    from PIL import Image, ImageDraw
    s = 2
    image = Image.new('RGBA', (60 * s, 64 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((8 * s, 2 * s, 46 * s, 58 * s), radius=3 * s, fill=(255, 255, 255, 255))
    d.polygon([(36 * s, 2 * s), (46 * s, 12 * s), (36 * s, 12 * s)], fill=(214, 222, 232, 255))
    for i, width in enumerate((28, 24, 28, 18)):
        y = (19 + i * 7) * s
        d.rounded_rectangle((14 * s, y, (14 + width) * s, y + 2 * s), radius=s, fill=(196, 205, 216, 255))
    rgb = tuple(int(accent[i:i + 2], 16) for i in (1, 3, 5))
    d.line([(14 * s, 52 * s), (18 * s, 47 * s), (21 * s, 51 * s), (25 * s, 46 * s), (28 * s, 51 * s), (33 * s, 48 * s)], fill=rgb + (255,), width=2 * s, joint='curve')
    d.polygon([(40 * s, 40 * s), (56 * s, 24 * s), (60 * s, 28 * s), (44 * s, 44 * s), (39 * s, 45 * s)], fill=rgb + (255,))
    out = io.BytesIO()
    image.save(out, format='PNG', optimize=True)
    return out.getvalue()


def inline_images(entity_name: str) -> list[tuple[str, bytes]]:
    entity = get_entity(entity_name)
    return [(f"logo.{entity['key']}@tfsc", logo_png(entity)), (f"doc.{entity['key']}@tfsc", document_icon(entity['accent']))]


def layout(entity: dict, headline: str, body_html: str, *, box: dict | None = None, button: tuple[str, str] | None = None, security_note: bool = False,
           link_text: bool = False) -> str:
    key, color, accent = entity['key'], entity['color'], entity['accent']
    contact = esc(box['address']) if box and box.get('address') else ''
    font = 'font-family:Arial,Helvetica,sans-serif;'
    button_html = ''
    if button:
        button_html = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:26px auto 4px auto;"><tr>'
                       f'<td align="center" bgcolor="{accent}" style="background:{accent};border-radius:4px;">'
                       f'<a href="{esc(button[1])}" target="_blank" style="display:inline-block;padding:14px 34px;{font}font-size:15px;font-weight:bold;'
                       f'color:{INK};text-decoration:none;letter-spacing:.6px;">{esc(button[0])}</a></td></tr></table>')
    sections = []
    if security_note:
        sections.append(('Do not share this email', f'This email contains a secure link to documents from {esc(entity["name"].rstrip("."))}. Please do not share this email, '
                                                    f'the link or any access code with others.'))
        if link_text and button:
            sections.append(('If the button does not work', f'Copy this address into your web browser: <span style="word-break:break-all;color:#30476B;">{esc(button[1])}</span>'))
        sections.append(('Questions about the documents?', 'If you need to change the documents or have questions about their details, reply to this email'
                                                           + (f' or write to {contact}' if contact else '') + '.'))
        sections.append((f'About electronic signatures with {esc(entity["short"])}', 'Sign documents electronically in a few minutes, from any computer, '
                         'tablet or phone. It is secure and legally binding: every step is recorded, and the completed documents come with a Certificate '
                         'of Completion.'))
    elif contact:
        sections.append(('Questions?', f'Reply to this email or contact {contact}.'))
    footer = ''.join(f'<p style="margin:0 0 14px 0;"><strong style="color:#30476B;">{title}</strong><br>{text}</p>' for title, text in sections)
    return f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>{esc(headline)}</title></head>
<body style="margin:0;padding:0;background:#EAEDF1;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#EAEDF1;"><tr><td align="center" style="padding:26px 10px;">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;width:100%;background:#FFFFFF;border-radius:6px;{font}color:#1D2B4F;">
<tr><td style="padding:22px 30px 18px 30px;"><img src="cid:logo.{key}@tfsc" alt="{esc(entity["name"])}" height="52" style="display:block;height:52px;width:auto;max-width:260px;border:0;"></td></tr>
<tr><td style="padding:0 18px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td align="center" bgcolor="{color}" style="background:{color};border-radius:6px;padding:34px 28px 32px 28px;">
<img src="cid:doc.{key}@tfsc" alt="" width="60" height="64" style="display:block;margin:0 auto 18px auto;border:0;">
<div style="{font}font-size:19px;line-height:27px;color:#FFFFFF;">{esc(headline)}</div>{button_html}</td></tr></table></td></tr>
<tr><td style="padding:26px 30px 18px 30px;font-size:14px;line-height:22px;">{body_html}</td></tr>
<tr><td style="padding:0 30px 24px 30px;"><div style="border-top:1px solid #E3E7ED;"></div></td></tr>
<tr><td style="padding:0 30px 10px 30px;font-size:12px;line-height:18px;color:#5B6475;">{footer}</td></tr>
</table>
<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;width:100%;{font}">
<tr><td align="center" style="padding:16px 20px 4px 20px;font-size:11px;line-height:17px;color:#6B7485;">{esc(entity["name"])} · {esc(entity.get("address", ""))}</td></tr>
<tr><td align="center" style="padding:0 20px 6px 20px;font-size:11px;line-height:17px;color:#8A93A3;">Sent with TFSC Agreements electronic signature</td></tr>
</table></td></tr></table></body></html>'''


def text_footer(entity: dict, box: dict | None, security_note: bool) -> str:
    lines = []
    if security_note:
        lines.append('Do not share this email. It contains a secure link to your documents. Please do not share this email, the link or any access code with others.')
    if box and box.get('address'):
        lines.append(f"Questions about these documents? Reply to this email or contact {box['address']}.")
    lines.append(f"{entity['name']} · {entity.get('address', '')}")
    lines.append('Sent with TFSC Agreements electronic signature.')
    return '\n\n' + '\n'.join(lines)


def compose(kind: str, ctx: dict) -> tuple[str, str, str]:
    """Subject, plain text and HTML for each kind of email. Every value from users or clients is escaped."""
    entity, box = ctx['entity'], ctx.get('box')
    subject = ctx.get('subject', '')
    sender = ctx.get('sender_name', entity['short'])
    lines, body, button, headline, security = [], '', None, '', False
    docs = ctx.get('documents', [])
    doc_list = ''.join(f'<li>{esc(d)}</li>' for d in docs)
    if kind in ('invite', 'reminder', 'corrected', 'expiring', 'copy'):
        security = True
        action = {'signer': 'review and sign', 'viewer': 'review', 'cc': 'view'}.get(ctx['role'], 'review and sign')
        headline = {'invite': f'{sender} sent you a document to {action}.', 'reminder': f'Reminder: {sender} sent you a document to {action}.',
                    'corrected': f'{sender} updated a document for you to {action}.', 'expiring': f'A document waiting for you expires on {ctx.get("expires", "")}.',
                    'copy': f'{sender} sent you a copy of documents for your records.'}[kind]
        if kind == 'reminder':
            subject = 'Reminder: ' + subject
        elif kind == 'expiring':
            subject = 'Expiring soon: ' + subject
        elif kind == 'corrected':
            subject = 'Corrected: ' + subject
        button = ({'signer': 'REVIEW DOCUMENT', 'viewer': 'REVIEW DOCUMENT', 'cc': 'VIEW DOCUMENTS', 'in_person': 'START IN-PERSON SIGNING'}.get(ctx['role'], 'REVIEW DOCUMENT'), ctx['url'])
        body = (f'<p style="margin:0 0 16px 0;"><strong style="font-size:15px;">{esc(sender)}</strong><br>'
                f'<a href="mailto:{esc(ctx.get("sender_email", ""))}" style="color:#30476B;text-decoration:none;">{esc(ctx.get("sender_email", ""))}</a></p>')
        if ctx.get('message'):
            body += _paragraphs(ctx['message'])
        if ctx.get('note'):
            body += f'<p style="margin:0 0 12px 0;padding:10px 12px;background:#F2F6FC;border-left:3px solid {entity["color"]};"><strong>Note for you:</strong><br>{esc(ctx["note"]).replace(chr(10), "<br>")}</p>'
        if docs:
            body += f'<p style="margin:0 0 6px 0;">Documents:</p><ul style="margin:0 0 12px 18px;padding:0;">{doc_list}</ul>'
        if ctx.get('access_code'):
            body += '<p style="margin:0 0 12px 0;">You will need the <strong>access code</strong> the sender gave you to open these documents.</p>'
        if ctx.get('expires') and kind != 'expiring':
            body += f'<p style="margin:0 0 12px 0;color:#5B6475;">Please complete this by {esc(ctx["expires"])}.</p>'
        lines = [headline, '', f"{sender} ({ctx.get('sender_email', '')})"]
        if ctx.get('message'):
            lines += ['', ctx['message']]
        if ctx.get('note'):
            lines += ['', 'Note for you: ' + ctx['note']]
        if docs:
            lines += ['', 'Documents:'] + [f'- {d}' for d in docs]
        if ctx.get('access_code'):
            lines += ['', 'You will need the access code the sender gave you to open these documents.']
        lines += ['', f"Open the documents: {ctx['url']}"]
    elif kind == 'completed':
        subject = 'Completed: ' + subject
        headline = 'All parties have completed the documents.'
        button = ('VIEW COMPLETED DOCUMENTS', ctx['url']) if ctx.get('url') else None
        attached = 'Your copy of the completed documents and the Certificate of Completion is attached.' if ctx.get('attached') else \
            'The completed documents are too large to attach. Use the button to download them.'
        body = f'<p style="margin:0 0 12px 0;"><strong>{esc(ctx["recipient_name"])}</strong>,</p><p style="margin:0 0 12px 0;">{esc(attached)}</p><ul style="margin:0 0 12px 18px;padding:0;">{doc_list}</ul>'
        lines = [headline, '', f"{ctx['recipient_name']},", '', attached] + [f'- {d}' for d in docs] + ([f"\nDownload: {ctx['url']}"] if ctx.get('url') else [])
        security = bool(ctx.get('url'))
    elif kind == 'host':
        subject = 'Host in-person signing: ' + subject
        headline = f"It is time for {ctx['actor']} to sign in person."
        button = ('OPEN IN TFSC AGREEMENTS', ctx['url'])
        body = (f'<p style="margin:0 0 12px 0;">You are the host for <strong>{esc(ctx["actor"])}</strong> on envelope {esc(ctx["ref"])}: "{esc(ctx["subject"])}".</p>'
                '<p style="margin:0 0 12px 0;">When you are together, sign in to TFSC Agreements, open the envelope and select <strong>Start in-person '
                'signing</strong>. Then hand your device to the signer.</p>')
        lines = [headline, '', f'You are the host for {ctx["actor"]} on envelope {ctx["ref"]}: "{ctx["subject"]}".',
                 'When you are together, sign in to TFSC Agreements, open the envelope and select Start in-person signing. Then hand your device to the signer.',
                 '', f"Open: {ctx['url']}"]
    elif kind == 'removed':
        subject = 'No longer needed: ' + subject
        headline = f'{sender} removed you from these documents.'
        body = (f'<p style="margin:0 0 12px 0;"><strong>{esc(ctx["recipient_name"])}</strong>,</p>'
                f'<p style="margin:0 0 12px 0;">No action is needed from you for "{esc(ctx["subject"])}". Earlier links to these documents no longer work.</p>')
        lines = [headline, '', f"{ctx['recipient_name']},", '', f'No action is needed from you for "{ctx["subject"]}". Earlier links to these documents no longer work.']
    elif kind in ('voided', 'declined_info'):
        subject = ('Voided: ' if kind == 'voided' else 'Declined: ') + subject
        headline = f'{sender} voided this envelope.' if kind == 'voided' else 'This envelope was declined and is now closed.'
        reason = ctx.get('reason', '')
        body = f'<p style="margin:0 0 12px 0;"><strong>{esc(ctx["recipient_name"])}</strong>,</p><p style="margin:0 0 12px 0;">No further action is needed from you for "{esc(ctx["subject"])}".</p>'
        if reason:
            body += f'<p style="margin:0 0 12px 0;"><strong>Reason:</strong> {esc(reason)}</p>'
        lines = [headline, '', f"{ctx['recipient_name']},", '', f'No further action is needed from you for "{ctx["subject"]}".'] + ([f'Reason: {reason}'] if reason else [])
    elif kind.startswith('notify_'):
        event = kind[7:]
        who = ctx.get('actor', '')
        headline = {'viewed': f'{who} viewed the documents.', 'signed': f'{who} signed the documents.', 'completed': 'All parties completed the documents.',
                    'declined': f'{who} declined to sign.', 'bounced': f'The email to {who} could not be delivered.',
                    'failed': f'The email to {who} could not be sent.', 'replied': f'{who} replied by email.', 'voided': 'The envelope was voided.',
                    'expired': 'The envelope expired before it was completed.', 'auth_failed': f'{who} entered a wrong access code too many times.',
                    'reassigned': f'{who} assigned the documents to someone else.'}[event]
        subject = {'viewed': 'Viewed: ', 'signed': 'Signed: ', 'completed': 'Completed: ', 'declined': 'Declined: ', 'bounced': 'Undeliverable: ',
                   'failed': 'Not sent: ', 'replied': 'Reply received: ', 'voided': 'Voided: ', 'expired': 'Expired: ', 'auth_failed': 'Access code failed: ',
                   'reassigned': 'Reassigned: '}[event] + subject
        button = ('OPEN IN TFSC AGREEMENTS', ctx['url'])
        body = f'<p style="margin:0 0 12px 0;">Envelope {esc(ctx["ref"])}: "{esc(ctx["subject"])}"</p>'
        if ctx.get('detail'):
            body += f'<p style="margin:0 0 12px 0;padding:10px 12px;background:#F7F8FB;">{esc(ctx["detail"]).replace(chr(10), "<br>")}</p>'
        if event == 'completed' and ctx.get('attached'):
            body += '<p style="margin:0 0 12px 0;">The completed documents and the Certificate of Completion are attached.</p>'
        lines = [headline, '', f'Envelope {ctx["ref"]}: "{ctx["subject"]}"'] + ([ctx['detail']] if ctx.get('detail') else []) + ['', f"Open: {ctx['url']}"]
    else:
        raise ValueError('Unknown email kind.')
    html_body = layout(entity, headline, body, box=box, button=button, security_note=security, link_text=kind in ('invite', 'reminder', 'corrected', 'expiring'))
    return subject[:300], '\n'.join(lines) + text_footer(entity, box, security), html_body


# ------------------------------------------------------------------ incoming mail (IMAP)
def _imap_session(box: dict):
    host, port, security = box['imap_host'], box['imap_port'], box['imap_security']
    if security == 'ssl':
        imap = imaplib.IMAP4_SSL(host, port, ssl_context=_context(), timeout=30)
    else:
        imap = imaplib.IMAP4(host, port, timeout=30)
        if security == 'starttls':
            imap.starttls(ssl_context=_context())
    user = box['login'] or box['address']
    try:
        if box['auth'] == 'google_sa':
            sa = json.loads(secretbox.open_(box['sa_key'], 'sa:' + box['entity']))
            token = google_token(sa, user)
            imap.authenticate('XOAUTH2', lambda _: _xoauth2(user, token).encode())
        else:
            imap.login(user, secretbox.open_(box['secret'], 'smtp:' + box['entity']))
    except Exception:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
        raise
    return imap


def check_inbox(c, entity_name: str) -> int:
    """Sign in and open the inbox read-only. Returns the message count (for the settings screen's test button)."""
    box = transport(c, entity_name)
    if box['mode'] != 'mailbox':
        raise MailboxNotReady('Reading replies needs the company mailbox itself (not the platform mail server).')
    imap = _imap_session(box)
    try:
        status, data = imap.select('INBOX', readonly=True)
        if status != 'OK':
            raise MailboxNotReady('The inbox could not be opened.')
        return int(data[0])
    finally:
        imap.logout()


def poll_inboxes() -> int:
    processed = 0
    now = time.time()
    with db.engine.connect() as c:
        names = [r[0] for r in c.execute(select(mailboxes.c.entity).where(mailboxes.c.mode == 'mailbox', mailboxes.c.imap_enabled.is_(True),
                                                                          or_(mailboxes.c.imap_last_poll.is_(None), mailboxes.c.imap_last_poll < now - poll_interval())))]
    for name in names:
        with db.engine.begin() as c:
            claimed = c.execute(update(mailboxes).where(mailboxes.c.entity == name, or_(mailboxes.c.imap_lease.is_(None), mailboxes.c.imap_lease < now))
                                .values(imap_lease=now + 600))
        if claimed.rowcount != 1:
            continue
        try:
            processed += _poll(name)
            error = None
        except Exception as exc:  # noqa: BLE001
            error = _describe(exc) if not isinstance(exc, imaplib.IMAP4.error) else f'The mail server refused: {str(exc)[:300]}'
            log.warning('inbox poll for %s failed: %s', name, error)
        with db.engine.begin() as c:
            c.execute(update(mailboxes).where(mailboxes.c.entity == name).values(imap_lease=None, imap_last_poll=time.time(), imap_error=error))
    return processed


def poll_interval() -> float:
    return float(os.getenv('TFSC_IMAP_POLL_SECONDS', '90'))


def _poll(name: str) -> int:
    with db.engine.connect() as c:
        box = load(c, name)
    if not ready(box):
        return 0
    imap = _imap_session(box)
    count = 0
    try:
        status, _ = imap.select('INBOX', readonly=True)
        if status != 'OK':
            raise MailboxNotReady('The inbox could not be opened.')
        validity = int(imap.response('UIDVALIDITY')[1][0])
        last = box['imap_last_uid']
        if box['imap_uidvalidity'] != validity or last is None:
            # First connection (or the mailbox was rebuilt): start where the settings were saved. Older mail is never
            # imported; mail that arrived since then (a reply before this first check) is read below.
            last = _starting_uid(imap, box['updated'])
            with db.engine.begin() as c:
                c.execute(update(mailboxes).where(mailboxes.c.entity == name).values(imap_uidvalidity=validity, imap_last_uid=last))
        status, data = imap.uid('SEARCH', None, f'UID {last + 1}:*')
        uids = sorted(int(x) for x in (data[0] or b'').split() if int(x) > last)[:100]
        for uid in uids:
            status, data = imap.uid('FETCH', str(uid), '(RFC822.SIZE BODY.PEEK[HEADER])')
            header, size = b'', 0
            for item in data:
                if isinstance(item, tuple):
                    header = item[1]
                    match = re.search(rb'RFC822\.SIZE (\d+)', item[0])
                    size = int(match.group(1)) if match else 0
            raw = None
            if size <= 25_000_000 and _maybe_ours(header):
                status, data = imap.uid('FETCH', str(uid), '(BODY.PEEK[])')
                raw = next((item[1] for item in data if isinstance(item, tuple)), None)
            with db.engine.begin() as c:
                if raw:
                    count += process_inbound(c, box, validity, uid, raw)
                c.execute(update(mailboxes).where(mailboxes.c.entity == name).values(imap_last_uid=uid))
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return count


def _starting_uid(imap, since: float) -> int:
    """Highest UID that arrived before `since` (only the latest 200 messages are examined)."""
    status, data = imap.uid('SEARCH', None, 'ALL')
    uids = sorted(int(x) for x in (data[0] or b'').split())
    if not uids:
        return 0
    recent = uids[-200:]
    status, data = imap.uid('FETCH', f'{recent[0]}:{recent[-1]}', '(INTERNALDATE)')
    arrived = {}
    for item in data or []:
        raw = item[0] if isinstance(item, tuple) else item
        if not isinstance(raw, bytes):
            continue
        uid = re.search(rb'UID (\d+)', raw)
        when = imaplib.Internaldate2tuple(raw)
        if uid and when:
            arrived[int(uid.group(1))] = time.mktime(when)
    older = [u for u in recent if u in arrived and arrived[u] < since]
    return max(older) if older else (recent[0] - 1 if len(uids) <= 200 else recent[0])


def _maybe_ours(header: bytes) -> bool:
    text = header.decode('utf-8', errors='replace')
    if OUR_ID.search(text):
        return True
    msg = email.message_from_string(text, policy=email.policy.default)
    content_type = (msg.get('Content-Type') or '').lower()
    sender = parseaddr(str(msg.get('From', '')))[1].lower()
    return 'multipart/report' in content_type or sender.split('@')[0] in ('mailer-daemon', 'postmaster')


def _body_text(msg) -> str:
    part = msg.get_body(preferencelist=('plain', 'html'))
    if part is None:
        return ''
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        return ''
    if part.get_content_subtype() == 'html':
        content = re.sub(r'(?is)<(script|style).*?</\1>', ' ', content)
        content = html.unescape(re.sub(r'<[^>]+>', ' ', content))
    kept = []
    for line in content.splitlines():
        if line.startswith('>') or re.match(r'^On .{5,200} wrote:\s*$', line.strip()) or line.strip() in ('-----Original Message-----',):
            if re.match(r'^On .{5,200} wrote:\s*$', line.strip()) or line.strip() == '-----Original Message-----':
                break
            continue
        kept.append(line)
    text = re.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()
    return ''.join(ch for ch in text if ch in '\n\t' or ch.isprintable())[:3000]


def _dsn(msg) -> dict | None:
    """Parse a delivery status notification; only permanent failures count as a bounce."""
    content_type = msg.get_content_type()
    report = None
    if content_type == 'multipart/report':
        for part in msg.walk():
            if part.get_content_type() == 'message/delivery-status':
                report = part
                break
    sender = parseaddr(str(msg.get('From', '')))[1].lower()
    if report is None and sender.split('@')[0] not in ('mailer-daemon', 'postmaster'):
        return None
    failed, status, diagnostic, rcpt = report is None, '', '', ''
    if report is not None:
        blocks = []
        for sub in report.iter_parts() if report.is_multipart() else []:
            blocks.append(sub)
        raw = report.get_payload()
        texts = [str(b) for b in blocks] if blocks else [raw if isinstance(raw, str) else str(raw)]
        joined = '\n'.join(texts)
        failed = bool(re.search(r'(?im)^Action:\s*failed', joined))
        m = re.search(r'(?im)^Status:\s*([245]\.\d+\.\d+)', joined)
        status = m.group(1) if m else ''
        m = re.search(r'(?im)^Diagnostic-Code:\s*(.+(?:\n[ \t].+)*)', joined)
        diagnostic = re.sub(r'\s+', ' ', m.group(1)).strip()[:500] if m else ''
        m = re.search(r'(?im)^Final-Recipient:\s*[^;]*;\s*(\S+)', joined)
        rcpt = m.group(1).strip('<>') if m else ''
    if not failed:
        return None
    return {'status': status, 'diagnostic': diagnostic, 'recipient': rcpt}


ALLOWED_ATTACHMENTS = {'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg',
                       'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx'}


def scan(body: bytes) -> str:
    """'clean', 'infected' or 'not_scanned' (no scanner configured). Raises if a configured scanner is unreachable."""
    target = os.getenv('TFSC_CLAMAV', '')
    if not target:
        return 'not_scanned'
    host, _, port = target.rpartition(':')
    with socket.create_connection((host, int(port)), timeout=30) as s:
        s.sendall(b'zINSTREAM\0')
        for i in range(0, len(body), 65536):
            chunk = body[i:i + 65536]
            s.sendall(struct.pack('>I', len(chunk)) + chunk)
        s.sendall(struct.pack('>I', 0))
        reply = b''
        while not reply.endswith(b'\0'):
            data = s.recv(4096)
            if not data:
                break
            reply += data
    text = reply.decode(errors='replace')
    if 'FOUND' in text:
        return 'infected'
    if text.rstrip('\0').endswith('OK'):
        return 'clean'
    raise RuntimeError('The virus scanner returned an error: ' + text[:200])


def process_inbound(c, box: dict, validity: int, uid: int, raw: bytes) -> int:
    """Attach a bounce or a client's reply to its envelope. Returns 1 when the message was ours."""
    import esign
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(str(msg.get('From', '')))[1]
    if sender.casefold() == box['address'].casefold():
        return 0  # our own copies
    if c.execute(select(inbound.c.id).where(inbound.c.entity == box['entity'], inbound.c.uidvalidity == validity, inbound.c.uid == uid)).first():
        return 0
    bounce = _dsn(msg)
    if bounce is not None:
        ids = OUR_ID.findall(raw.decode('utf-8', errors='replace'))
    else:
        if str(msg.get('Auto-Submitted', 'no')).lower() not in ('no', ''):
            return 0  # out-of-office and other automatic replies
        ids = OUR_ID.findall(' '.join(str(msg.get(h, '')) for h in ('In-Reply-To', 'References')))
    row = None
    for full, outbox_id in reversed(ids):
        row = c.execute(select(outbox).where(outbox.c.id == outbox_id)).mappings().first()
        if row and row['entity'] == box['entity'] and row['message_id'].strip('<>').startswith(full):
            break
        row = None
    if row is None or not row['envelope_id']:
        return 0
    record = dict(id=db.uid(), entity=box['entity'], uidvalidity=validity, uid=uid, kind='bounce' if bounce else 'reply',
                  message_id=str(msg.get('Message-ID', ''))[:250], from_addr=sender[:250], subject=re.sub(r'[\r\n]+', ' ', str(msg.get('Subject', '')))[:300],
                  received=time.time(), envelope_id=row['envelope_id'], recipient_id=row['recipient_id'], outbox_id=row['id'], created=time.time(),
                  snippet=(f"{bounce['status']} {bounce['diagnostic']}".strip() if bounce else _body_text(msg)))
    c.execute(inbound.insert().values(**record))
    if bounce:
        c.execute(update(outbox).where(outbox.c.id == row['id']).values(state='bounced', last_error=record['snippet'][:2000]))
        if row['recipient_id']:
            c.execute(update(recipients).where(recipients.c.id == row['recipient_id']).values(delivery='bounced', delivery_error=record['snippet'][:1000] or 'Undeliverable'))
        esign.mail_bounced(c, row, record)
        return 1
    files, skipped = [], []
    for part in msg.iter_attachments():
        content_type = part.get_content_type()
        name = re.sub(r'[^\w .()-]', '_', part.get_filename() or 'attachment')[:150] or 'attachment'
        body = part.get_payload(decode=True) or b''
        if content_type not in ALLOWED_ATTACHMENTS or not 0 < len(body) <= 10_000_000 or len(files) >= 5:
            skipped.append(name)
            continue
        try:
            verdict = scan(body)
        except Exception as exc:  # noqa: BLE001
            log.warning('attachment scan failed: %s', exc)
            skipped.append(name + ' (could not be scanned)')
            continue
        if verdict == 'infected':
            skipped.append(name + ' (blocked: malware detected)')
            continue
        files.append(dict(id=db.uid(), inbound_id=record['id'], name=name, content_type=content_type, size=len(body),
                          sha256=hashlib.sha256(body).hexdigest(), scan=verdict, body=body))
    for f in files:
        c.execute(inbound_files.insert().values(**f))
    esign.mail_replied(c, row, record, [f['name'] for f in files], skipped)
    return 1


# ------------------------------------------------------------------ worker loop
wake = threading.Event()


def addresses(value: str) -> list[str]:
    return [a for _, a in getaddresses([value]) if a]


def known_entities() -> list[str]:
    return list(ENTITIES)
