"""E-signature and company mailbox tables (envelopes, recipients, fields, audit trail, mail outbox and inbox)."""
from sqlalchemy import (JSON, BigInteger, Boolean, Column, Float, ForeignKey, Index, Integer, LargeBinary, String, Table, Text,
                        UniqueConstraint)

from database import meta

envelopes = Table(
    'esign_envelopes', meta,
    Column('id', String(32), primary_key=True),
    Column('ref', String(20), nullable=False, unique=True),
    Column('owner_id', String(32), ForeignKey('users.id'), nullable=False),
    Column('entity', String(100), nullable=False),
    Column('client_id', String(32), ForeignKey('clients.id'), nullable=True),
    Column('contract_id', String(64), ForeignKey('contracts.id'), nullable=True),
    Column('subject', String(200), nullable=False),
    Column('message', Text, nullable=False),
    # draft | sent | completing | completed | declined | voided | expired | template
    Column('status', String(20), nullable=False),
    Column('template_name', String(150), nullable=True),
    Column('settings', JSON, nullable=False),
    Column('version', Integer, nullable=False),
    Column('created', Float, nullable=False),
    Column('updated', Float, nullable=False),
    Column('sent_at', Float, nullable=True),
    Column('expires_at', Float, nullable=True),
    Column('expiry_warned', Boolean, nullable=False),
    Column('completed_at', Float, nullable=True),
    Column('closed_at', Float, nullable=True),
    Column('close_reason', Text, nullable=True),
    Column('completion_attempts', Integer, nullable=False),
    Column('completion_next', Float, nullable=True),
    Column('completion_error', Text, nullable=True),
    Column('lease_until', Float, nullable=True),
    Column('certificate', LargeBinary, nullable=True),
    Column('certificate_sha256', String(64), nullable=True),
)
Index('ix_esign_env_owner_updated', envelopes.c.owner_id, envelopes.c.updated)
Index('ix_esign_env_status', envelopes.c.status, envelopes.c.completion_next)
Index('ix_esign_env_client', envelopes.c.client_id, envelopes.c.created)

documents = Table(
    'esign_documents', meta,
    Column('id', String(32), primary_key=True),
    Column('envelope_id', String(32), ForeignKey('esign_envelopes.id'), nullable=False, index=True),
    Column('position', Integer, nullable=False),
    Column('name', String(200), nullable=False),
    Column('source', String(20), nullable=False),            # generated | upload | template
    Column('source_document_id', String(32), nullable=True),  # documents.id of a generated agreement
    Column('agreement', String(20), nullable=True),           # baa | services | nda for generated agreements
    Column('pdf', LargeBinary, nullable=False),
    Column('sha256', String(64), nullable=False),
    Column('pages', Integer, nullable=False),
    Column('page_sizes', JSON, nullable=False),               # [[width, height, rotation], ...] as displayed, in points
    Column('final_pdf', LargeBinary, nullable=True),
    Column('final_sha256', String(64), nullable=True, index=True),
    Column('created', Float, nullable=False),
)

recipients = Table(
    'esign_recipients', meta,
    Column('id', String(32), primary_key=True),
    Column('envelope_id', String(32), ForeignKey('esign_envelopes.id'), nullable=False, index=True),
    Column('role', String(12), nullable=False),               # signer | viewer | cc
    Column('role_name', String(60), nullable=False),          # label in templates ("Client", "Company signer")
    Column('routing_order', Integer, nullable=False),
    Column('name', String(150), nullable=False),
    Column('email', String(200), nullable=False),
    Column('access_code', Text, nullable=True),               # argon2 hash
    Column('note', Text, nullable=False),                     # private message for this recipient
    # created | sent | delivered | signed | completed | declined | auth_failed
    Column('status', String(20), nullable=False),
    Column('sent_at', Float, nullable=True),
    Column('delivered_at', Float, nullable=True),
    Column('signed_at', Float, nullable=True),
    Column('declined_at', Float, nullable=True),
    Column('decline_reason', Text, nullable=True),
    Column('consent_at', Float, nullable=True),
    Column('ip', String(64), nullable=True),
    Column('user_agent', String(300), nullable=True),
    Column('auth_method', String(40), nullable=True),
    Column('signature_png', LargeBinary, nullable=True),
    Column('initials_png', LargeBinary, nullable=True),
    Column('signature_kind', String(10), nullable=True),      # typed | drawn | uploaded
    Column('auth_failures', Integer, nullable=False),
    Column('delivery', String(12), nullable=False),           # pending | sent | bounced | failed
    Column('delivery_error', Text, nullable=True),
    Column('reminders_sent', Integer, nullable=False),
    Column('last_reminder_at', Float, nullable=True),
    Column('created', Float, nullable=False),
)
Index('ix_esign_rcpt_email', recipients.c.email, recipients.c.status)

fields = Table(
    'esign_fields', meta,
    Column('id', String(32), primary_key=True),
    Column('envelope_id', String(32), ForeignKey('esign_envelopes.id'), nullable=False, index=True),
    Column('document_id', String(32), ForeignKey('esign_documents.id'), nullable=False),
    Column('recipient_id', String(32), ForeignKey('esign_recipients.id'), nullable=False),
    Column('page', Integer, nullable=False),
    # signature | initials | date_signed | name | email | company | title | text | checkbox | dropdown
    Column('kind', String(20), nullable=False),
    Column('x', Float, nullable=False), Column('y', Float, nullable=False),
    Column('w', Float, nullable=False), Column('h', Float, nullable=False),
    Column('required', Boolean, nullable=False),
    Column('label', String(100), nullable=False),
    Column('options', JSON, nullable=False),
    Column('value', Text, nullable=True),
    Column('filled_at', Float, nullable=True),
)

