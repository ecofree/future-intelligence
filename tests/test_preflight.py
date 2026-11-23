"""Local-only preflight regressions. All secret values below are synthetic."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from future_intelligence.runtime.preflight import inspect_configuration, main, ROLE_SECRETS, PROVIDER_KEYS
from future_intelligence.runtime.secrets import read_secret
from secret_fixture import file_symlink, grant_test_access


class PreflightTests(unittest.TestCase):
    def test_missing_is_blocked(self):
        result = inspect_configuration('worker', 'openai', {})
        self.assertEqual(result['status'], 'BLOCKED_LOCAL_CONFIGURATION')
        self.assertEqual([c['status'] for c in result['checks']], ['MISSING', 'MISSING'])

    def test_presence_never_means_verified(self):
        env = {'FI_WORKER_DATABASE_URL': 'synthetic-not-a-real-dsn', 'OPENAI_API_KEY': 'synthetic-key'}
        with patch('socket.socket', side_effect=AssertionError('no networking')):
            result = inspect_configuration('worker', 'openai', env)
        self.assertEqual(result['status'], 'LOCAL_CONFIGURATION_PRESENT_UNVERIFIED')
        for key in ('network_attempted', 'database_connected', 'provider_verified',
                    'external_timestamp_verified', 'production_acceptance'):
            self.assertIs(result[key], False)
        self.assertNotIn('synthetic-key', json.dumps(result))
        self.assertNotIn('synthetic-not-a-real-dsn', json.dumps(result))

    def test_all_roles_only_inspect_their_inputs(self):
        for role, names in ROLE_SECRETS.items():
            provider = 'google' if role == 'worker' else None
            env = {name: 'synthetic' for name in names}
            if provider:
                env[PROVIDER_KEYS[provider]] = 'synthetic'
            env['UNRELATED_SECRET_FILE'] = '/never/read/this'
            self.assertEqual(inspect_configuration(role, provider, env)['status'],
                             'LOCAL_CONFIGURATION_PRESENT_UNVERIFIED')

    def test_three_provider_key_names(self):
        for provider, name in PROVIDER_KEYS.items():
            result = inspect_configuration('worker', provider, {'FI_WORKER_DATABASE_URL': 'synthetic', name: 'synthetic'})
            self.assertEqual(result['checks'][1]['name'], name)
            self.assertEqual(result['checks'][1]['status'], 'PRESENT_NOT_VALIDATED')

    def test_no_implicit_provider_or_foreign_role(self):
        for role, provider in [('worker', None), ('worker', 'unknown'), ('dashboard', 'openai'), ('admin', None)]:
            with self.subTest(role=role, provider=provider), self.assertRaises(ValueError):
                inspect_configuration(role, provider, {})

    def test_ambiguous_source_is_not_silently_selected(self):
        env = {'FI_OPERATOR_DATABASE_URL': 'synthetic', 'FI_OPERATOR_DATABASE_URL_FILE': '/private/location'}
        result = inspect_configuration('operator', environ=env)
        self.assertEqual(result['checks'][0]['status'], 'INVALID_OR_UNREADABLE')
        self.assertNotIn('/private/location', json.dumps(result))

    def test_unreadable_path_not_in_report(self):
        result = inspect_configuration('operator', environ={'FI_OPERATOR_DATABASE_URL_FILE': '/missing/private-name'})
        self.assertEqual(result['status'], 'BLOCKED_LOCAL_CONFIGURATION')
        self.assertNotIn('private-name', json.dumps(result))

    def test_private_file_and_no_value_in_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'private'
            path.write_text('synthetic-local-secret\n'); grant_test_access(path)
            result = inspect_configuration('operator', environ={'FI_OPERATOR_DATABASE_URL_FILE': str(path)})
            self.assertEqual(result['checks'][0]['status'], 'PRESENT_NOT_VALIDATED')
            self.assertNotIn(str(path), json.dumps(result))
            self.assertNotIn('synthetic-local-secret', json.dumps(result))

    def test_empty_insecure_and_directory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file'
            path.touch(mode=0o600)
            for target in (path, Path(tmp)):
                self.assertEqual(inspect_configuration('operator', environ={'FI_OPERATOR_DATABASE_URL_FILE': str(target)})['checks'][0]['status'], 'INVALID_OR_UNREADABLE')
            path.write_text('synthetic'); grant_test_access(path, public=True)
            self.assertEqual(inspect_configuration('operator', environ={'FI_OPERATOR_DATABASE_URL_FILE': str(path)})['checks'][0]['status'], 'INVALID_OR_UNREADABLE')

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file'
            path.write_text('synthetic'); grant_test_access(path)
            link = Path(tmp) / 'link'; file_symlink(link, path)
            self.assertEqual(inspect_configuration('operator', environ={'FI_OPERATOR_DATABASE_URL_FILE': str(link)})['checks'][0]['status'], 'INVALID_OR_UNREADABLE')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO regression')
    def test_named_pipe_rejected_without_waiting_for_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pipe'; os.mkfifo(path, 0o600)
            env = dict(os.environ)
            env.pop('FI_OPERATOR_DATABASE_URL', None)
            env['FI_OPERATOR_DATABASE_URL_FILE'] = str(path)
            completed = subprocess.run([sys.executable, '-m', 'future_intelligence.runtime.preflight', '--role', 'operator'],
                                       env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(json.loads(completed.stdout)['checks'][0]['status'], 'INVALID_OR_UNREADABLE')
            self.assertEqual(completed.stderr, '')

    def test_cli_exit_code_not_live_acceptance(self):
        for env, code in [({}, 2), ({'FI_OPERATOR_DATABASE_URL': 'synthetic'}, 0)]:
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--role', 'operator']), code)
            self.assertIs(json.loads(output.getvalue())['production_acceptance'], False)

    def test_whitespace_and_invalid_utf8_block(self):
        self.assertEqual(inspect_configuration('operator', environ={'FI_OPERATOR_DATABASE_URL': '  '})['checks'][0]['status'], 'MISSING')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad'; path.write_bytes(b'\xff'); grant_test_access(path)
            self.assertEqual(inspect_configuration('operator', environ={'FI_OPERATOR_DATABASE_URL_FILE': str(path)})['checks'][0]['status'], 'INVALID_OR_UNREADABLE')
