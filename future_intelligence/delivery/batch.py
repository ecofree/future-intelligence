"""FI-BATCH-TEXT-v1: exact UTF-8 record bytes; hashes do not prove time or truth."""
from __future__ import annotations

import hashlib
import json
import secrets
from uuid import UUID

from future_intelligence.core.commitments import merkle_root_and_proofs

PROTOCOL = 'fi.batch-text.v1'


def digest(value: str) -> bytes:
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('expected lowercase SHA-256 hex')
    return bytes.fromhex(value)


def strict_json(text: str):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('duplicate JSON key')
            obj[key] = value
        return obj
    def reject(value):
        raise ValueError('non-finite JSON constant')
    return json.loads(text, object_pairs_hook=unique, parse_constant=reject)


def record_digest(text: str, nonce: str) -> str:
    if not isinstance(text, str) or len(text.encode('utf-8')) > 20_000_000:
        raise ValueError('invalid record size')
    strict_json(text)
    return hashlib.sha256(b'FI:batch-record:v1\0' + digest(nonce) + text.encode('utf-8')).hexdigest()


def subject_digest(batch_id: str, kind: str, root: str, count: int) -> str:
    if kind not in ('REGISTRATION', 'FORECAST') or type(count) is not int or not 2 <= count <= 5001:
        raise ValueError('invalid batch subject')
    value = (b'FI:batch-root:v1\0' + str(UUID(str(batch_id))).encode() + b'\0'
             + kind.encode() + b'\0' + digest(root) + b'\0' + str(count).encode())
    return hashlib.sha256(value).hexdigest()


def seal(batch_id: str, kind: str, records: list[str], nonces: list[str] | None = None) -> dict:
    if not isinstance(records, list) or not 2 <= len(records) <= 5001:
        raise ValueError('batch must contain manifest and at least one task')
    if sum(len(x.encode('utf-8')) for x in records) > 50_000_000:
        raise ValueError('oversized batch')
    nonces = [secrets.token_hex(32) for _ in records] if nonces is None else list(nonces)
    if len(nonces) != len(records) or len(set(nonces)) != len(nonces):
        raise ValueError('one independent nonce per record is required')
    hashes = [record_digest(text, nonce) for text, nonce in zip(records, nonces)]
    root, _ = merkle_root_and_proofs(hashes)
    return dict(protocol=PROTOCOL, batch_id=str(UUID(str(batch_id))), kind=kind,
                records=list(records), nonces=nonces, root_sha256=root,
                subject_sha256=subject_digest(batch_id, kind, root, len(records)))


def verify(batch: dict) -> list[dict]:
    if not isinstance(batch, dict) or set(batch) != {
        'protocol', 'batch_id', 'kind', 'records', 'nonces', 'root_sha256', 'subject_sha256'
    } or batch['protocol'] != PROTOCOL:
        raise ValueError('unsupported batch format')
    rebuilt = seal(batch['batch_id'], batch['kind'], batch['records'], batch['nonces'])
    if rebuilt != batch:
        raise ValueError('batch commitment mismatch')
    records = [strict_json(x) for x in batch['records']]
    if records[0].get('record_type') != 'MANIFEST':
        raise ValueError('missing manifest')
    tasks = records[1:]
    if any(t.get('record_type') != 'TASK' for t in tasks):
        raise ValueError('unexpected record type')
    ids = [t['task_id'] for t in tasks]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        raise ValueError('task ordering or uniqueness violated')
    if records[0]['task_count'] != len(tasks):
        raise ValueError('incomplete task batch')
    return records


def verify_pair(registration: dict, forecast: dict) -> tuple[list[dict], list[dict]]:
    r, f = verify(registration), verify(forecast)
    if registration['kind'] != 'REGISTRATION' or forecast['kind'] != 'FORECAST':
        raise ValueError('wrong batch kinds')
    for key in ('experiment_id', 'run_kind', 'suite', 'experiment_manifest', 'evidence_manifest',
                'questions', 'analysis_plan', 'trust_profile', 'task_count'):
        if r[0].get(key) != f[0].get(key):
            raise ValueError('registration/forecast manifest mismatch: ' + key)
    identity = ('task_id', 'question_version_id', 'entry_key', 'entry_config_sha256')
    if [[t.get(k) for k in identity] for t in r[1:]] != [[t.get(k) for k in identity] for t in f[1:]]:
        raise ValueError('forecast does not disclose complete registered task set')
    for task in f[1:]:
        sub = task.get('submission')
        if sub is not None and (sub.get('task_id') != task['task_id'] or
                                sub.get('run_kind') != f[0]['run_kind']):
            raise ValueError('submission identity mismatch')
    return r, f
