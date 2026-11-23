"""One non-experimental APED sample for a public Git revision, without credentials."""
import hashlib
import json
import os
from pathlib import Path
import re

from future_intelligence.delivery.timestamps import make_query, request_reply
from future_intelligence.public_https import probe_egress


HOST = 'timestamp.aped.gov.gr'


def sample(directory: Path, revision: str) -> bool:
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('public Git revision required')
    directory.mkdir(mode=0o700)  # Refuse to replace an earlier attempt.
    subject = f'Non-experimental APED review of ecofree/future-intelligence at {revision}\n'.encode()
    (directory / 'subject.txt').write_bytes(subject)
    report = {'subject_sha256': hashlib.sha256(subject).hexdigest(), 'revision': revision,
              'production_acceptance': False, 'query_created': False, 'reply_received': False}
    try:
        egress = probe_egress(HOST)
        (directory / 'egress.json').write_text(json.dumps(egress), encoding='utf-8')
        if egress['tls_passed']:
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
    raise SystemExit(0 if sample(Path('/review/sample'), os.environ['PUBLIC_REVISION']) else 2)
