"""Offline arithmetic for the proposed November 2026 CPI question contract.

The caller supplies the Table 1 index as printed (three decimals). This module
does not fetch a release, verify its vintage, write an outcome, or score it. Its
output is a candidate for independent source and first-publication review.

The bin is computed from an exact fraction. The finite decimal is only an
adapter for the existing outcome value_text field; it is not BLS's published
one-decimal percentage change and is never used to decide the exact bin.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import re

from future_intelligence.delivery.evaluation import category


BASELINE_INDEX = '324.122'
BINARY_THRESHOLD_INDEX = '337.511'
BIN_EDGES = ('3.0', '3.5', '4.0', '4.5')
SERIALIZATION_DECIMAL_PLACES = 12
_BASELINE_THOUSANDTHS = 324122
_THRESHOLD_THOUSANDTHS = 337511
_INDEX_PATTERN = re.compile(r'(?:0|[1-9][0-9]*)\.[0-9]{3}')


def _decimal_serialization(value: Fraction) -> str:
    """Truncate toward zero at a fixed scale, without Decimal context or floats.

    For these three-decimal inputs and fixed boundaries, any nonzero distance
    to a boundary is at least 1 / (10 * 324122), well above 10**-12. We still
    check the serialized category before returning a usable candidate.
    """
    scale = 10 ** SERIALIZATION_DECIMAL_PLACES
    scaled = abs(value.numerator) * scale // value.denominator
    whole, fractional = divmod(scaled, scale)
    sign = '-' if value < 0 and scaled else ''
    return f'{sign}{whole}.{fractional:0{SERIALIZATION_DECIMAL_PLACES}d}'


def cpi_settlement_candidate(index_value_text: str) -> dict:
    """Derive binary and five-bin candidates under the fixed 2026M11 contract.

    Input is a positive canonical index string with exactly three decimals.
    It is an index level, not a percentage. Provenance must be checked outside
    this function; supplying a plausible number does not establish a release.
    """
    if not isinstance(index_value_text, str) or not _INDEX_PATTERN.fullmatch(index_value_text):
        raise ValueError('index must be a positive index string with exactly three decimal places')
    index_thousandths = int(index_value_text.replace('.', ''))
    if index_thousandths <= 0:
        raise ValueError('index must be greater than zero')

    yoy = Fraction(100 * (index_thousandths - _BASELINE_THOUSANDTHS), _BASELINE_THOUSANDTHS)
    bin_index = sum(yoy >= Fraction(edge) for edge in BIN_EDGES)
    value_text = _decimal_serialization(yoy)
    if len(value_text) > 100:
        raise ValueError('derived value exceeds the existing outcome value_text limit')
    if category({'question_type': 'binned_numeric', 'bin_edges': BIN_EDGES}, value_text) != bin_index:
        raise ArithmeticError('finite decimal serialization changed the exact outcome category')

    return {
        'contract': 'fi.cpi-2026m11.first-release.v2',
        'status': 'CANDIDATE_REQUIRES_INDEPENDENT_REVIEW',
        'source_verified': False,
        'first_release_verified': False,
        'publication_time_verified': False,
        'requires_independent_review': True,
        'series_id': 'CUUR0000SA0',
        'reference_period': '2026-11',
        'index_units': '1982-84=100; not seasonally adjusted',
        'index_value_text': index_value_text,
        'index_thousandths': index_thousandths,
        'binary': {
            'question_type': 'binary',
            'threshold_index_value_text': BINARY_THRESHOLD_INDEX,
            'comparison': 'INDEX_GREATER_THAN_OR_EQUAL',
            'value_text': str(int(index_thousandths >= _THRESHOLD_THOUSANDTHS)),
        },
        'yoy': {
            'question_type': 'binned_numeric',
            'formula': '100 * (index_value / 324.122 - 1)',
            'baseline_index_value_text': BASELINE_INDEX,
            'baseline_reference_period': '2025-11',
            'units': 'percent',
            'exact_numerator': yoy.numerator,
            'exact_denominator': yoy.denominator,
            'bin_edges': list(BIN_EDGES),
            'bin_index': bin_index,
            'value_text': value_text,
            'value_text_kind': 'DERIVED_SERIALIZATION_NOT_PUBLISHED_PERCENT_CHANGE',
            'serialization_decimal_places': SERIALIZATION_DECIMAL_PLACES,
            'serialization_rounding': 'TOWARD_ZERO',
            'serialization_category_verified': True,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Compute offline CPI-2026M11 candidates for independent review.')
    parser.add_argument('--index', required=True, help='Unadjusted All items index, exactly three decimals.')
    args = parser.parse_args(argv)
    try:
        candidate = cpi_settlement_candidate(args.index)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
