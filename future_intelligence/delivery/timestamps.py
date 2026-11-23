"""Bounded RFC3161 verification via OpenSSL; external trust is supplied separately.

No system CA fallback, no downloaded trust anchors, no network during verification.
OpenTimestamps evidence is independent and never substitutes for precise TSA time.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
from urllib.parse import urlsplit

from .batch import digest

UTC = timezone.utc
MAX_PROOF = 2_000_000
MAX_PROOF_BYTES = MAX_PROOF
SHA256_OID = bytes.fromhex('608648016503040201')
CRL_POLICY = 'offline-full-chain-crl-v1'
_TRUST_FIELDS = frozenset(('ca_sha256', 'policy_oids', 'max_accuracy_us', 'missing_accuracy_us', 'fixture'))
_CRL_TRUST_FIELDS = _TRUST_FIELDS | {'revocation_policy', 'max_crl_age_seconds'}
_CRL_PEM = re.compile(
    rb'-----BEGIN X509 CRL-----\r?\n(?P<body>(?:[A-Za-z0-9+/=]+\r?\n)+)-----END X509 CRL-----(?:\r?\n|\Z)'
)


class VerificationError(ValueError):
    pass


def utc(value: str | datetime) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise VerificationError('timezone required')
    return result.astimezone(UTC)


def command(args: list[str], *, timeout: int = 20) -> bytes:
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False,
                                env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError('verification executable failed or timed out') from exc
    if result.returncode or len(result.stdout) > MAX_PROOF:
        raise VerificationError('cryptographic verification failed')
    return result.stdout


def tlvs(data: bytes) -> list[tuple[int, bytes]]:
    """Strict, bounded DER TLV reader, not a general ASN.1 implementation."""
    if len(data) > MAX_PROOF:
        raise VerificationError('oversized DER')
    result, p = [], 0
    while p < len(data):
        if p + 2 > len(data):
            raise VerificationError('truncated DER')
        tag, length = data[p], data[p + 1]; p += 2
        if tag & 31 == 31:
            raise VerificationError('unsupported high-tag DER')
        if length & 128:
            n = length & 127
            if n == 0 or n > 4 or p + n > len(data) or data[p] == 0:
                raise VerificationError('invalid DER length')
            length = int.from_bytes(data[p:p+n]); p += n
            if length < 128:
                raise VerificationError('nonminimal DER length')
        if p + length > len(data):
            raise VerificationError('truncated DER value')
        result.append((tag, data[p:p+length])); p += length
    return result


def sequence(data: bytes) -> list[tuple[int, bytes]]:
    outer = tlvs(data)
    if len(outer) != 1 or outer[0][0] != 48:
        raise VerificationError('DER sequence required')
    return tlvs(outer[0][1])


def integer(value: bytes) -> int:
    if not value or value[0] & 128 or (len(value) > 1 and value[0] == 0 and not value[1] & 128):
        raise VerificationError('invalid nonnegative DER integer')
    return int.from_bytes(value)


def imprint(item: tuple[int, bytes]) -> bytes:
    if item[0] != 48:
        raise VerificationError('missing message imprint')
    fields = tlvs(item[1])
    if len(fields) != 2 or fields[0][0] != 48 or fields[1][0] != 4 or len(fields[1][1]) != 32:
        raise VerificationError('invalid message imprint')
    alg = tlvs(fields[0][1])
    if not alg or alg[0] != (6, SHA256_OID) or alg[1:] not in ([], [(5, b'')]):
        raise VerificationError('only SHA-256 timestamps are supported')
    return fields[1][1]


def oid_text(data: bytes) -> str:
    if not data:
        raise VerificationError('empty OID')
    arcs, acc = [], 0
    for b in data:
        acc = acc * 128 + (b & 127)
        if not b & 128:
            arcs.append(acc); acc = 0
    if data[-1] & 128 or not arcs:
        raise VerificationError('invalid OID')
    first = min(arcs[0] // 40, 2)
    return '.'.join(map(str, [first, arcs[0] - 40 * first] + arcs[1:]))


@dataclass(frozen=True)
class TrustProfile:
    ca_sha256: str
    policy_oids: tuple[str, ...]
    max_accuracy_us: int = 5_000_000
    missing_accuracy_us: int | None = None
    fixture: bool = False

    def validate(self) -> None:
        digest(self.ca_sha256)
        if not self.policy_oids or any(re.fullmatch(r'[0-2](?:\.[0-9]+)+', x) is None for x in self.policy_oids):
            raise VerificationError('explicit TSA policies required')
        for value in (self.max_accuracy_us, self.missing_accuracy_us):
            if value is not None and (type(value) is not int or not 0 <= value <= 60_000_000):
                raise VerificationError('invalid accuracy policy')
        if self.max_accuracy_us is None or type(self.fixture) is not bool:
            raise VerificationError('invalid trust profile')


@dataclass(frozen=True, kw_only=True)
class CRLTrustProfile(TrustProfile):
    """Explicit offline full-chain revocation policy; never inferred from v1."""
    revocation_policy: str
    max_crl_age_seconds: int

    def validate(self) -> None:
        super().validate()
        if self.revocation_policy != CRL_POLICY or type(self.max_crl_age_seconds) is not int \
                or not 1 <= self.max_crl_age_seconds <= 31_536_000:
            raise VerificationError('invalid offline CRL policy')


def trust_profile_from_dict(value: dict) -> TrustProfile:
    """Load one exact, versioned profile shape without adding fields to v1."""
    if not isinstance(value, dict) or value.keys() not in (_TRUST_FIELDS, _CRL_TRUST_FIELDS):
        raise VerificationError('trust profile requires exactly five or seven fields')
    if not isinstance(value['policy_oids'], (list, tuple)) \
            or any(not isinstance(oid, str) for oid in value['policy_oids']):
        raise VerificationError('explicit TSA policies required')
    values = {**value, 'policy_oids': tuple(value['policy_oids'])}
    cls = CRLTrustProfile if value.keys() == _CRL_TRUST_FIELDS else TrustProfile
    try:
        result = cls(**values)
        result.validate()
    except (TypeError, ValueError) as exc:
        raise VerificationError('invalid trust profile') from exc
    return result


def _crl_time(field: tuple[int, bytes]) -> datetime:
    """RFC 5280 CRL times have seconds and Z, with no fractional seconds."""
    tag, value = field
    if tag not in (23, 24) or re.fullmatch(rb'\d{12}Z' if tag == 23 else rb'\d{14}Z', value) is None:
        raise VerificationError('invalid CRL update time')
    try:
        offset = 2 if tag == 23 else 4
        year = int(value[:offset])
        if tag == 23:
            year += 2000 if year < 50 else 1900
        parts = [int(value[i:i+2]) for i in range(offset, offset + 10, 2)]
        return datetime(year, *parts, tzinfo=UTC)
    except ValueError as exc:
        raise VerificationError('invalid CRL update time') from exc


def _crl_times(der: bytes) -> tuple[datetime, datetime]:
    """Read only the bounded CRL structure and dates; OpenSSL checks signatures."""
    outer = sequence(der)
    if len(outer) != 3 or [tag for tag, _ in outer] != [48, 48, 3]:
        raise VerificationError('invalid CRL structure')
    fields = tlvs(outer[0][1])
    offset = 0
    if fields and fields[0][0] == 2:
        if integer(fields[0][1]) != 1:
            raise VerificationError('unsupported CRL version')
        offset = 1
    if len(fields) < offset + 4 or [tag for tag, _ in fields[offset:offset+2]] != [48, 48]:
        raise VerificationError('CRL requires issuer and both update times')
    last_update = _crl_time(fields[offset+2])
    next_update = _crl_time(fields[offset+3])
    if [tag for tag, _ in fields[offset+4:]] not in ([], [48], [160], [48, 160]):
        raise VerificationError('invalid CRL structure')
    if next_update <= last_update:
        raise VerificationError('invalid CRL validity interval')
    return last_update, next_update


def _crl_window(bundle: bytes, max_age_seconds: int, now: datetime) -> tuple[datetime, datetime]:
    """Accept only 1..8 PEM CRLs, all fresh, without importing any trust anchors."""
    if not isinstance(bundle, bytes) or not 0 < len(bundle) <= MAX_PROOF_BYTES:
        raise VerificationError('missing or oversized CRL bundle')
    position, windows = 0, []
    while position < len(bundle):
        while position < len(bundle) and bundle[position] in b' \t\r\n':
            position += 1
        if position == len(bundle):
            break
        match = _CRL_PEM.match(bundle, position)
        if match is None or len(windows) == 8:
            raise VerificationError('bundle must contain only one to eight PEM X509 CRLs')
        body = b''.join(match['body'].splitlines())
        try:
            der = base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise VerificationError('invalid CRL PEM encoding') from exc
        if base64.b64encode(der) != body:
            raise VerificationError('noncanonical CRL PEM encoding')
        last_update, next_update = _crl_times(der)
        try:
            age_limit = last_update + timedelta(seconds=max_age_seconds)
        except OverflowError as exc:
            raise VerificationError('invalid CRL age interval') from exc
        valid_until = min(next_update, age_limit)
        if not last_update <= now < valid_until:
            raise VerificationError('CRL is not current within the configured maximum age')
        windows.append((last_update, valid_until))
        position = match.end()
    if not windows:
        raise VerificationError('CRL bundle is empty')
    return max(start for start, _ in windows), min(end for _, end in windows)


def make_query(subject_sha256: str) -> bytes:
    digest(subject_sha256)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'request.tsq'
        command(['openssl', 'ts', '-query', '-digest', subject_sha256, '-sha256', '-cert', '-out', str(path)])
        query = path.read_bytes()
    fields = sequence(query)
    if imprint(fields[1]) != digest(subject_sha256) or not any(tag == 2 for tag, _ in fields[2:]):
        raise VerificationError('timestamp query missing nonce or digest')
    return query


def token_metadata(data: bytes, profile: TrustProfile) -> dict:
    f = sequence(data)
    if len(f) < 5 or [x[0] for x in f[:5]] != [2, 6, 48, 2, 24] or integer(f[0][1]) != 1:
        raise VerificationError('invalid TSTInfo')
    policy = oid_text(f[1][1])
    if policy not in profile.policy_oids:
        raise VerificationError('untrusted TSA policy')
    value = f[4][1].decode('ascii')
    if not re.fullmatch(r'\d{14}(?:\.\d{1,6})?Z', value):
        raise VerificationError('unsupported timestamp precision')
    time = datetime.strptime(value, '%Y%m%d%H%M%S.%fZ' if '.' in value else '%Y%m%d%H%M%SZ').replace(tzinfo=UTC)
    acc = profile.missing_accuracy_us
    position = 5
    if position < len(f) and f[position][0] == 48:
        parts = tlvs(f[position][1]); position += 1
        if not parts or [t for t, _ in parts] != sorted(set(t for t, _ in parts)):
            raise VerificationError('invalid timestamp accuracy')
        acc = 0
        for tag, v in parts:
            n = integer(v)
            if tag not in (2, 128, 129) or (tag != 2 and not 1 <= n <= 999):
                raise VerificationError('invalid timestamp accuracy')
            acc += n * {2: 1_000_000, 128: 1000, 129: 1}[tag]
    if position < len(f) and f[position][0] == 1:
        if f[position][1] != b'\xff':
            raise VerificationError('invalid DER ordering boolean')
        position += 1
    if position >= len(f) or f[position][0] != 2:
        raise VerificationError('timestamp nonce absent')
    nonce = integer(f[position][1]); position += 1
    if any(tag not in (160, 161) for tag, _ in f[position:]):
        raise VerificationError('unexpected timestamp fields')
    if acc is None or acc > profile.max_accuracy_us:
        raise VerificationError('unknown or excessive TSA time uncertainty')
    return dict(gen_time=time, upper_time=time + timedelta(microseconds=acc),
                accuracy_us=acc, nonce=nonce, policy_oid=policy, imprint=imprint(f[2]).hex())


def verify_reply(subject_sha256: str, query: bytes, reply: bytes, ca_pem: bytes,
                 profile: TrustProfile, deadline: str | datetime, *, now: datetime | None = None,
                 crl_bundle: bytes | None = None) -> dict:
    profile.validate(); expected = digest(subject_sha256)
    uses_crl = isinstance(profile, CRLTrustProfile)
    if not uses_crl and crl_bundle is not None:
        raise VerificationError('CRL material requires an explicit CRL trust profile')
    if uses_crl and now is not None and not profile.fixture:
        raise VerificationError('production CRL verification cannot override the current time')
    validation_time = utc(now or datetime.now(UTC)) if uses_crl else None
    crl_window = _crl_window(crl_bundle, profile.max_crl_age_seconds, validation_time) if uses_crl else None
    if any(not isinstance(x, bytes) or not 0 < len(x) <= MAX_PROOF for x in (query, reply, ca_pem)):
        raise VerificationError('missing or oversized timestamp material')
    if hashlib.sha256(ca_pem).hexdigest() != profile.ca_sha256:
        raise VerificationError('trust bundle does not match independently configured pin')
    q = sequence(query)
    if len(q) < 3 or q[0] != (2, b'\x01') or imprint(q[1]) != expected:
        raise VerificationError('query does not match committed subject')
    nonces = [integer(v) for tag, v in q[2:] if tag == 2]
    if len(nonces) != 1:
        raise VerificationError('exactly one query nonce required')
    with tempfile.TemporaryDirectory() as tmp:
        paths = {k: Path(tmp) / k for k in ('request', 'reply', 'ca', 'token', 'info')}
        if uses_crl and b'-----BEGIN X509 CRL-----' in ca_pem:
            raise VerificationError('CRLs must be supplied separately from the pinned CA bundle')
        store = ca_pem + b'\n' + crl_bundle if uses_crl else ca_pem
        for k, value in (('request', query), ('reply', reply), ('ca', store)):
            paths[k].write_bytes(value)
        args = ['openssl', 'ts', '-verify', '-queryfile', str(paths['request']), '-in', str(paths['reply']),
                '-CAfile', str(paths['ca']), '-CApath', tmp, '-CAstore', str(paths['ca']), '-auth_level', '2']
        if uses_crl:
            args.extend(['-crl_check', '-crl_check_all'])
            if now is not None:  # Only an explicitly marked local fixture can use a synthetic clock.
                args.extend(['-attime', str(int(validation_time.timestamp()))])
        command(args)
        command(['openssl', 'ts', '-reply', '-in', str(paths['reply']), '-token_out', '-out', str(paths['token'])])
        command(['openssl', 'cms', '-verify', '-binary', '-inform', 'DER', '-outform', 'DER',
                 '-in', str(paths['token']), '-noverify',
                 '-out', str(paths['info'])])
        info = token_metadata(paths['info'].read_bytes(), profile)
    if info['imprint'] != subject_sha256 or info['nonce'] != nonces[0]:
        raise VerificationError('signed message imprint or nonce mismatch')
    if info['gen_time'] > utc(now or datetime.now(UTC)) + timedelta(seconds=5):
        raise VerificationError('timestamp is in the future')
    if uses_crl and not crl_window[0] <= utc(now or datetime.now(UTC)) < crl_window[1]:
        raise VerificationError('CRL validity interval ended during verification')
    # Even the uncertainty upper bound must precede the exclusive cutoff.
    eligible = info['upper_time'] < utc(deadline)
    report = dict(protocol='fi.rfc3161-verification.v1', status='VERIFIED' if eligible else 'VERIFIED_LATE',
                  subject_sha256=subject_sha256, gen_time=info['gen_time'].isoformat(),
                  upper_time=info['upper_time'].isoformat(), accuracy_us=info['accuracy_us'],
                  policy_oid=info['policy_oid'], ca_sha256=profile.ca_sha256,
                  reply_sha256=hashlib.sha256(reply).hexdigest(), fixture=profile.fixture,
                  verifier='openssl-ts+fi-tstinfo-v1', revocation_status='NOT_CHECKED')
    if uses_crl:
        report.update(protocol='fi.rfc3161-verification.v2', verifier='openssl-ts+fi-tstinfo-crl-v2',
                      revocation_status='GOOD', crl_sha256=hashlib.sha256(crl_bundle).hexdigest(),
                      crl_base64=base64.b64encode(crl_bundle).decode('ascii'),
                      crl_valid_from=crl_window[0].isoformat().replace('+00:00', 'Z'),
                      crl_valid_until=crl_window[1].isoformat().replace('+00:00', 'Z'))
    return report


def request_reply(url: str, query: bytes, *, allowed_host: str, timeout: int = 15) -> bytes:
    """Explicitly configured HTTPS TSA only; no redirects or retries."""
    u = urlsplit(url)
    if u.scheme != 'https' or u.hostname != allowed_host or u.port not in (None, 443) or u.username or u.password or u.fragment:
        raise VerificationError('unapproved TSA endpoint')
    if not query or len(query) > 8192 or not 1 <= timeout <= 60:
        raise VerificationError('invalid timestamp request')
    from .operations import PublicHTTPS  # Lazy import avoids a module cycle.
    connection = PublicHTTPS(allowed_host, timeout=timeout, context=ssl.create_default_context())
    try:
        connection.request('POST', (u.path or '/') + ('?' + u.query if u.query else ''), query,
                           {'Content-Type': 'application/timestamp-query', 'Accept': 'application/timestamp-reply'})
        response = connection.getresponse()
        data = response.read(MAX_PROOF + 1)
        if response.status != 200 or len(data) > MAX_PROOF or response.getheader('Content-Type', '').split(';')[0] != 'application/timestamp-reply':
            raise VerificationError('TSA did not return a bounded timestamp reply')
        return data
    except (OSError, http.client.HTTPException) as exc:
        raise VerificationError('TSA network failure; proof not obtained') from exc
    finally:
        connection.close()


def ots_status(proof: bytes | None, verified_block_time: str | None = None) -> dict:
    # Untrusted supplied times are deliberately not interpreted as confirmation.
    if verified_block_time is not None:
        raise VerificationError('block time must come from an independent Bitcoin verifier')
    if proof is None:
        return {'status': 'NOT_REQUESTED', 'qualifies_precise_deadline': False}
    if not isinstance(proof, bytes) or not 0 < len(proof) <= MAX_PROOF:
        raise VerificationError('invalid OTS proof')
    return {'status': 'PENDING_UNVERIFIED', 'proof_sha256': hashlib.sha256(proof).hexdigest(),
            'qualifies_precise_deadline': False}
