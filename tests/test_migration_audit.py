import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('migration_audit',Path(__file__).resolve().parents[1]/'scripts/audit_migrations.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)

class MigrationAuditTests(unittest.TestCase):
    def test_match_is_read_only_not_acceptance(self):
        result=audit.compare_manifests({'0001_test.sql':'a'*64},{'0001_test.sql':'a'*64})
        self.assertEqual(result['status'],'MATCH');self.assertTrue(result['read_only'])
        self.assertFalse(result['repair_performed']);self.assertFalse(result['production_acceptance'])
    def test_checksum_difference_is_preserved_and_blocked(self):
        old={'0001_test.sql':'b'*64};new={'0001_test.sql':'a'*64};r=audit.compare_manifests(new,old)
        self.assertEqual(r['status'],'BLOCKED_HISTORY_MISMATCH')
        self.assertEqual(r['checksum_mismatches'][0]['applied_sha256'],'b'*64)
        self.assertEqual(old,{'0001_test.sql':'b'*64})
    def test_unknown_history_not_deleted(self):
        r=audit.compare_manifests({'0001_test.sql':'a'*64},{'0001_test.sql':'a'*64,'0014_local.sql':'b'*64})
        self.assertEqual(r['unknown_applied'],['0014_local.sql']);self.assertFalse(r['repair_performed'])
    def test_two_0014_are_not_renamed_or_blanket_rejected(self):
        manifest={'0014_a.sql':'a'*64,'0014_b.sql':'b'*64,'0015_reconcile.sql':'c'*64}
        r=audit.compare_manifests(manifest,manifest)
        self.assertEqual(r['status'],'MATCH');self.assertEqual(r['duplicate_numeric_prefixes'],['0014'])
    def test_pending_retroactive_order_needs_review(self):
        r=audit.compare_manifests({'0014_a.sql':'a'*64,'0014_b.sql':'b'*64,'0015_c.sql':'c'*64},{'0014_b.sql':'b'*64})
        self.assertEqual(r['status'],'REVIEW_REQUIRED_ORDER');self.assertEqual(r['pending_before_last_applied'],['0014_a.sql'])
    def test_forward_pending_not_applied_by_audit(self):
        r=audit.compare_manifests({'0001_a.sql':'a'*64,'0002_b.sql':'b'*64},{'0001_a.sql':'a'*64})
        self.assertEqual(r['status'],'PENDING_FORWARD_MIGRATIONS');self.assertEqual(r['pending'],['0002_b.sql'])
    def test_invalid_manifest_rejected(self):
        for manifest in ({},{'../secret':'a'*64},{'0001_a.sql':'x'*64}):
            with self.assertRaises(ValueError):audit.compare_manifests(manifest,{})
    def test_missing_secret_no_connection_no_value_printed(self):
        with patch.dict(os.environ,{},clear=True),patch.object(audit,'audit',side_effect=AssertionError('must not connect')),contextlib.redirect_stdout(io.StringIO()) as stream:
            code=audit.main([])
        self.assertEqual(code,2);self.assertEqual(json.loads(stream.getvalue())['status'],'BLOCKED_MISSING_DSN')
    def test_connection_error_sanitized(self):
        with patch.dict(os.environ,{'FI_MIGRATION_DATABASE_URL':'SECRET_DSN'}),patch.object(audit,'audit',side_effect=RuntimeError('SECRET_DSN')),contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(audit.main([]),2)
        self.assertNotIn('SECRET_DSN',stream.getvalue());self.assertIn('BLOCKED_AUDIT_FAILED',stream.getvalue())


class MigrationHistoryOrderTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timedelta, timezone
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.later = self.now + timedelta(seconds=1)
        self.last = self.now + timedelta(seconds=2)
        self.manifest = {'0014_a.sql': 'a'*64, '0014_b.sql': 'b'*64,
                         '0015_reconcile.sql': 'c'*64}

    def rows(self, names):
        return [(name, self.manifest[name], moment)
                for name, moment in zip(names, (self.now, self.later, self.last))]

    def test_hash_only_comparison_does_not_claim_historical_order_check(self):
        result = audit.compare_manifests(self.manifest, self.manifest)
        self.assertEqual(result['historical_order_status'], 'NOT_CHECKED')

    def test_matching_hashes_with_reversed_applied_order_require_review(self):
        rows = self.rows(['0014_b.sql', '0014_a.sql', '0015_reconcile.sql'])
        result = audit.compare_history(self.manifest, rows)
        self.assertEqual(result['status'], 'REVIEW_REQUIRED_ORDER')
        self.assertEqual(result['checksum_mismatches'], [])
        self.assertEqual(result['pending'], [])
        self.assertEqual(result['historical_order_status'], 'NONCANONICAL_RECORDED_ORDER')
        pair = result['applied_order_inversions'][0]
        self.assertEqual((pair['earlier_version'], pair['later_version']), ('0014_b.sql', '0014_a.sql'))
        # A file named reconcile is not itself evidence of a reconciled effective schema.
        self.assertFalse(result['repair_performed'])

    def test_duplicate_numeric_prefixes_in_recorded_order_are_not_errors(self):
        result = audit.compare_history(self.manifest, self.rows(list(self.manifest)))
        self.assertEqual(result['status'], 'MATCH')
        self.assertEqual(result['duplicate_numeric_prefixes'], ['0014'])
        self.assertEqual(result['historical_order_status'], 'CONSISTENT_RECORDED_TIMESTAMPS')
        self.assertFalse(result['production_acceptance'])

    def test_identical_times_do_not_invent_an_execution_sequence(self):
        rows = [(n, h, self.now) for n, h in self.manifest.items()]
        result = audit.compare_history(self.manifest, rows)
        self.assertEqual(result['status'], 'REVIEW_REQUIRED_ORDER')
        self.assertEqual(result['historical_order_status'], 'INDETERMINATE_TIED_TIMESTAMPS')
        self.assertEqual(result['applied_order_inversions'], [])
        self.assertEqual(result['applied_timestamp_ties'][0]['versions'], sorted(self.manifest))

    def test_equal_instants_in_different_timezones_remain_tied(self):
        from datetime import timedelta, timezone
        local = self.now.astimezone(timezone(timedelta(hours=9)))
        rows = [('0014_a.sql', 'a'*64, self.now), ('0014_b.sql', 'b'*64, local)]
        result = audit.compare_history(self.manifest, rows)
        self.assertEqual(result['historical_order_status'], 'INDETERMINATE_TIED_TIMESTAMPS')
        self.assertEqual(result['status'], 'REVIEW_REQUIRED_ORDER')

    def test_nonadjacent_inversions_are_not_lost(self):
        result = audit.compare_history(self.manifest,
            self.rows(['0015_reconcile.sql', '0014_a.sql', '0014_b.sql']))
        self.assertEqual([x['later_version'] for x in result['applied_order_inversions']],
                         ['0014_a.sql', '0014_b.sql'])

    def test_checksum_mismatch_has_priority_over_order_warning(self):
        rows = self.rows(['0014_b.sql', '0014_a.sql', '0015_reconcile.sql'])
        rows[0] = (rows[0][0], 'd'*64, rows[0][2])
        result = audit.compare_history(self.manifest, rows)
        self.assertEqual(result['status'], 'BLOCKED_HISTORY_MISMATCH')
        self.assertTrue(result['applied_order_inversions'])
        self.assertEqual(result['checksum_mismatches'][0]['applied_sha256'], 'd'*64)

    def test_unsorted_input_not_mutated(self):
        rows = list(reversed(self.rows(list(self.manifest))))
        before = list(rows)
        result = audit.compare_history(self.manifest, rows)
        self.assertEqual(rows, before)
        self.assertEqual(result['status'], 'MATCH')
        self.assertEqual([r['version'] for r in result['applied_order']], sorted(self.manifest))

    def test_invalid_or_duplicate_history_rows_rejected(self):
        valid = ('0014_a.sql', 'a'*64, self.now)
        for rows in ([valid, valid], [('0014_a.sql', 'a'*64)],
                     [('0014_a.sql', 'a'*64, None)],
                     [('0014_a.sql', 'a'*64, self.now.replace(tzinfo=None))],
                     [('0014_a.sql', 'a'*64, '2026-01-01')]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                audit.compare_history(self.manifest, rows)

    def test_empty_history_and_pending_forward_migrations_are_distinct(self):
        empty = audit.compare_history(self.manifest, [])
        self.assertEqual(empty['status'], 'PENDING_FORWARD_MIGRATIONS')
        self.assertEqual(empty['historical_order_status'], 'EMPTY_HISTORY')
        normal = audit.compare_history(self.manifest, [('0014_a.sql', 'a'*64, self.now)])
        self.assertEqual(normal['status'], 'PENDING_FORWARD_MIGRATIONS')
        self.assertEqual(normal['historical_order_status'], 'CONSISTENT_RECORDED_TIMESTAMPS')

    def test_review_order_cli_exit_is_not_success(self):
        result = audit.compare_history(self.manifest,
            self.rows(['0014_b.sql', '0014_a.sql', '0015_reconcile.sql']))
        with patch.dict(os.environ, {'FI_MIGRATION_DATABASE_URL': 'synthetic-dsn'}), \
                patch.object(audit, 'audit', return_value=result), \
                contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(audit.main([]), 2)
        self.assertNotIn('synthetic-dsn', stream.getvalue())
        self.assertEqual(json.loads(stream.getvalue())['status'], 'REVIEW_REQUIRED_ORDER')

    def test_audit_uses_recorded_order_and_read_only_consistent_snapshot(self):
        import hashlib
        import tempfile
        import types
        statements = []
        with tempfile.TemporaryDirectory() as directory:
            manifest = {}
            for name in self.manifest:
                raw = ('-- synthetic inventory ' + name + '\n').encode()
                (Path(directory)/name).write_bytes(raw)
                manifest[name] = hashlib.sha256(raw).hexdigest()
            rows = [(name, manifest[name], moment) for name, _, moment in
                    self.rows(['0014_b.sql', '0014_a.sql', '0015_reconcile.sql'])]
            class Connection:
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def execute(self, statement):
                    statements.append(statement)
                    return types.SimpleNamespace(fetchone=lambda: (True,), fetchall=lambda: rows)
            module = types.SimpleNamespace(connect=lambda dsn: Connection())
            with patch.dict('sys.modules', {'psycopg': module}):
                result = audit.audit('synthetic-dsn-not-used', directory)
            self.assertEqual(result['status'], 'REVIEW_REQUIRED_ORDER')
            self.assertEqual(result['checksum_mismatches'], [])
            self.assertEqual(statements[0], 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            self.assertTrue(all(s.startswith(('SET ', 'SELECT ')) for s in statements))
            self.assertEqual(manifest, {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in Path(directory).iterdir()})
