"""Synthetic rows only. Real cumulative PostgreSQL coverage is separate."""
import base64
import json
import unittest
from uuid import UUID
from fastapi.testclient import TestClient
from future_intelligence.delivery.repository import DeliveryRepository
from future_intelligence.delivery.workbench import create_app


def ident(i): return str(UUID(int=i))


class RecordingRepository(DeliveryRepository):
    def __init__(self,rows=()): self.rows=list(rows);self.calls=[]
    def many(self,statement,params=()):self.calls.append((statement,params));return self.rows


class ListPaginationTests(unittest.TestCase):
    def test_page_plus_one_and_cursor_continuation(self):
        r=RecordingRepository([{'id':ident(i)} for i in range(1,5)])
        p=r.list_page('experiments',3)
        self.assertEqual(len(p['items']),3);self.assertTrue(p['has_more'])
        self.assertEqual(r.calls[0][1],(4,));self.assertIn('ORDER BY id LIMIT %s',r.calls[0][0])
        r.rows=[];q=r.list_page('experiments',3,p['next_cursor'])
        self.assertEqual(r.calls[-1][1],(ident(3),4));self.assertIn('id > %s::uuid',r.calls[-1][0])
        self.assertFalse(q['has_more']);self.assertIsNone(q['next_cursor'])
    def test_exact_last_page_and_empty_page(self):
        for rows in ([],[{'id':ident(1)}]):
            p=RecordingRepository(rows).list_page('experiments',1)
            self.assertFalse(p['has_more']);self.assertIsNone(p['next_cursor'])
    def test_timestamp_ties_use_uuid(self):
        for resource,key in [('batches','captured_at'),('runs','created_at')]:
            r=RecordingRepository([{key:'2026-11-17T01:00:00+00:00','id':ident(i)} for i in (4,3,2)])
            p=r.list_page(resource,2);r.rows=[];r.list_page(resource,2,p['next_cursor'])
            statement,args=r.calls[-1]
            self.assertIn('('+key+',id) < (%s::timestamptz,%s::uuid)',statement)
            self.assertIn('ORDER BY '+key+' DESC,id DESC',statement)
            self.assertEqual(args[-2:],(ident(3),3))
    def test_outcomes_cursor_uses_version_id(self):
        r=RecordingRepository([{'version_id':ident(1)},{'version_id':ident(2)}]);p=r.list_page('outcomes',1)
        r.rows=[];r.list_page('outcomes',1,p['next_cursor'])
        self.assertIn('version_id > %s::uuid',r.calls[-1][0])
    def test_scoped_id_not_sql(self):
        r=RecordingRepository();r.list_page('experiments',17,experiment_id=ident(7))
        self.assertIn('WHERE id=%s::uuid',r.calls[-1][0]);self.assertEqual(r.calls[-1][1],(ident(7),18))
        r.overview(experiment_id=ident(7));self.assertEqual(r.calls[-1][1],(ident(7),201))
    def test_invalid_sizes_and_resources_rejected_before_sql(self):
        r=RecordingRepository()
        for size in (True,0,-1,201,1.0,'10'):
            with self.assertRaises(ValueError):r.list_page('experiments',size)
        with self.assertRaises(ValueError):r.list_page('experiments; DROP TABLE fi.experiments')
        with self.assertRaises(ValueError):r.list_page('outcomes',experiment_id=ident(1))
        with self.assertRaises(ValueError):r.list_page('experiments',experiment_id='bad; SQL')
        self.assertEqual(r.calls,[])
    def test_cursor_scope_resource_and_size_bound(self):
        r=RecordingRepository([{'id':ident(1)},{'id':ident(2)}]);p=r.list_page('experiments',1,experiment_id=ident(3))
        for args in [('outcomes',1,p['next_cursor'],None),('experiments',2,p['next_cursor'],ident(3)),('experiments',1,p['next_cursor'],None),('experiments',1,p['next_cursor'],ident(4))]:
            with self.assertRaises(ValueError):r.list_page(*args)
        self.assertEqual(len(r.calls),1)
    def test_malformed_cursor_never_reaches_sql(self):
        r=RecordingRepository()
        malformed=['','!!!','a'*1025,base64.urlsafe_b64encode(b'null').decode(),base64.urlsafe_b64encode(b'[1,"experiments",null,["not-uuid"],10]').decode()]
        for c in malformed:
            with self.subTest(cursor=c[:40]),self.assertRaises(ValueError):r.list_page('experiments',10,c)
        for key in [['not-a-time',ident(1)],['2026-11-17T00:00:00',ident(1)]]:
            c=base64.urlsafe_b64encode(json.dumps([1,'batches',None,key,10]).encode()).decode()
            with self.assertRaises(ValueError):r.list_page('batches',10,c)
        self.assertEqual(r.calls,[])
    def test_pages_never_claim_snapshot_or_total(self):
        p=RecordingRepository().list_page('runs')
        self.assertEqual(p['consistency'],'LIVE_VIEW_NOT_AUDIT_SNAPSHOT');self.assertNotIn('total',p)


