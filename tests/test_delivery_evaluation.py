from copy import deepcopy
import math
import random
import unittest

from future_intelligence.delivery.evaluation import score, category, holm, paired_clusters, compare_score_rows, planned_sample_size


class EvaluationTests(unittest.TestCase):
    def test_binary_hand_calculation(self):
        s=score({'question_type':'binary'},[300000,700000],'1')
        self.assertAlmostEqual(s['primary'],.09);self.assertAlmostEqual(s['baseline'],.25)
    def test_wrong_certainty_infinite_logloss(self):
        self.assertEqual(score({'question_type':'binary'},[1000000,0],'1')['log_loss'],'Infinity')
    def test_numeric_boundary(self):
        q={'question_type':'binned_numeric','bin_edges':['0','10']}
        self.assertEqual(category(q,'0'),1);self.assertEqual(category(q,'10'),2)
        self.assertEqual(category(q,'-0.00001'),0)
    def test_rps_hand_calculation(self):
        self.assertAlmostEqual(score({'question_type':'binned_numeric','bin_edges':['0','10']},[200000,300000,500000],'12')['primary'],(.2**2+.5**2)/2)
    def test_no_probability_repair(self):
        for p in ([1,2],[500000,True],[float('nan'),1],[500000,500000,0]):
            with self.subTest(p=p),self.assertRaises(ValueError):score({'question_type':'binary'},p,'1')
    def test_nonfinite_outcomes(self):
        for val in ('NaN','Infinity','-Infinity'):
            with self.assertRaises(ValueError):category({'question_type':'binary'},val)
    def test_holm_known_values(self):
        self.assertEqual(holm([.01,.04,.03]),[.03,.06,.06])
    def test_holm_unsorted_and_bound(self):
        self.assertEqual(holm([.9,.01,.5]),[1,.03,1]);self.assertEqual(holm([]),[])
    def test_holm_invalid(self):
        for values in ([math.nan],[1.1],[-.1],[True]):
            with self.assertRaises(ValueError):holm(values)
    def test_cluster_not_question_count(self):
        r=paired_clusters([('same-event',.1,.2)]*100,seed=1,resamples=200)
        self.assertEqual(r['event_clusters'],1);self.assertIsNone(r['confidence_interval']);self.assertIsNone(r['p_value'])
    def test_cluster_seed_reproducible(self):
        pairs=[(str(i//3),i/100,i/200) for i in range(30)]
        self.assertEqual(paired_clusters(pairs,seed=9,resamples=200),paired_clusters(pairs,seed=9,resamples=200))
    def test_identical_not_proof_of_equality(self):
        r=paired_clusters([(str(i),.2,.2) for i in range(10)],seed=2,resamples=200)
        self.assertEqual(r['p_value'],1);self.assertEqual(r['status'],'PROVISIONAL')
    def test_missing_counts_and_cohorts(self):
        rows=[dict(entry_key=e,question_version_id=str(i),cohort_key='binary',metric='brier',event_cluster=str(i),
                   primary_score=.1 if e=='a' else (None if i==1 else .2),diagnostic_score=.1) for i in range(2) for e in ('a','b')]
        plan=dict(protocol='fi.analysis.v1',stopping_rule='FIXED_REGISTERED_BATCH',comparisons=[['a','b']],seed=1,resamples=200,alpha_ppm=50000,min_clusters=20)
        r=compare_score_rows(rows,plan)[0]
        self.assertEqual(r['registered_questions'],2);self.assertEqual(r['paired_questions'],1);self.assertEqual(r['paired_coverage'],.5)
        rows[0]['cohort_key']='different';self.assertEqual(len(compare_score_rows(rows,plan)),2)
    def test_duplicate_comparisons_rejected(self):
        plan=dict(protocol='fi.analysis.v1',stopping_rule='FIXED_REGISTERED_BATCH',comparisons=[['a','b'],['b','a']])
        with self.assertRaises(ValueError):compare_score_rows([],plan)
    def test_planning_not_stopping(self):
        r=planned_sample_size([-.2,0,.2],.01)
        self.assertGreater(r['required_independent_units'],100);self.assertIn('NOT_STOPPING',r['status'])
    def test_zero_variance_not_certainty(self):
        self.assertIsNone(planned_sample_size([.1,.1],.01)['required_independent_units'])
    def test_optional_statsmodels_crosscheck(self):
        # Cross-check performed when the independent library is available, never a production dependency.
        try:from statsmodels.stats.multitest import multipletests
        except ImportError:return
        p=[random.Random(i).random() for i in range(20)]
        for a,b in zip(holm(p),multipletests(p,method='holm')[1]):self.assertAlmostEqual(a,b)
