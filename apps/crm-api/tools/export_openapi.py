"""Writes apps/crm-api/openapi.json (the API contract the web client types are generated from).

Usage: python tools/export_openapi.py && (cd ../crm-web && npm run gen:api)
CI fails if the committed openapi.json is stale (see .github/workflows/ci.yml).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tfsc_crm.main import create_app

spec = create_app().openapi()
out = Path(__file__).resolve().parents[1] / 'openapi.json'
out.write_text(json.dumps(spec, indent=1, sort_keys=True) + '\n', encoding='utf-8')
print(out, len(spec['paths']), 'paths')
