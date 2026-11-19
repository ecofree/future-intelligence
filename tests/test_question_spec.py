import copy
from datetime import datetime, timedelta, timezone
import unittest

from future_intelligence.questions.spec import validate_spec, outcome_bin


def example_spec():
    now = datetime.now(timezone.utc)
    return {
        'title': 'Synthetic official release threshold',
        'event_definition': 'TEST FIXTURE ONLY, not a real prediction',
        'domain': 'MACRO', 'track': 'FAST', 'question_type': 'binary',
        'event_cluster': 'fixture-release-001', 'original_timezone': 'Asia/Tokyo',
        'forecast_deadline': (now + timedelta(days=2)).isoformat(),
        'outcome_not_before': (now + timedelta(days=3)).isoformat(),
        'resolution_deadline': (now + timedelta(days=4)).isoformat(),
        'resolution_rule': 'Use designated first release; missing data is not FALSE.',
        'data_revision_policy': 'FIRST_RELEASE', 'unit': 'boolean', 'bin_edges': [],
    }


class QuestionSpecTests(unittest.TestCase):
    def test_valid_roundtrip(self):
        spec = example_spec()
        self.assertEqual(validate_spec(validate_spec(spec)), validate_spec(spec))

    def test_isolated_copy(self):
        spec = example_spec()
        value = validate_spec(spec)
        value['bin_edges'].append('1')
        self.assertEqual(spec['bin_edges'], [])

    def test_required_and_unknown_fields(self):
        for key in example_spec():
            with self.subTest(key=key):
                spec = example_spec(); del spec[key]
                with self.assertRaises(ValueError): validate_spec(spec)
        spec = example_spec(); spec['locked_at'] = 'fake'
        with self.assertRaises(ValueError): validate_spec(spec)

    def test_naive_dates_rejected(self):
        for key in ('forecast_deadline','outcome_not_before','resolution_deadline'):
            spec = example_spec(); spec[key] = '2030-01-01T10:00:00'
            with self.assertRaises(ValueError): validate_spec(spec)

    def test_timezone(self):
        spec = example_spec(); spec['original_timezone'] = 'not/a-zone'
        with self.assertRaises(ValueError): validate_spec(spec)

    def test_time_order(self):
        spec = example_spec(); spec['forecast_deadline'] = (
            datetime.fromisoformat(spec['outcome_not_before']) + timedelta(microseconds=1)).isoformat()
        with self.assertRaises(ValueError): validate_spec(spec)

    def test_equivalent_time_offsets(self):
        a=example_spec(); b=copy.deepcopy(a)
        a['forecast_deadline']='2030-01-01T01:00:00+00:00'
        b['forecast_deadline']='2030-01-01T10:00:00+09:00'
        for v in (a,b):
            v['outcome_not_before']='2030-01-02T00:00:00Z'
            v['resolution_deadline']='2030-01-03T00:00:00Z'
        self.assertEqual(validate_spec(a), validate_spec(b))

    def test_empty_strings(self):
        for key in ('event_definition','resolution_rule','event_cluster','unit'):
            spec=example_spec(); spec[key]=' '
            with self.assertRaises(ValueError): validate_spec(spec)

    def test_classifications(self):
        for key,value in [('domain','GEO'),('track','unknown'),('question_type','time'),
                          ('data_revision_policy','whatever_is_best')]:
            spec=example_spec(); spec[key]=value
            with self.assertRaises(ValueError): validate_spec(spec)

    def test_numeric_bins(self):
        spec=example_spec(); spec.update(question_type='binned_numeric',unit='percent',bin_edges=['-1','0','2.5'])
        self.assertEqual(validate_spec(spec)['bin_edges'],['-1','0','2.5'])
        self.assertEqual([outcome_bin(x,spec['bin_edges']) for x in ['-2','-1','0','2.5','100']], [0,1,2,3,3])

    def test_invalid_bins(self):
        for edges in [[],['NaN'],['Infinity'],['2','1'],['1','1'],[1,2],None,['text']]:
            spec=example_spec(); spec.update(question_type='binned_numeric',bin_edges=edges)
            with self.subTest(edges=edges),self.assertRaises(ValueError): validate_spec(spec)

    def test_binary_no_bins(self):
        spec=example_spec(); spec['bin_edges']=['1']
        with self.assertRaises(ValueError): validate_spec(spec)

    def test_nonfinite_outcome(self):
        for value in ['NaN','Infinity','hello']:
            with self.assertRaises(ValueError): outcome_bin(value,['0','1'])

    def test_outcome_boundaries(self):
        for edges in [[],['2','1'],['1','1'],['NaN']]:
            with self.assertRaises(ValueError): outcome_bin('1',edges)


if __name__ == '__main__': unittest.main()
