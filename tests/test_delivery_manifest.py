import copy
import hashlib
import json
import unittest
from uuid import uuid4
from future_intelligence.delivery.manifest import validate_manifest

class ManifestTests(unittest.TestCase):
    def data(self):
        version=str(uuid4());q=json.dumps(dict(id=version,question_id=str(uuid4()),spec=dict(question_type='binary')))
        sha=hashlib.sha256(q.encode()).hexdigest()
        original=dict(protocol='fi.experiment-manifest.v1',questions=[dict(version_id=version,snapshot_sha256=sha)],
                      entries=[dict(entry_key='a'),dict(entry_key='b')],spec=dict(run_kind='FIXTURE',suite='A'))
        return [dict(experiment_manifest=json.dumps(original),questions=[dict(version_id=version,snapshot_text=q,snapshot_sha256=sha)],run_kind='FIXTURE',suite='A'),
                dict(question_version_id=version,entry_key='a',submission=dict(probabilities_ppm=[500000,500000])),
                dict(question_version_id=version,entry_key='b',submission=None)]
    def test_full_cartesian_set(self):self.assertEqual(len(validate_manifest(self.data())),1)
    def test_snapshot_does_not_match_hash(self):
        rows=self.data();rows[0]['questions'][0]['snapshot_text']+=' '
        with self.assertRaises(ValueError):validate_manifest(rows)
    def test_omission_even_after_resealing(self):
        rows=self.data();rows.pop()
        with self.assertRaises(ValueError):validate_manifest(rows)
    def test_duplicate_entry_cannot_replace_missing(self):
        rows=self.data();rows[-1]['entry_key']='a'
        with self.assertRaises(ValueError):validate_manifest(rows)
    def test_probability_valid_before_outcome_exists(self):
        rows=self.data();rows[1]['submission']['probabilities_ppm']=[1,2]
        with self.assertRaises(ValueError):validate_manifest(rows)
