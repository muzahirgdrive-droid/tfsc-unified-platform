"""Fail if a rendered Helm Job hook does not run on both install and upgrade.

Schema validation cannot catch a hook list that YAML flow syntax split (`{ "helm.sh/hook": pre-install,pre-upgrade }`
makes the value `pre-install` and adds an empty `pre-upgrade` key), which silently skipped migrations on upgrades.
Usage: helm template t infra/helm/tfsc-platform | python tools/check_helm_hooks.py
"""
import sys

import yaml

jobs = [d for d in yaml.safe_load_all(sys.stdin) if d and d.get('kind') == 'Job']
bad = {j['metadata']['name']: j['metadata'].get('annotations', {}).get('helm.sh/hook') for j in jobs
       if set(str(j['metadata'].get('annotations', {}).get('helm.sh/hook', '')).split(',')) != {'pre-install', 'pre-upgrade'}}
print(f'{len(jobs)} hook jobs checked')
if not jobs or bad:
    sys.exit(f'Helm hook jobs that would not run on install and upgrade: {bad or "no jobs rendered"}')
