"""Narrow SQL gateway: use distinct operator, verifier, resolver and scorer logins."""
from __future__ import annotations

import base64
from datetime import datetime
import json
from uuid import UUID

from . import batch
from .timestamps import TrustProfile, make_query, verify_reply


def uid(value):
    return str(UUID(str(value)))


class DeliveryRepository:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def one(self, statement, params=()):
        import psycopg
        with psycopg.connect(self._dsn) as c:
            c.execute("SET LOCAL lock_timeout='5s'")
            c.execute("SET LOCAL statement_timeout='30s'")
            row = c.execute(statement, params).fetchone()
            if row is None:
                raise LookupError('record not found')
            value = row[0]
        return value

    def many(self, statement, params=()):
        return self.one('SELECT coalesce(jsonb_agg(x),\'[]\'::jsonb) FROM ('+statement+') x',params)

    def capture(self, experiment, kind, plan=None, profile=None) -> str:
        from psycopg.types.json import Jsonb
        return str(self.one('SELECT fi.capture_batch(%s::uuid,%s,%s,%s)',
                            (uid(experiment),kind,Jsonb(plan) if plan is not None else None,
                             Jsonb(profile) if profile is not None else None)))

    def get_batch(self, batch_id) -> dict:
        b = self.one('SELECT to_jsonb(b) FROM fi.commitment_batches b WHERE id=%s::uuid',(uid(batch_id),))
        records = self.one('SELECT jsonb_agg(record_text ORDER BY ordinal) FROM fi.batch_records WHERE batch_id=%s::uuid',(uid(batch_id),))
        old = self.one('SELECT (SELECT to_jsonb(s) FROM fi.batch_seals s WHERE batch_id=%s::uuid)',(uid(batch_id),))
        if old is None:
            raise LookupError('batch not sealed')
        return dict(protocol=batch.PROTOCOL,batch_id=uid(batch_id),kind=b['kind'],records=records,
                    nonces=old['nonces'],root_sha256=old['root_sha256'],subject_sha256=old['subject_sha256'])

    def seal(self, batch_id) -> dict:
        try:
            return self.get_batch(batch_id)
        except LookupError:
            pass
        b = self.one('SELECT to_jsonb(b) FROM fi.commitment_batches b WHERE id=%s::uuid',(uid(batch_id),))
        records = self.one('SELECT jsonb_agg(record_text ORDER BY ordinal) FROM fi.batch_records WHERE batch_id=%s::uuid',(uid(batch_id),))
        value = batch.seal(batch_id,b['kind'],records)
        self.one('SELECT fi.seal_batch(%s::uuid,%s::text[],%s,%s)',
                 (uid(batch_id),value['nonces'],value['root_sha256'],value['subject_sha256']))
        return self.get_batch(batch_id)

    def new_anchor(self, batch_id, protocol='RFC3161', query: bytes | None=None) -> tuple[str, bytes]:
        if query is None:
            if protocol!='RFC3161':
                raise ValueError('OTS requires independently generated proof/request bytes')
            query = make_query(self.get_batch(batch_id)['subject_sha256'])
        return str(self.one('SELECT fi.request_anchor(%s::uuid,%s,%s)',(uid(batch_id),protocol,query))),query

    def receive(self, request_id, reply: bytes | None, error_code: str | None=None):
        self.one('SELECT fi.receive_anchor(%s::uuid,%s,%s)',(uid(request_id),reply,error_code))

    def verify_anchor(self, request_id, ca_pem: bytes, trusted_profile: TrustProfile,
                      *, crl_bundle: bytes | None = None) -> dict:
        from psycopg.types.json import Jsonb
        # Binary material is base64 only at the JSON transport boundary.
        data = self.one("""SELECT jsonb_build_object('query',encode(a.request_bytes,'base64'),
          'reply',encode(r.reply_bytes,'base64'),'subject',s.subject_sha256,'deadline',e.deadline,'profile',b.trust_profile)
          FROM fi.anchor_requests a JOIN fi.anchor_replies r ON r.request_id=a.id
          JOIN fi.batch_seals s ON s.batch_id=a.batch_id JOIN fi.commitment_batches b ON b.id=s.batch_id
          JOIN fi.experiments e ON e.id=b.experiment_id WHERE a.id=%s::uuid""",(uid(request_id),))
        external = json.loads(json.dumps(trusted_profile.__dict__))
        if external != data['profile']:
            raise ValueError('independent trust configuration disagrees with preregistration')
        result = verify_reply(data['subject'],base64.b64decode(data['query']),base64.b64decode(data['reply']),
                              ca_pem,trusted_profile,data['deadline'],crl_bundle=crl_bundle)
        self.one('SELECT fi.attest_anchor(%s::uuid,%s)',(uid(request_id),Jsonb(result)))
        return result

    def add_evidence(self, version, permission, release_key, published_at, revision, value, sha256, source_reference):
        return str(self.one('SELECT fi.add_outcome_evidence(%s::uuid,%s::uuid,%s,%s::timestamptz,%s,%s,%s,%s)',
                            (uid(version),uid(permission),release_key,published_at,revision,value,sha256,source_reference)))

    def propose(self, version, kind, evidence_id, reason, parent=None, fallback_reason=None):
        return str(self.one('SELECT fi.propose_resolution(%s::uuid,%s,%s::uuid,%s,%s::uuid,%s)',
                            (uid(version),kind,uid(evidence_id) if evidence_id else None,reason,uid(parent) if parent else None,fallback_reason)))

    def confirm(self, proposal, note):
        self.one('SELECT fi.confirm_resolution(%s::uuid,%s)',(uid(proposal),note))

    def score(self, experiment):
        return str(self.one('SELECT fi.score_experiment(%s::uuid)',(uid(experiment),)))

    def scores(self, run):
        return self.many('SELECT * FROM fi.score_rows WHERE run_id=%s::uuid ORDER BY entry_key,question_version_id',(uid(run),))

    def list_page(self, resource, page_size=100, cursor=None, experiment_id=None):
        """Bounded keyset page; a live view, not a frozen audit export.

        Cursor scope is validated, never used as an authority. The existing
        role-specific DB connection still enforces access. No caller SQL identifiers.
        """
        queries = {
            'experiments': ('*', 'fi.experiment_summary', 'id', None, 'id'),
            'outcomes': ('*', 'fi.outcome_catalog', 'version_id', None, None),
            'batches': ('*', 'fi.batch_catalog', 'id', 'captured_at', 'experiment_id'),
            'runs': ('id,experiment_id,algorithm,context_sha256,created_at',
                     'fi.score_runs', 'id', 'created_at', 'experiment_id'),
        }
        if resource not in queries or type(page_size) is not int or not 1 <= page_size <= 200:
            raise ValueError('invalid page request')
        columns, relation, id_column, time_column, filter_column = queries[resource]
        scope = uid(experiment_id) if experiment_id is not None else None
        if scope is not None and filter_column is None:
            raise ValueError('experiment filter unavailable for this resource')
        terms, params = [], []
        if scope is not None:
            terms.append(filter_column+'=%s::uuid'); params.append(scope)
        if cursor is not None:
            # Keep malformed, cross-resource and cross-filter cursors out of SQL.
            try:
                if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024:
                    raise ValueError
                raw = base64.b64decode(cursor+'='*((-len(cursor)) % 4), altchars=b'-_', validate=True)
                data = json.loads(raw)
                if (not isinstance(data, list) or len(data) != 5 or type(data[0]) is not int
                        or data[:3] != [1, resource, scope] or not isinstance(data[3], list)):
                    raise ValueError
                key = data[3]
                # Last slot binds the page size so changing size cannot silently
                # change the traversal contract during a user's navigation.
                if type(data[4]) is not int or data[4] != page_size:
                    raise ValueError
                if len(key) != (2 if time_column else 1) or not all(isinstance(k,str) for k in key):
                    raise ValueError
                last_id = uid(key[-1])
                if time_column:
                    stamp = datetime.fromisoformat(key[0].replace('Z', '+00:00'))
                    if stamp.tzinfo is None or stamp.utcoffset() is None:
                        raise ValueError
                    terms.append('('+time_column+','+id_column+') < (%s::timestamptz,%s::uuid)')
                    params.extend([stamp.isoformat(), last_id])
                else:
                    terms.append(id_column+' > %s::uuid'); params.append(last_id)
            except (ValueError, TypeError, UnicodeError, OverflowError):
                raise ValueError('invalid or mismatched page cursor') from None
        order = time_column+' DESC,'+id_column+' DESC' if time_column else id_column
        statement = 'SELECT '+columns+' FROM '+relation
        if terms: statement += ' WHERE '+' AND '.join(terms)
        statement += ' ORDER BY '+order+' LIMIT %s'
        rows = self.many(statement, tuple(params+[page_size+1]))
        more = len(rows) > page_size
        items = rows[:page_size]
        next_cursor = None
        if more:
            last = items[-1]
            key = [str(last[time_column]), str(last[id_column])] if time_column else [str(last[id_column])]
            value = json.dumps([1,resource,scope,key,page_size],separators=(',',':')).encode('utf-8')
            next_cursor = base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')
        return {'items':items, 'next_cursor':next_cursor, 'has_more':more,
                'page_size':page_size, 'consistency':'LIVE_VIEW_NOT_AUDIT_SNAPSHOT'}

    def overview(self, *, experiment_id=None):
        # Existing callers still get one list. Tests for a known experiment must
        # use this exact filter, not assume it falls inside an arbitrary first page.
        return self.list_page('experiments',200,experiment_id=experiment_id)['items']

    def outcomes(self):
        return self.list_page('outcomes',200)['items']

    def batches(self):
        return self.list_page('batches',200)['items']

    def score_runs(self):
        return self.list_page('runs',200)['items']

    def score_summary(self, run):
        return self.many('SELECT * FROM fi.score_summary WHERE run_id=%s::uuid ORDER BY cohort_key,entry_key',(uid(run),))

    def export(self, experiment) -> dict:
        rows = self.many('SELECT id,kind,analysis_plan,trust_profile FROM fi.commitment_batches WHERE experiment_id=%s::uuid ORDER BY kind',(uid(experiment),))
        by_kind = {r['kind']:self.get_batch(r['id']) for r in rows}
        if set(by_kind)!={'REGISTRATION','FORECAST'}:
            raise ValueError('complete registration and forecast batches required')
        _, final_records = batch.verify_pair(by_kind['REGISTRATION'],by_kind['FORECAST'])
        # Select only question identities already present in the sealed public proof.
        # Do not broaden operator privileges to underlying task tables.
        versions = sorted({uid(t['question_version_id']) for t in final_records[1:]})
        anchors=self.many("""SELECT a.id,a.batch_id,a.protocol,encode(a.request_bytes,'base64') AS query_b64,
          r.status,encode(r.reply_bytes,'base64') AS reply_b64,r.error_code,v.report AS claimed_verification
          FROM fi.anchor_requests a JOIN fi.commitment_batches b ON b.id=a.batch_id
          LEFT JOIN fi.anchor_replies r ON r.request_id=a.id LEFT JOIN fi.anchor_verifications v ON v.request_id=a.id
          WHERE b.experiment_id=%s::uuid ORDER BY a.created_at,a.id""",(uid(experiment),))
        outcomes=self.many("SELECT * FROM fi.outcome_catalog WHERE version_id=ANY(%s::uuid[]) ORDER BY version_id",(versions,))
        return dict(protocol='fi.audit-export.v1',classification='INTERNAL_RESEARCH_NOT_AUTOMATIC_PUBLICATION',
                    registration=by_kind['REGISTRATION'],forecast=by_kind['FORECAST'],anchors=anchors,outcomes=outcomes)
