"""One non-experimental APED sample for a public Git revision, without credentials."""
import hashlib
import json
import os
from pathlib import Path
import re

from future_intelligence.delivery.timestamps import imprint, integer, make_query, request_reply, sequence
from future_intelligence.public_https import probe_egress


HOST = 'timestamp.aped.gov.gr'


def public_fixture_query(value: str) -> tuple[bytes, str]:
    """Only an ordinary public test imprint, 64-bit nonce and certReq may transit CI."""
    if re.fullmatch(r'[0-9a-f]{112,144}', value) is None:
        raise ValueError('invalid public fixture query encoding')
    query = bytes.fromhex(value)
    fields = sequence(query)
    if (len(fields) != 4 or fields[0] != (2,b'\x01') or fields[2][0] != 2
            or not 0 < integer(fields[2][1]) < 2**64 or fields[3] != (1,b'\xff')):
        raise ValueError('unexpected public fixture query fields')
    return query, imprint(fields[1]).hex()


def sample(directory: Path, revision: str, fixture_hex: str = '') -> bool:
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('public Git revision required')
    directory.mkdir(mode=0o700)  # Refuse to replace an earlier attempt.
    if fixture_hex:
        query, subject_hash = public_fixture_query(fixture_hex)
    else:
        subject = f'Non-experimental APED review of ecofree/future-intelligence at {revision}\n'.encode()
        (directory / 'subject.txt').write_bytes(subject)
        query, subject_hash = None, hashlib.sha256(subject).hexdigest()
    report = {'subject_sha256': subject_hash, 'revision': revision,
              'subject_kind': 'PUBLIC_QA_FIXTURE' if fixture_hex else 'PUBLIC_REVISION',
              'production_acceptance': False, 'query_created': False, 'reply_received': False}
    try:
        egress = probe_egress(HOST)
        (directory / 'egress.json').write_text(json.dumps(egress), encoding='utf-8')
        if egress['tls_passed']:
            if query is None:
                query = make_query(report['subject_sha256'])
            (directory / 'request.tsq').write_bytes(query)
            report['query_created'] = True
            reply = request_reply(f'https://{HOST}/qtss', query, allowed_host=HOST)
            (directory / 'reply.tsr').write_bytes(reply)
            report.update(reply_received=True, reply_sha256=hashlib.sha256(reply).hexdigest())
    except (OSError, ValueError, RuntimeError) as exc:
        report['error'] = type(exc).__name__
    (directory / 'receipt.json').write_text(json.dumps(report), encoding='utf-8')
    print(json.dumps(report))
    return report['reply_received']


if __name__ == '__main__':
    raise SystemExit(0 if sample(Path('/review/sample'), os.environ['PUBLIC_REVISION'],
                                 os.environ.get('PUBLIC_FIXTURE_QUERY','')) else 2)
