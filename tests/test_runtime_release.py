import copy
from datetime import datetime,timezone
import hashlib
import json
import unittest
from future_intelligence.runtime.release import draft_bls_question

class ReleaseDraftTests(unittest.TestCase):
    def fixture(self):
        t=dict(series='CUUR0000SA0',year=2027,period='M01',release_family='CPI',
            forecast_deadline='2027-02-10T12:00:00Z',outcome_not_before='2027-02-10T13:30:00Z',
            resolution_deadline='2027-02-11T13:30:00Z',original_timezone='America/New_York',
            calendar_reference='https://www.bls.gov/schedule/news_release/cpi.htm',
            bin_edges=['100','200'],unit='index',review_note='Synthetic calendar checked only for unit test')
        doc={'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':t['series'],'data':[]}]}}
        return t,doc
    def draft(self,t,d):
        return draft_bls_question(t,b'SYNTHETIC CALENDAR',json.dumps(d).encode(),now=datetime(2027,2,1,tzinfo=timezone.utc))
    def test_candidate_not_proof_unknown(self):
        t,d=self.fixture();r=self.draft(t,d)
        self.assertEqual(r['status'],'CANDIDATE_REQUIRES_INDEPENDENT_REVIEW')
        self.assertEqual(r['known_answer_check'],'NOT_FOUND_IN_THIS_SNAPSHOT_ONLY')
        self.assertEqual(r['calendar_sha256'],hashlib.sha256(b'SYNTHETIC CALENDAR').hexdigest())
    def test_known_target_rejected_from_actual_document(self):
        t,d=self.fixture();d['Results']['series'][0]['data']=[{'year':'2027','period':'M01','value':'150'}]
        with self.assertRaises(ValueError):self.draft(t,d)
    def test_error_response_not_unknown(self):
        t,d=self.fixture();d['status']='REQUEST_FAILED'
        with self.assertRaises(ValueError):self.draft(t,d)
    def test_wrong_series_not_unknown(self):
        t,d=self.fixture();d['Results']['series'][0]['seriesID']='DIFFERENT'
        with self.assertRaises(ValueError):self.draft(t,d)
    def test_shared_release_cluster_not_series(self):
        t,d=self.fixture();first=self.draft(t,d)
        t['series']='CUUR0000SA0L1E';d['Results']['series'][0]['seriesID']=t['series']
        self.assertEqual(first['spec']['event_cluster'],self.draft(t,d)['spec']['event_cluster'])
    def test_no_ambiguous_calendar_url(self):
        t,d=self.fixture()
        for url in ['http://www.bls.gov/a','https://evil.invalid/a','https://x@www.bls.gov/a','https://www.bls.gov/a?other=1']:
            with self.subTest(url=url):
                bad=copy.deepcopy(t);bad['calendar_reference']=url
                with self.assertRaises(ValueError):self.draft(bad,d)
    def test_empty_calendar_rejected(self):
        t,d=self.fixture()
        with self.assertRaises(ValueError):draft_bls_question(t,b'',json.dumps(d).encode())
