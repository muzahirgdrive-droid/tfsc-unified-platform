"""Billing Nest's corrected letterhead (the earlier file showed another company's address in its footer).

Data only. The new letterhead replaces the old one only where the old, incorrect file is still in use: a letterhead a Super
Admin uploaded in Company settings since then is left as it is. Agreements already generated or signed keep the letterhead
they were issued with; every agreement generated from now on uses the new one. The margins come from the letterhead check.

Revision ID: 0010_billing_nest_letterhead
Revises: 0009_five_companies
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = '0010_billing_nest_letterhead'
down_revision = '0009_five_companies'
branch_labels = None
depends_on = None

ASSETS = Path(__file__).resolve().parents[2] / 'assets' / 'companies'
OLD_SHA = 'b0dedfb1e6dc36d56ddd09bd99a72118a79406edcb7f286c7a211cb620d55911'   # the incorrect file shipped with 0009

companies = sa.table('companies', sa.column('key', sa.String), sa.column('name', sa.String), sa.column('letterhead', sa.LargeBinary),
                     sa.column('letterhead_sha256', sa.String), sa.column('width', sa.Integer), sa.column('top', sa.Integer),
                     sa.column('bottom', sa.Integer), sa.column('left', sa.Integer), sa.column('right', sa.Integer), sa.column('updated', sa.Float))


def upgrade() -> None:
    spec = next(m for m in json.loads((ASSETS / 'companies.json').read_text(encoding='utf-8')) if m['key'] == 'billing_nest')
    raw = (ASSETS / spec['letterhead']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != spec['letterhead_sha256']:
        raise RuntimeError("The Billing Nest letterhead file is damaged; reinstall the release.")
    op.get_bind().execute(companies.update().where(companies.c.letterhead_sha256 == OLD_SHA).values(
        letterhead=raw, letterhead_sha256=spec['letterhead_sha256'], width=spec['width'], top=spec['top'], bottom=spec['bottom'],
        left=spec['left'], right=spec['right'], updated=time.time()))


def downgrade() -> None:
    # The old file was wrong; stepping back keeps the corrected letterhead.
    pass
