"""Independent offline verification; never trust a bundle's claimed verification."""
from __future__ import annotations

import base64
import json
from .batch import verify_pair, strict_json
from .timestamps import TrustProfile, verify_reply, utc, VerificationError
from .evaluation import score


def verify_export(data: dict, ca_pem: bytes, profile: TrustProfile) -> dict:
    if data.get('protocol') != 'fi.audit-export.v1':
        raise ValueError('unsupported export')
    registration, forecast = data['registration'], data['forecast']
    r, f = verify_pair(registration, forecast)
    registered_profile = r[0]['trust_profile']
    if json.loads(json.dumps(profile.__dict__)) != registered_profile:
        raise VerificationError('untrusted bundle cannot supply its own trust profile')
    manifest = strict_json(r[0]['experiment_manifest'])
    # P0-02 manifests contain the frozen spec. The deadline is not a caller override.
    spec = manifest.get('spec')
    if spec is None:
        raise ValueError('experiment manifest has no frozen spec')
    deadline = spec['forecast_deadline']
    bundles = {registration['batch_id']:registration, forecast['batch_id']:forecast}
    reports, upper = [], {}
    for a in data['anchors']:
        if a['batch_id'] not in bundles:
            raise ValueError('foreign anchor')
        if a['protocol'] != 'RFC3161' or not a.get('reply_b64'):
            reports.append({'id':a['id'],'status':'NOT_A_VERIFIED_PRECISE_TIME_PROOF'});continue
        try:
            report=verify_reply(bundles[a['batch_id']]['subject_sha256'],base64.b64decode(a['query_b64']),
                                base64.b64decode(a['reply_b64']),ca_pem,profile,deadline)
            reports.append({'id':a['id'],**report})
            if report['status']=='VERIFIED':
                time=utc(report['upper_time']); upper[a['batch_id']]=min(time,upper.get(a['batch_id'],time))
        except (ValueError,TypeError) as exc:
            reports.append({'id':a['id'],'status':'INVALID_PROOF','error_type':type(exc).__name__})
    has_time=len(upper)==2
    first_claim=f[0].get('first_attempt_at')
    preregistered=has_time and first_claim is not None and upper[registration['batch_id']]<=utc(first_claim)
    questions={q['version_id']:strict_json(q['snapshot_text'])['spec'] for q in f[0]['questions']}
    outcomes={o['version_id']:o for o in data.get('outcomes',[])}
    if len(outcomes)!=len(data.get('outcomes',[])):
        raise ValueError('duplicate outcomes')
    scored=[]
    for task in f[1:]:
        sub=task.get('submission'); outcome=outcomes.get(task['question_version_id'])
        result={'task_id':task['task_id'],'entry_key':task['entry_key'],'score':None}
        if sub is not None and outcome and outcome['status']=='RESOLVED':
            result['score']=score(questions[task['question_version_id']],sub['probabilities_ppm'],outcome['value_text'])
        scored.append(result)
    return dict(integrity='VERIFIED_COMPLETE_TASK_SET',registered_tasks=len(f)-1,
                precise_time_verified=bool(has_time),registration_precedes_first_attempt=bool(preregistered),
                time_eligible_live=bool(preregistered and not profile.fixture and f[0]['run_kind']=='LIVE'),
                fixture=profile.fixture,anchors=reports,independent_recalculation=scored,
                outcome_truth='REQUIRES_ORIGINAL_SOURCE_AND_BLIND_REVIEW',
                provider_authenticity='TRUSTED_RUNNER_NOT_CRYPTOGRAPHICALLY_PROVED')
