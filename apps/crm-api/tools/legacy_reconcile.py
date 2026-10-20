"""Reconcile a legacy tracking workbook against the §2 figures without touching any database.

Usage: python tools/legacy_reconcile.py <workbook.xlsx> [--start 2026-10-01 --end 2026-10-14] [--json]
Prints aggregate figures only (no row contents). Exit code 1 if the audited workbook does not reproduce §2.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tfsc_crm.domain import legacy_workbook as lw

ap = argparse.ArgumentParser()
ap.add_argument('workbook')
ap.add_argument('--start', default=lw.AUDITED_PERIOD[0].isoformat())
ap.add_argument('--end', default=lw.AUDITED_PERIOD[1].isoformat())
ap.add_argument('--json', action='store_true')
args = ap.parse_args()
s = lw.scan(args.workbook)
rep = lw.reconciliation(s, date.fromisoformat(args.start), date.fromisoformat(args.end))
summary = s.summary()
summary['sheets'] = [{k: v for k, v in sh.items() if k != 'name'} | {'name': sh['name'][:3] + '…'} for sh in summary['sheets']]
summary['roster'] = len(summary['roster'])
out = {'summary': summary, 'reconciliation': rep}
if args.json:
    print(json.dumps(out, indent=1, default=str))
else:
    print('sha256      ', s.sha256)
    print('entries     ', summary['rows'], '| attempts (all time)', summary['attempts_all_time'], '| review', summary['review'])
    for k, v in rep['workbook'].items():
        exp = rep.get('expected', {}).get(k)
        ok = rep.get('workbook_matches_expected', {}).get(k)
        print(f'{k:15} {v!s:>10}' + (f'   expected {exp!s:>10}  {"OK" if ok else "MISMATCH"}' if exp is not None else ''))
    print('phone repeats', rep['phone_repeats'])
sys.exit(0 if all(rep.get('workbook_matches_expected', {}).values()) else 1)
