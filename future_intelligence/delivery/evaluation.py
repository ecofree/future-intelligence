"""Versioned probability scoring and cluster-aware exploratory comparisons.

Bootstrap resamples entire independent event clusters, not individual questions.
Sign-flip inference assumes independent clusters and a symmetric null difference.
No optional-stopping claim; sample-size calculations are planning approximations.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import math
import random
from statistics import NormalDist, mean, stdev

from future_intelligence.core.scoring import brier_score, log_loss, ranked_probability_score


def category(spec: dict, value: str) -> int:
    try:
        n = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('invalid outcome value') from exc
    if not n.is_finite():
        raise ValueError('nonfinite outcome')
    if spec['question_type'] == 'binary':
        if value not in ('0', '1'):
            raise ValueError('binary outcome must be 0 or 1')
        return int(value)
    if spec['question_type'] != 'binned_numeric':
        raise ValueError('unsupported question type')
    edges = [Decimal(x) for x in spec['bin_edges']]
    if not edges or any(not x.is_finite() for x in edges) or any(a >= b for a, b in zip(edges, edges[1:])):
        raise ValueError('invalid frozen bin boundaries')
    return sum(n >= edge for edge in edges)  # Lower-inclusive bins, matching P0-01.


def score(spec: dict, probabilities_ppm: list[int], value: str) -> dict:
    k = 2 if spec['question_type'] == 'binary' else len(spec['bin_edges']) + 1
    if (not isinstance(probabilities_ppm, list) or len(probabilities_ppm) != k
            or any(type(p) is not int or not 0 <= p <= 1_000_000 for p in probabilities_ppm)
            or sum(probabilities_ppm) != 1_000_000):
        raise ValueError('invalid frozen distribution; never repair probabilities')
    y = category(spec, value)
    probs = [p / 1_000_000 for p in probabilities_ppm]
    uniform = [(1_000_000 // k + (i < 1_000_000 % k)) / 1_000_000 for i in range(k)]
    result = dict(metric='normalized_rps', primary=ranked_probability_score(probs, y),
                  baseline=ranked_probability_score(uniform, y), observed_category=y, log_loss=None)
    if spec['question_type'] == 'binary':
        ll = log_loss(probs[1], y)
        result.update(metric='brier', primary=brier_score(probs[1], y),
                      log_loss='Infinity' if math.isinf(ll) else ll)
    return result


def holm(pvalues: list[float]) -> list[float]:
    if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in pvalues):
        raise ValueError('invalid p values')
    result, previous = [0.0] * len(pvalues), 0.0
    for rank, i in enumerate(sorted(range(len(pvalues)), key=lambda i: pvalues[i])):
        previous = min(1.0, max(previous, (len(pvalues) - rank) * pvalues[i]))
        result[i] = previous
    return result


def quantile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered)-1)*p; low = int(pos); high = min(low+1, len(ordered)-1)
    return ordered[low] + (ordered[high]-ordered[low]) * (pos-low)


def paired_clusters(pairs: list[tuple[str, float, float]], *, seed: int, resamples: int,
                    alpha: float = .05, min_clusters: int = 20) -> dict:
    if type(seed) is not int or type(resamples) is not int or not 200 <= resamples <= 10000 or not 0 < alpha < .5:
        raise ValueError('invalid frozen sampling plan')
    if type(min_clusters) is not int or min_clusters < 2:
        raise ValueError('invalid provisional-label threshold')
    groups = defaultdict(list)
    for cluster, a, b in pairs:
        if not isinstance(cluster, str) or not cluster or any(isinstance(x, bool) or not math.isfinite(float(x)) for x in (a,b)):
            raise ValueError('invalid paired cluster observation')
        groups[cluster].append(float(a)-float(b))
    arrays = [groups[k] for k in sorted(groups)]
    n = sum(map(len, arrays)); m = len(arrays)
    result = dict(algorithm='fi.cluster-comparison.v1', paired_questions=n, event_clusters=m,
                  status='PROVISIONAL' if m < min_clusters else 'EXPLORATORY_FIXED_BATCH',
                  mean_difference=None, confidence_interval=None, p_value=None,
                  inference_assumption='INDEPENDENT_CLUSTERS_SYMMETRIC_NULL', seed=seed, resamples=resamples)
    if not arrays:
        return result
    difference = sum(map(sum, arrays))/n; result['mean_difference'] = difference
    if m < 2:
        return result  # No fake precision from one event cluster.
    rng = random.Random(seed)
    sums, sizes = list(map(sum, arrays)), list(map(len, arrays))
    sampled = []
    for _ in range(resamples):
        indices = [rng.randrange(m) for _ in range(m)]
        sampled.append(sum(sums[i] for i in indices)/sum(sizes[i] for i in indices))
    result['confidence_interval'] = [quantile(sampled, alpha/2), quantile(sampled, 1-alpha/2)]
    # Exact clustered sign flips for small samples; plus-one MC correction otherwise.
    if m <= 12:
        stats = [abs(sum(sums[i]*(1 if mask & (1<<i) else -1) for i in range(m))/n) for mask in range(1<<m)]
        result['p_value'] = sum(x >= abs(difference)-1e-15 for x in stats)/len(stats)
    else:
        exceed = sum(abs(sum(s*rng.choice((-1,1)) for s in sums)/n) >= abs(difference)-1e-15 for _ in range(resamples))
        result['p_value'] = (exceed+1)/(resamples+1)
    return result


def compare_score_rows(rows: list[dict], plan: dict, *, diagnostic: bool = False) -> list[dict]:
    """Never mix cohort/metric, use full registration denominator, no missing imputation."""
    if plan.get('protocol') != 'fi.analysis.v1' or plan.get('stopping_rule') != 'FIXED_REGISTERED_BATCH':
        raise ValueError('preregistered fixed-batch analysis required')
    comparisons = plan['comparisons']
    canonical_pairs = []
    for pair in comparisons:
        if not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]:
            raise ValueError('invalid comparison')
        canonical_pairs.append(tuple(sorted(pair)))
    if len(set(canonical_pairs)) != len(canonical_pairs):
        raise ValueError('duplicate comparison family')
    by_cohort = defaultdict(dict)
    for row in rows:
        identity = (row['entry_key'], row['question_version_id'])
        key = (row['cohort_key'], row['metric'])
        if identity in by_cohort[key]:
            raise ValueError('duplicate score row')
        by_cohort[key][identity] = row
    column = 'diagnostic_score' if diagnostic else 'primary_score'
    output = []
    for (cohort, metric), index in sorted(by_cohort.items()):
        for a, b in comparisons:
            question_ids = sorted({qid for entry, qid in index if entry in (a,b)})
            pairs = []
            for qid in question_ids:
                left, right = index.get((a,qid)), index.get((b,qid))
                if not left or not right or left[column] is None or right[column] is None:
                    continue
                if left['event_cluster'] != right['event_cluster']:
                    raise ValueError('paired question assigned inconsistent event clusters')
                pairs.append((left['event_cluster'],float(left[column]),float(right[column])))
            result = paired_clusters(pairs,seed=plan['seed'],resamples=plan['resamples'],
                                     alpha=plan['alpha_ppm']/1_000_000,min_clusters=plan['min_clusters'])
            result.update(cohort_key=cohort,metric=metric,entries=[a,b],registered_questions=len(question_ids),
                          paired_coverage=len(pairs)/len(question_ids) if question_ids else 0,
                          basis='CONDITIONAL_DIAGNOSTIC' if diagnostic else 'TIMED_LIVE_ONLY')
            output.append(result)
    # One family for every predeclared pair x observed cohort, not cherry-picked winners.
    valid = [i for i,r in enumerate(output) if r['p_value'] is not None]
    adjusted = holm([r['p_value'] if r['p_value'] is not None else 1.0 for r in output])
    for i, result in enumerate(output):
        result['holm_p_value'] = adjusted[i] if i in valid else None
        result['interpretation'] = 'EXPLORATORY_ONLY_NOT_EQUALITY_OR_SUPERIORITY_PROOF'
    return output


def planned_sample_size(differences: list[float], target_difference: float, *, alpha=.05, power=.8) -> dict:
    if len(differences)<2 or any(not math.isfinite(x) for x in differences) or not math.isfinite(target_difference) or target_difference<=0:
        raise ValueError('pilot variance and positive target difference required')
    if not 0 < alpha < .5 or not .5 < power < 1:
        raise ValueError('invalid alpha or power')
    sd = stdev(differences)
    if sd == 0:
        return {'required_independent_units':None,'status':'DEGENERATE_PILOT_NOT_EVIDENCE_OF_CERTAINTY'}
    z = NormalDist().inv_cdf(1-alpha/2)+NormalDist().inv_cdf(power)
    return {'required_independent_units':math.ceil((z*sd/target_difference)**2),
            'status':'PLANNING_APPROXIMATION_NOT_STOPPING_RULE','pilot_sd':sd,'target_difference':target_difference}
