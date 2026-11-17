"""Local secret readability only: no database, DNS, HTTP, dispatch or billing.

Exit zero means the selected role's local configuration is present, NOT that
credentials, model IDs, budgets, permissions or external time are validated.
"""
from __future__ import annotations

import argparse
import json
from typing import Mapping

from .secrets import read_secret

ROLE_SECRETS = {
    'dashboard': ('FI_DASHBOARD_DATABASE_URL', 'FI_DASHBOARD_TOKEN'),
    'operator': ('FI_OPERATOR_DATABASE_URL',),
    'worker': ('FI_WORKER_DATABASE_URL',),
    'verifier': ('FI_VERIFIER_DATABASE_URL',),
    'source-admin': ('FI_SOURCE_ADMIN_DATABASE_URL',),
    'admission-reviewer': ('FI_ADMISSION_REVIEWER_DATABASE_URL',),
    'resolver': ('FI_RESOLVER_DATABASE_URL',),
    'reviewer': ('FI_REVIEWER_DATABASE_URL',),
    'scorer': ('FI_SCORER_DATABASE_URL',),
}
PROVIDER_KEYS = {
    'openai': 'OPENAI_API_KEY',
    'anthropic': 'ANTHROPIC_API_KEY',
    'google': 'GEMINI_API_KEY',
}


def inspect_configuration(role: str, provider: str | None = None,
                          environ: Mapping[str, str] | None = None) -> dict:
    """Inspect only explicitly named role inputs; never return values or paths."""
    if role not in ROLE_SECRETS:
        raise ValueError('unknown role')
    if (role == 'worker' and provider not in PROVIDER_KEYS) or (role != 'worker' and provider is not None):
        raise ValueError('provider is required only for worker')
    names = ROLE_SECRETS[role] + ((PROVIDER_KEYS[provider],) if provider else ())
    checks = []
    for name in names:
        try:
            value = read_secret(name, environ, required=False)
            status = 'PRESENT_NOT_VALIDATED' if value and value.strip() else 'MISSING'
            value = None
        except (OSError, ValueError):
            # Exception text may contain a filename or secret; do not serialize it.
            status = 'INVALID_OR_UNREADABLE'
        checks.append({'name': name, 'status': status})
    present = all(c['status'] == 'PRESENT_NOT_VALIDATED' for c in checks)
    return {
        'protocol': 'fi.local-preflight.v1', 'role': role, 'provider': provider,
        'status': 'LOCAL_CONFIGURATION_PRESENT_UNVERIFIED' if present else 'BLOCKED_LOCAL_CONFIGURATION',
        'checks': checks, 'network_attempted': False, 'database_connected': False,
        'provider_verified': False, 'external_timestamp_verified': False,
        'production_acceptance': False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=sorted(ROLE_SECRETS), required=True)
    parser.add_argument('--provider', choices=sorted(PROVIDER_KEYS))
    args = parser.parse_args(argv)
    if (args.role == 'worker') != (args.provider is not None):
        parser.error('--provider is required only for --role worker')
    result = inspect_configuration(args.role, args.provider)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'LOCAL_CONFIGURATION_PRESENT_UNVERIFIED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