events = Table(
    'esign_events', meta,
    Column('id', String(32), primary_key=True),
    Column('envelope_id', String(32), ForeignKey('esign_envelopes.id'), nullable=False),
    Column('seq', Integer, nullable=False),
    Column('recipient_id', String(32), nullable=True),
    Column('at', Float, nullable=False),
    Column('kind', String(40), nullable=False),
    Column('actor', String(250), nullable=False),
    Column('ip', String(64), nullable=False),
    Column('user_agent', String(300), nullable=False),
    Column('detail', JSON, nullable=False),
    Column('prev_hash', String(64), nullable=False),
    Column('hash', String(64), nullable=False),
    UniqueConstraint('envelope_id', 'seq', name='uq_esign_event_seq'),
)

tokens = Table(
    'esign_tokens', meta,
    Column('id', String(64), primary_key=True),               # sha256 of the token in a signing link
    Column('recipient_id', String(32), ForeignKey('esign_recipients.id'), nullable=False, index=True),
    Column('via', String(12), nullable=False),                # email | portal (one use, five minutes)
    Column('created', Float, nullable=False),
    Column('expires', Float, nullable=True),
)

sign_sessions = Table(
    'esign_sessions', meta,
    Column('id', String(64), primary_key=True),               # sha256 of the session cookie
    Column('csrf', String(64), nullable=False),
    Column('recipient_id', String(32), ForeignKey('esign_recipients.id'), nullable=False, index=True),
    Column('verified', Boolean, nullable=False),
    Column('via', String(12), nullable=False),                # email | portal
    Column('expires', Float, nullable=False, index=True),
)

mailboxes = Table(
    'mailboxes', meta,
    Column('entity', String(100), primary_key=True),
    Column('address', String(200), nullable=False),
    Column('display_name', String(150), nullable=False),
    Column('mode', String(12), nullable=False),               # mailbox | relay | off
    Column('auth', String(16), nullable=False),               # password | google_sa
    Column('login', String(200), nullable=False),
    Column('secret', Text, nullable=True),                    # encrypted app password
    Column('sa_key', Text, nullable=True),                    # encrypted Google service account JSON
    Column('smtp_host', String(200), nullable=False),
    Column('smtp_port', Integer, nullable=False),
    Column('smtp_security', String(10), nullable=False),      # starttls | ssl | none
    Column('imap_enabled', Boolean, nullable=False),
    Column('imap_host', String(200), nullable=False),
    Column('imap_port', Integer, nullable=False),
    Column('imap_security', String(10), nullable=False),      # ssl | starttls | none
    Column('signer_email', String(200), nullable=False),
    Column('notify', JSON, nullable=False),
    Column('imap_uidvalidity', BigInteger, nullable=True),
    Column('imap_last_uid', BigInteger, nullable=True),
    Column('imap_last_poll', Float, nullable=True),
    Column('imap_error', Text, nullable=True),
    Column('imap_lease', Float, nullable=True),
    Column('smtp_error', Text, nullable=True),
    Column('last_test', JSON, nullable=False),
    Column('updated', Float, nullable=False),
    Column('updated_by', String(32), nullable=True),
)

outbox = Table(
    'mail_outbox', meta,
    Column('id', String(32), primary_key=True),
    Column('entity', String(100), nullable=False),
    Column('envelope_id', String(32), ForeignKey('esign_envelopes.id'), nullable=True, index=True),
    Column('recipient_id', String(32), nullable=True),
    Column('kind', String(40), nullable=False),
    Column('to_addrs', JSON, nullable=False),
    Column('cc_addrs', JSON, nullable=False),
    Column('bcc_addrs', JSON, nullable=False),
    Column('subject', String(300), nullable=False),
    Column('text', Text, nullable=False),
    Column('html', Text, nullable=False),
    Column('attachments', JSON, nullable=False),
    Column('message_id', String(250), nullable=False, unique=True),
    Column('state', String(12), nullable=False),              # queued | sending | sent | failed | bounced
    Column('attempts', Integer, nullable=False),
    Column('next_attempt', Float, nullable=False),
    Column('lease_until', Float, nullable=True),
    Column('last_error', Text, nullable=True),
    Column('created', Float, nullable=False),
    Column('sent_at', Float, nullable=True),
)
Index('ix_mail_outbox_queue', outbox.c.state, outbox.c.next_attempt)

inbound = Table(
    'mail_inbound', meta,
    Column('id', String(32), primary_key=True),
    Column('entity', String(100), nullable=False),
    Column('uidvalidity', BigInteger, nullable=False),
    Column('uid', BigInteger, nullable=False),
    Column('kind', String(12), nullable=False),               # reply | bounce
    Column('message_id', String(250), nullable=False),
    Column('from_addr', String(250), nullable=False),
    Column('subject', String(300), nullable=False),
    Column('received', Float, nullable=False),
    Column('envelope_id', String(32), ForeignKey('esign_envelopes.id'), nullable=True, index=True),
    Column('recipient_id', String(32), nullable=True),
    Column('outbox_id', String(32), nullable=True),
    Column('snippet', Text, nullable=False),
    Column('created', Float, nullable=False),
    UniqueConstraint('entity', 'uidvalidity', 'uid', name='uq_mail_inbound_uid'),
)

inbound_files = Table(
    'mail_inbound_files', meta,
    Column('id', String(32), primary_key=True),
    Column('inbound_id', String(32), ForeignKey('mail_inbound.id'), nullable=False, index=True),
    Column('name', String(200), nullable=False),
    Column('content_type', String(100), nullable=False),
    Column('size', Integer, nullable=False),
    Column('sha256', String(64), nullable=False),
    Column('scan', String(20), nullable=False),               # clean | not_scanned
    Column('body', LargeBinary, nullable=False),
)
