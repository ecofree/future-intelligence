import math
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from future_intelligence.core.scoring import brier_score, log_loss, ranked_probability_score
from future_intelligence.core.commitments import (
    canonical_bytes, record_hash, make_commitment, merkle_root_and_proofs,
    verify_inclusion, InclusionProof, ProofStep,
)
from future_intelligence.providers.base import ForecastRequest, ForecastResponse, ForecastProvider


class ScoringTests(unittest.TestCase):
    def test_brier_hand_example(self):
        self.assertAlmostEqual(brier_score(0.7, 1), 0.09)

    def test_brier_extremes(self):
        self.assertEqual(brier_score(0, 0), 0)
        self.assertEqual(brier_score(1, 0), 1)

    def test_brier_symmetry(self):
        self.assertAlmostEqual(brier_score(0.2, 1), brier_score(0.8, 0))

    def test_log_loss_hand_example(self):
        self.assertAlmostEqual(log_loss(0.5, 1), math.log(2))

    def test_log_loss_does_not_hide_wrong_certainty(self):
        self.assertTrue(math.isinf(log_loss(0, 1)))
        self.assertTrue(math.isinf(log_loss(1, 0)))
        self.assertEqual(log_loss(1, 1), 0)
        self.assertEqual(log_loss(0, 0), 0)

    def test_invalid_probabilities(self):
        for p in (-0.1, 1.1, float('nan'), float('inf'), True, '0.5', None):
            with self.subTest(p=p), self.assertRaises(ValueError):
                brier_score(p, 1)

    def test_invalid_outcome(self):
        for y in (2, -1, True, 0.0, '1'):
            with self.subTest(y=y), self.assertRaises(ValueError):
                log_loss(0.5, y)

    def test_rps_hand_example(self):
        self.assertAlmostEqual(ranked_probability_score([0.2, 0.3, 0.5], 1), 0.145)

    def test_rps_binary_equals_brier(self):
        self.assertAlmostEqual(ranked_probability_score([0.3, 0.7], 1), brier_score(0.7, 1))

    def test_rps_deterministic(self):
        self.assertEqual(ranked_probability_score([0, 1, 0], 1), 0)
        self.assertEqual(ranked_probability_score([1, 0, 0], 2), 1)

    def test_invalid_rps(self):
        for values, index in (([1], 0), ([0.5, 0.4], 0), ([0.5, 0.5], 2), ([0.5, 0.5], True)):
            with self.subTest(values=values, index=index), self.assertRaises(ValueError):
                ranked_probability_score(values, index)


class CommitmentTests(unittest.TestCase):
    def test_canonical_key_order(self):
        self.assertEqual(canonical_bytes({'b': 2, 'a': 1}), b'{"a":1,"b":2}')

    def test_unicode(self):
        self.assertEqual(canonical_bytes({'题目': '通胀'}).decode(), '{"题目":"通胀"}')

    def test_float_and_nonjson_rejected(self):
        for value in ({'p': 0.5}, {'p': float('nan')}, {1: 'a'}, {'x': (1, 2)}, {'x': b'a'}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_bytes(value)

    def test_hash_stable_and_sensitive(self):
        self.assertEqual(record_hash({'b': 2, 'a': 1}), record_hash({'a': 1, 'b': 2}))
        self.assertNotEqual(record_hash({'a': 1}), record_hash({'a': 2}))

    def test_random_nonce_and_isolation(self):
        payload = {'p': 700000}
        e1, d1 = make_commitment(payload)
        e2, d2 = make_commitment(payload)
        self.assertEqual(len(e1['nonce']), 64)
        self.assertNotEqual(d1, d2)
        payload['p'] = 1
        self.assertEqual(e1['payload']['p'], 700000)

    def test_fixed_fixture_nonce(self):
        self.assertEqual(make_commitment({'p': 1}, nonce='ab' * 32),
                         make_commitment({'p': 1}, nonce='ab' * 32))

    def test_invalid_nonce(self):
        with self.assertRaises(ValueError):
            make_commitment({}, nonce='short')

    def test_empty_tree(self):
        with self.assertRaises(ValueError):
            merkle_root_and_proofs([])

    def test_even_odd_and_single_trees(self):
        for count in (1, 2, 3, 5, 6, 7, 16, 31):
            with self.subTest(count=count):
                digests = [record_hash({'id': i}) for i in range(count)]
                root, proofs = merkle_root_and_proofs(digests)
                for digest, proof in zip(digests, proofs):
                    self.assertTrue(verify_inclusion(digest, proof, root))
                    self.assertFalse(verify_inclusion(record_hash({'bad': 1}), proof, root))

    def test_tampered_root(self):
        d = record_hash({})
        root, proofs = merkle_root_and_proofs([d])
        self.assertFalse(verify_inclusion(d, proofs[0], '00' * 32))

    def test_bad_index(self):
        digests = [record_hash({'i': i}) for i in range(3)]
        root, proofs = merkle_root_and_proofs(digests)
        self.assertFalse(verify_inclusion(digests[0], replace(proofs[0], leaf_index=1), root))
        self.assertFalse(verify_inclusion(digests[0], replace(proofs[0], leaf_index=-1), root))

    def test_wrong_direction(self):
        digests = [record_hash({'i': i}) for i in range(2)]
        root, proofs = merkle_root_and_proofs(digests)
        step = replace(proofs[0].steps[0], sibling_on_left=True)
        self.assertFalse(verify_inclusion(digests[0], replace(proofs[0], steps=(step,)), root))

    def test_proof_length(self):
        d = record_hash({})
        root, proofs = merkle_root_and_proofs([d])
        bad = replace(proofs[0], steps=(ProofStep('00' * 32, False),))
        self.assertFalse(verify_inclusion(d, bad, root))
        root, proofs = merkle_root_and_proofs([d, d])
        self.assertFalse(verify_inclusion(d, replace(proofs[0], steps=()), root))

    def test_bad_hash_input(self):
        with self.assertRaises(ValueError):
            merkle_root_and_proofs(['not-a-sha256'])
        self.assertFalse(verify_inclusion('bad', InclusionProof(0, 1, ()), '00' * 32))


class ProviderContractTests(unittest.TestCase):
    def request(self, **kwargs):
        args = dict(task_id='t', question_version_id='qv', evidence_bundle_sha256='00' * 32,
                    model_id='unconfigured', prompt='fixture', deadline=datetime.now(timezone.utc),
                    max_output_tokens=100, budget_microusd=1000)
        args.update(kwargs)
        return ForecastRequest(**args)

    def test_timezone_required(self):
        with self.assertRaises(ValueError):
            self.request(deadline=datetime.now())

    def test_budget_and_limit(self):
        self.assertEqual(self.request().max_output_tokens, 100)
        with self.assertRaises(ValueError):
            self.request(budget_microusd=-1)

    def test_unknown_cost_preserved(self):
        r = ForecastResponse(500000, 'fixture', 'model', 'req', None, None, None, {})
        self.assertIsNone(r.cost_microusd)

    def test_invalid_response(self):
        with self.assertRaises(ValueError):
            ForecastResponse(1000001, '', '', '', None, None, None, {})
        with self.assertRaises(ValueError):
            ForecastResponse(500000, '', '', '', None, None, -1, {})

    def test_interface_cannot_fake_working_provider(self):
        with self.assertRaises(TypeError):
            ForecastProvider()


if __name__ == '__main__':
    unittest.main()
