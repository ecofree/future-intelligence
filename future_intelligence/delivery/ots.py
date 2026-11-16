"""Optional official OTS CLI bridge. Bitcoin verification is not precise TSA time."""
from pathlib import Path
import hashlib
import subprocess
import tempfile
from urllib.parse import urlsplit
from .batch import digest


def stamp(subject_sha256: str, calendars: list[str]) -> bytes:
    # Explicit operator-configured calendars, never a URL supplied by a model.
    if not 1<=len(calendars)<=3:
        raise ValueError('one to three approved calendars required')
    for url in calendars:
        u=urlsplit(url)
        if u.scheme!='https' or u.hostname not in ('a.pool.opentimestamps.org','b.pool.opentimestamps.org','a.pool.eternitywall.com') or u.username or u.password or u.port not in (None,443):
            raise ValueError('unapproved OTS calendar')
    with tempfile.TemporaryDirectory() as tmp:
        file=Path(tmp)/'subject';file.write_bytes(digest(subject_sha256))
        args=['ots','--no-cache','stamp','--timeout','10','-m','1']
        for url in calendars:args+=['-c',url]
        result=subprocess.run(args+[str(file)],capture_output=True,timeout=35)
        proof=Path(str(file)+'.ots')
        if result.returncode or not proof.exists() or proof.stat().st_size>2_000_000:
            raise RuntimeError('OTS_STAMP_FAILED_OR_PENDING; never a confirmed Bitcoin proof')
        return proof.read_bytes()


def verify(subject_sha256: str, proof: bytes) -> dict:
    if not isinstance(proof,bytes) or not 0<len(proof)<=2_000_000:
        raise ValueError('invalid proof size')
    with tempfile.TemporaryDirectory() as tmp:
        data=Path(tmp)/'subject';data.write_bytes(digest(subject_sha256))
        path=Path(tmp)/'subject.ots';path.write_bytes(proof)
        # No remote calendar requests: only the locally configured Bitcoin node.
        try:
            result=subprocess.run(['ots','--no-cache','--no-default-whitelist','verify','-f',str(data),str(path)],
                                  capture_output=True,timeout=35)
        except (OSError,subprocess.TimeoutExpired):
            return {'status':'BLOCKED_VERIFIER_OR_NODE','qualifies_precise_deadline':False}
        return {'status':'VERIFIED_BITCOIN' if result.returncode==0 else 'PENDING_OR_INVALID',
                'proof_sha256':hashlib.sha256(proof).hexdigest(),'qualifies_precise_deadline':False}
