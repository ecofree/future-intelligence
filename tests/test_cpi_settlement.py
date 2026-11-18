from decimal import Decimal, Inexact, localcontext, Rounded, ROUND_UP
from fractions import Fraction
import json
import subprocess
import sys
import unittest

from future_intelligence.delivery.cpi import BIN_EDGES, cpi_settlement_candidate
from future_intelligence.delivery.evaluation import category


class CpiSettlementTests(unittest.TestCase):
    # All index arguments below are synthetic arithmetic fixtures, not a claim
    # that BLS has published the November 2026 outcome.
    def test_positive_three_decimal_index_only(self):
        for value in ('0.001', '1.000', '324.122', '337.511'):
            with self.subTest(value=value):
                self.assertEqual(cpi_settlement_candidate(value)['index_value_text'], value)
        invalid = (None, True, 337.511, 337, '', '337', '337.51', '337.5110',
                   '0.000', '-0.001', '+337.511', ' 337.511', '337.511 ',
                   '337.511\n', '0337.511', '.511', '3.37511e2', '337,511',
                   'NaN', 'Infinity', '３３７.５１１', '4.0%')
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                cpi_settlement_candidate(value)

    def test_binary_threshold_equality_is_yes(self):
        for value, expected in (('337.510', '0'), ('337.511', '1'), ('337.512', '1')):
            with self.subTest(value=value):
                self.assertEqual(cpi_settlement_candidate(value)['binary']['value_text'], expected)

    def test_four_exact_boundaries_on_three_decimal_grid(self):
        # Exact index levels at 3%, 3.5%, 4%, 4.5% are respectively
        # 333.84566, 335.46627, 337.08688, 338.70749. None is on this grid.
        cases = (('333.845', 0), ('333.846', 1),
                 ('335.466', 1), ('335.467', 2),
                 ('337.086', 2), ('337.087', 3),
                 ('338.707', 3), ('338.708', 4))
        spec = {'question_type': 'binned_numeric', 'bin_edges': BIN_EDGES}
        for index, expected in cases:
            with self.subTest(index=index):
                yoy = cpi_settlement_candidate(index)['yoy']
                self.assertEqual(yoy['bin_index'], expected)
                self.assertEqual(category(spec, yoy['value_text']), expected)

    def test_exact_fraction_and_frozen_constants_are_preserved(self):
        result = cpi_settlement_candidate('337.511')
        yoy = result['yoy']
        self.assertEqual(result['index_thousandths'], 337511)
        self.assertEqual(result['binary']['threshold_index_value_text'], '337.511')
        self.assertEqual(yoy['baseline_index_value_text'], '324.122')
        self.assertEqual((yoy['exact_numerator'], yoy['exact_denominator']), (669450, 162061))
        self.assertEqual(yoy['bin_edges'], ['3.0', '3.5', '4.0', '4.5'])
        self.assertEqual(yoy['bin_index'], 3)

    def test_published_one_decimal_percentage_can_cross_a_boundary(self):
        below = cpi_settlement_candidate('337.086')['yoy']
        above = cpi_settlement_candidate('337.087')['yoy']
        self.assertEqual(Decimal(below['value_text']).quantize(Decimal('0.1')), Decimal('4.0'))
        self.assertEqual(Decimal(above['value_text']).quantize(Decimal('0.1')), Decimal('4.0'))
        self.assertEqual((below['bin_index'], above['bin_index']), (2, 3))
        self.assertNotEqual(category({'question_type': 'binned_numeric', 'bin_edges': BIN_EDGES}, '4.0'),
                            below['bin_index'])

    def test_decimal_global_context_does_not_change_arithmetic(self):
        values = ('0.001', '324.122', '333.845', '333.846', '335.466',
                  '335.467', '337.086', '337.087', '338.707', '338.708')
        expected = [cpi_settlement_candidate(value) for value in values]
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_UP
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            self.assertEqual([cpi_settlement_candidate(value) for value in values], expected)

    def test_raw_index_is_not_the_numeric_outcome(self):
        result = cpi_settlement_candidate('337.086')
        spec = {'question_type': 'binned_numeric', 'bin_edges': BIN_EDGES}
        self.assertEqual(category(spec, result['index_value_text']), 4)
        self.assertEqual(category(spec, result['yoy']['value_text']), 2)
        self.assertNotEqual(result['index_value_text'], result['yoy']['value_text'])
        self.assertEqual(result['yoy']['units'], 'percent')
        self.assertEqual(result['yoy']['value_text_kind'], 'DERIVED_SERIALIZATION_NOT_PUBLISHED_PERCENT_CHANGE')

    def test_zero_and_negative_yoy_serialization(self):
        zero = cpi_settlement_candidate('324.122')['yoy']
        self.assertEqual((zero['exact_numerator'], zero['exact_denominator']), (0, 1))
        self.assertEqual(zero['value_text'], '0.000000000000')
        for index in ('0.001', '324.121', '324.123', '337.511'):
            with self.subTest(index=index):
                yoy = cpi_settlement_candidate(index)['yoy']
                exact = Fraction(yoy['exact_numerator'], yoy['exact_denominator'])
                serialized = Fraction(yoy['value_text'])
                self.assertLess(abs(serialized - exact), Fraction(1, 10**12))
                self.assertLessEqual(abs(serialized), abs(exact))
                self.assertRegex(yoy['value_text'], r'^-?[0-9]+\.[0-9]{12}$')
                self.assertTrue(yoy['serialization_category_verified'])

    def test_candidate_does_not_claim_first_release_or_source_verification(self):
        result = cpi_settlement_candidate('337.511')
        self.assertEqual(result['status'], 'CANDIDATE_REQUIRES_INDEPENDENT_REVIEW')
        self.assertTrue(result['requires_independent_review'])
        for flag in ('source_verified', 'first_release_verified', 'publication_time_verified'):
            self.assertIs(result[flag], False)
        self.assertEqual(result['series_id'], 'CUUR0000SA0')
        self.assertEqual(result['reference_period'], '2026-11')

    def test_serialized_value_must_fit_existing_database_limit(self):
        with self.assertRaisesRegex(ValueError, 'value_text limit'):
            cpi_settlement_candidate('9' * 100 + '.000')

    def test_cli_emits_only_an_unverified_candidate(self):
        process = subprocess.run([sys.executable, '-m', 'future_intelligence.delivery.cpi',
                                  '--index', '337.086'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['yoy']['bin_index'], 2)
        self.assertFalse(result['first_release_verified'])

    def test_cli_rejects_bad_input_without_a_candidate(self):
        process = subprocess.run([sys.executable, '-m', 'future_intelligence.delivery.cpi',
                                  '--index', '4.0'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, '')
        self.assertIn('three decimal places', process.stderr)