class HTTPPaginationTests(unittest.TestCase):
    def setUp(self):
        self.repo=RecordingRepository([{'id':ident(i)} for i in (1,2,3)])
        self.accounts={r:(c*40,self.repo) for r,c in [('dashboard','d'),('resolver','r'),('reviewer','v'),('scorer','s')]}
        self.client=TestClient(create_app(self.accounts));self.h={'Authorization':'Bearer '+'d'*40}
    def test_list_shape_and_continuation_headers(self):
        response=self.client.get('/api/v1/experiments?page_size=2',headers=self.h)
        self.assertEqual(response.status_code,200);self.assertIsInstance(response.json(),list)
        self.assertEqual(len(response.json()),2);self.assertEqual(response.headers['x-has-more'],'true')
        self.assertTrue(response.headers['x-next-cursor']);self.assertIn('no-store',response.headers['cache-control'])
    def test_legacy_explicitly_first_page(self):
        response=self.client.get('/api/v1/experiments',headers=self.h)
        self.assertEqual(response.status_code,200);self.assertIn('legacy-first-page',response.headers['x-pagination'])
    def test_exact_experiment_filter_and_empty(self):
        self.repo.rows=[];response=self.client.get('/api/v1/experiments',params={'page_size':20,'experiment_id':ident(9)},headers=self.h)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json(),[])
        self.assertEqual(self.repo.calls[-1][1],(ident(9),21));self.assertEqual(response.headers['x-next-cursor'],'')
    def test_invalid_pages_and_bare_cursor_rejected(self):
        for params in ({'page_size':0},{'page_size':201},{'page_size':'x'},{'cursor':'anything'},{'page_size':2,'cursor':'!!!'},{'experiment_id':ident(1)}):
            self.assertEqual(self.client.get('/api/v1/experiments',params=params,headers=self.h).status_code,422)
    def test_blind_roles_stay_blind_for_pages(self):
        for view in ('experiments','batches','runs'):
            self.assertEqual(self.client.get('/api/v1/'+view+'?page_size=1',headers={'Authorization':'Bearer '+'r'*40}).status_code,403)
        self.assertEqual(self.client.get('/api/v1/experiments?page_size=1').status_code,401)
        self.repo.rows=[]
        self.assertEqual(self.client.get('/api/v1/outcomes?page_size=1',headers={'Authorization':'Bearer '+'v'*40}).status_code,200)
    def test_page_error_does_not_leak(self):
        def broken(*args):raise RuntimeError('password=NEVER_PRINT')
        self.repo.list_page=broken;r=self.client.get('/api/v1/experiments?page_size=1',headers=self.h)
        self.assertEqual(r.status_code,409);self.assertNotIn('NEVER_PRINT',r.text)
    def test_pager_dom_and_stale_response_guard(self):
        text=self.client.get('/').text;self.assertIn('id="prev"',text);self.assertIn('id="next"',text)
        js=self.client.get('/ui.js').text
        for marker in ('X-Next-Cursor','page_size','request!==serial','AbortController','textContent'):
            self.assertIn(marker,js)
        self.assertNotIn('innerHTML',js);self.assertNotIn('localStorage',js);self.assertNotIn('sessionStorage',js)
