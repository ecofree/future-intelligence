"""Cross-check disclosed question snapshots and the full experiment Cartesian set."""
import hashlib
from uuid import UUID
from .batch import strict_json


def validate_manifest(records: list[dict]) -> dict:
    header=records[0];manifest=strict_json(header['experiment_manifest'])
    if manifest.get('protocol')!='fi.experiment-manifest.v1':
        raise ValueError('unsupported experiment manifest')
    refs=manifest['questions'];entries=manifest['entries'];questions=header['questions']
    ref_by_id={r['version_id']:r['snapshot_sha256'] for r in refs}
    entry_keys={e['entry_key'] for e in entries}
    if len(ref_by_id)!=len(refs) or len(entry_keys)!=len(entries) or not refs or not entries:
        raise ValueError('duplicate or empty preregistration')
    specs={};logical=set()
    for q in questions:
        version=q['version_id'];snapshot=strict_json(q['snapshot_text'])
        if str(UUID(version))!=version or version in specs:
            raise ValueError('duplicate or invalid question identity')
        hashed=hashlib.sha256(q['snapshot_text'].encode()).hexdigest()
        if hashed!=q['snapshot_sha256'] or hashed!=ref_by_id.get(version) or snapshot['id']!=version:
            raise ValueError('question definition does not match original registration')
        if snapshot['question_id'] in logical:
            raise ValueError('two versions of one logical question')
        logical.add(snapshot['question_id']);specs[version]=snapshot['spec']
    if set(specs)!=set(ref_by_id):
        raise ValueError('missing or extraneous question definitions')
    expected={(v,e) for v in specs for e in entry_keys}
    actual=[(t['question_version_id'],t['entry_key']) for t in records[1:]]
    if len(actual)!=len(expected) or set(actual)!=expected:
        raise ValueError('disclosed tasks are not the full registered question-model set')
    if header['run_kind']!=manifest['spec']['run_kind'] or header['suite']!=manifest['spec']['suite']:
        raise ValueError('experiment classification mismatch')
    for task in records[1:]:
        sub=task.get('submission')
        if sub is None:continue
        q=specs[task['question_version_id']]
        k=2 if q['question_type']=='binary' else len(q['bin_edges'])+1
        values=sub['probabilities_ppm']
        if len(values)!=k or any(type(p) is not int or not 0<=p<=1000000 for p in values) or sum(values)!=1000000:
            raise ValueError('invalid committed probability distribution')
    return specs
