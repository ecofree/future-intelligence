"""The operator path passes explicit CRL bytes and preserves legacy profiles."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from future_intelligence.delivery.__main__ import main
from future_intelligence.delivery.timestamps import CRLTrustProfile, MAX_PROOF, TrustProfile


class VerificationCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.ca=self.root/'ca.pem';self.ca.write_bytes(b'TEST ONLY CA INPUT')
        self.crl=self.root/'crl.pem';self.crl.write_bytes(b'TEST ONLY CRL INPUT')
        self.profile_file=self.root/'profile.json'
        self.old=TrustProfile('a'*64,('1.2.3',),fixture=True)
        self.profile=CRLTrustProfile(**asdict(self.old),
            revocation_policy='offline-full-chain-crl-v1',max_crl_age_seconds=3600)

    def invoke(self,profile,*extra):
        self.profile_file.write_text(json.dumps(asdict(profile)))
        args=['delivery','verify-anchor','fixture-request','--ca',str(self.ca),
              '--profile',str(self.profile_file),*extra]
        with patch('sys.argv',args),patch.dict('os.environ',{'FI_VERIFIER_DATABASE_URL':'fixture-dsn'}), \
             patch('future_intelligence.delivery.__main__.DeliveryRepository') as repo:
            result=main()
        return repo,result

    def test_crl_profile_and_raw_bytes_reach_isolated_verifier(self):
        repo,_=self.invoke(self.profile,'--crl',str(self.crl))
        repo.assert_called_once_with('fixture-dsn')
        repo.return_value.verify_anchor.assert_called_once_with(
            'fixture-request',self.ca.read_bytes(),self.profile,crl_bundle=self.crl.read_bytes())

    def test_legacy_profile_stays_five_fields_and_without_crl(self):
        repo,_=self.invoke(self.old)
        repo.return_value.verify_anchor.assert_called_once_with(
            'fixture-request',self.ca.read_bytes(),self.old,crl_bundle=None)

    def test_oversized_crl_is_rejected_before_any_verification_write(self):
        self.crl.write_bytes(b'x'*(MAX_PROOF+1))
        with self.assertRaisesRegex(ValueError,'CRL bundle size'):
            self.invoke(self.profile,'--crl',str(self.crl))
