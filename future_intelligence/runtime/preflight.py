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


COMPATIBLE = 'openai_compatible'


def inspect_configuration(role: str, provider: str | None = None,
                          environ: Mapping[str, str] | None = None,
                          endpoint: str | None = None) -> dict:
    """Inspect only explicitly named role inputs; never return values or paths.

    For ``openai_compatible`` the key variable comes from the named endpoint
    profile in the local registry (presets plus FI_COMPATIBLE_PROVIDERS_FILE).
    """
    if role not in ROLE_SECRETS:
        raise ValueError('unknown role')
    allowed = tuple(PROVIDER_KEYS) + (COMPATIBLE,)
    if (role == 'worker' and provider not in allowed) or (role != 'worker' and provider is not None):
        raise ValueError('provider is required only for worker')
    if (provider == COMPATIBLE) != (endpoint is not None):
        raise ValueError('endpoint is required only for openai_compatible')
    if provider == COMPATIBLE:
        from future_intelligence.providers.compatible import EndpointError, endpoint as lookup
        try:
            key_name = lookup(endpoint, environ)['api_key_env']
        except EndpointError:
            raise ValueError('unknown or invalid endpoint profile') from None
    else:
        key_name = PROVIDER_KEYS[provider] if provider else None
    names = ROLE_SECRETS[role] + ((key_name,) if key_name else ())
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
        **({'endpoint': endpoint} if endpoint is not None else {}),
        'status': 'LOCAL_CONFIGURATION_PRESENT_UNVERIFIED' if present else 'BLOCKED_LOCAL_CONFIGURATION',
        'checks': checks, 'network_attempted': False, 'database_connected': False,
        'provider_verified': False, 'external_timestamp_verified': False,
        'production_acceptance': False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=sorted(ROLE_SECRETS), required=True)
    parser.add_argument('--provider', choices=sorted(PROVIDER_KEYS) + [COMPATIBLE])
    parser.add_argument('--endpoint', help='registered endpoint name; only with --provider openai_compatible')
    args = parser.parse_args(argv)
    if (args.role == 'worker') != (args.provider is not None):
        parser.error('--provider is required only for --role worker')
    if (args.provider == COMPATIBLE) != (args.endpoint is not None):
        parser.error('--endpoint is required only for --provider openai_compatible')
    try:
        result = inspect_configuration(args.role, args.provider, endpoint=args.endpoint)
    except ValueError:
        parser.error('unknown or invalid endpoint profile')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'LOCAL_CONFIGURATION_PRESENT_UNVERIFIED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
