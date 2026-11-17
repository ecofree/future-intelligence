"""Synthetic wheel checks; actual build/install acceptance runs separately in CI."""
import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile

spec = importlib.util.spec_from_file_location('fi_distribution_check', Path(__file__).resolve().parents[1] / 'scripts' / 'check_distribution.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'synthetic.whl'
        self.files = {'future_intelligence/__init__.py': b'__version__ = "2.0.1.dev1"\n',
                      'future_intelligence/db/migrations/0001_test.sql': b'-- synthetic\n'}
        self.snapshot = {'protocol': check.PROTOCOL, 'version': '2.0.1.dev1',
                         'files': {k: check.digest(v) for k, v in self.files.items()}}
        self.meta = 'future_intelligence-2.0.1.dev1.dist-info/METADATA'

    def build(self, files=None, version='2.0.1.dev1'):
        with zipfile.ZipFile(self.path, 'w') as z:
            for name, value in (self.files if files is None else files).items():
                z.writestr(name, value)
            z.writestr(self.meta, 'Name: future-intelligence\nVersion: '+version+'\n')

    def test_exact_source_passes(self):
        self.build()
        result = check.verify_wheel(self.snapshot, self.path)
        self.assertEqual(result['package_files'], 2)
        self.assertIs(result['production_acceptance'], False)

    def test_missing_migration_denied(self):
        self.build({'future_intelligence/__init__.py': self.files['future_intelligence/__init__.py']})
        with self.assertRaises(ValueError): check.verify_wheel(self.snapshot, self.path)

    def test_changed_source_denied(self):
        self.build({**self.files, 'future_intelligence/__init__.py': b'changed'})
        with self.assertRaises(ValueError): check.verify_wheel(self.snapshot, self.path)

    def test_secret_or_extra_file_denied(self):
        for name in ('future_intelligence/.env', 'deploy/.local-secrets/bootstrap.json', 'future_intelligence/extra.py'):
            self.build({**self.files, name: b'synthetic-private-marker'})
            with self.subTest(name=name), self.assertRaises(ValueError):
                check.verify_wheel(self.snapshot, self.path)

    def test_metadata_version_mismatch_denied(self):
        self.build(version='2.0.1.dev0')
        with self.assertRaises(ValueError): check.verify_wheel(self.snapshot, self.path)

    def test_traversal_and_noncanonical_names_denied(self):
        for name in ('../private', '/absolute', 'future_intelligence/../private', 'a\\b', 'a//b', './a'):
            self.build({**self.files, name: b'x'})
            with self.subTest(name=name), self.assertRaises(ValueError):
                check.verify_wheel(self.snapshot, self.path)

    def test_duplicate_member_denied(self):
        self.build()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.path, 'a') as z:
                z.writestr('future_intelligence/__init__.py', b'changed')
        with self.assertRaises(ValueError): check.verify_wheel(self.snapshot, self.path)

    def test_symlink_denied(self):
        self.build()
        item = zipfile.ZipInfo('future_intelligence/link.py')
        item.create_system = 3; item.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.path, 'a') as z: z.writestr(item, '/private')
        with self.assertRaises(ValueError): check.verify_wheel(self.snapshot, self.path)
