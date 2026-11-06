"""Five more contracting companies: House of RCM, Quantum Medical Services, MedEquity Solutions, 3E Medical Billing and
Billing Nest, with their letterheads, logos, addresses and governing law.

Data only, nothing in the schema changes. A company already present (same legal name or key, for example added by hand
in Company settings) is left exactly as it is. The letterheads' margins come from the letterhead check, so the agreement
text clears each letterhead's artwork. The signatory is left for a Super Admin to set in Company settings; generating
for the company asks for it first. They offer the standard service catalogue (an empty list of own services).

Revision ID: 0009_five_companies
Revises: 0008_client_records
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = '0009_five_companies'
down_revision = '0008_client_records'
branch_labels = None
depends_on = None

ASSETS = Path(__file__).resolve().parents[2] / 'assets' / 'companies'

companies = sa.table(
    'companies', sa.column('key', sa.String), sa.column('name', sa.String), sa.column('short', sa.String), sa.column('display', sa.String),
    sa.column('address', sa.Text), sa.column('email', sa.String), sa.column('governing_state', sa.String), sa.column('timezone', sa.String),
    sa.column('color', sa.String), sa.column('accent', sa.String), sa.column('dark', sa.String), sa.column('signatory', sa.String),
    sa.column('title', sa.String), sa.column('signature_scale', sa.Float), sa.column('letterhead', sa.LargeBinary),
    sa.column('letterhead_sha256', sa.String), sa.column('logo', sa.LargeBinary), sa.column('logo_ext', sa.String),
    sa.column('width', sa.Integer), sa.column('top', sa.Integer), sa.column('bottom', sa.Integer), sa.column('left', sa.Integer),
    sa.column('right', sa.Integer), sa.column('services', sa.JSON), sa.column('doc_types', sa.JSON), sa.column('active', sa.Boolean),
    sa.column('created', sa.Float), sa.column('created_by', sa.String), sa.column('updated', sa.Float))


def _manifest() -> list[dict]:
    return json.loads((ASSETS / 'companies.json').read_text(encoding='utf-8'))


def upgrade() -> None:
    bind = op.get_bind()
    now = time.time()
    for spec in _manifest():
        present = bind.execute(sa.select(companies.c.key).where(sa.or_(companies.c.name == spec['name'], companies.c.key == spec['key']))).first()
        if present:
            continue
        letterhead = (ASSETS / spec['letterhead']).read_bytes()
        if hashlib.sha256(letterhead).hexdigest() != spec['letterhead_sha256']:
            raise RuntimeError(f"The letterhead file for {spec['name']} is damaged; reinstall the release.")
        bind.execute(companies.insert().values(
            key=spec['key'], name=spec['name'], short=spec['short'], display=spec['display'], address=spec['address'], email=spec['email'],
            governing_state=spec['governing_state'], timezone=spec['timezone'], color=spec['color'], accent=spec['accent'], dark=spec['dark'],
            signatory='', title='', signature_scale=0.85, letterhead=letterhead, letterhead_sha256=spec['letterhead_sha256'],
            logo=(ASSETS / spec['logo']).read_bytes(), logo_ext='png', width=spec['width'], top=spec['top'], bottom=spec['bottom'],
            left=spec['left'], right=spec['right'], services=[], doc_types=['baa', 'services', 'nda'], active=True,
            created=now, created_by=None, updated=now))


def downgrade() -> None:
    # Only companies this migration would have added, and only while nothing was generated for them.
    bind = op.get_bind()
    contracts = sa.table('contracts', sa.column('entity', sa.String))
    for spec in _manifest():
        if bind.execute(sa.select(contracts.c.entity).where(contracts.c.entity == spec['name']).limit(1)).first() is None:
            bind.execute(companies.delete().where(companies.c.key == spec['key'], companies.c.signatory == ''))
