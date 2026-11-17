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
