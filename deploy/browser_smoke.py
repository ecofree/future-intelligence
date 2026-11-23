"""Read-only real Chromium smoke on the local, disposable Compose deployment."""
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect

secret=Path('deploy/.local-secrets/dashboard_token').read_text().strip()
with sync_playwright() as p:
    browser=p.chromium.launch()
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://127.0.0.1:8080/',wait_until='networkidle')
    expect(page.locator('h1')).to_have_text('预测与验证工作台')
    with page.expect_response(lambda r:urlsplit(r.url).path=='/api/v1/experiments') as response:
        page.locator('#load').click()
    assert response.value.status==401
    expect(page.locator('#status')).to_have_text('访问失败：401')
    page.locator('#token').fill(secret)
    with page.expect_response(lambda r:urlsplit(r.url).path=='/api/v1/experiments') as response:
        page.locator('#load').click()
    assert response.value.status==200
    first=response.value.json();assert len(first)==100
    seen=[row['id'] for row in first]
    expect(page.locator('#status')).to_contain_text('已连接')
    expect(page.locator('#rows table')).to_be_visible()
    expect(page.locator('#rows')).to_contain_text('FIXTURE')
    expect(page.locator('#rows')).to_contain_text('operation_mode')
    expect(page.locator('#prev')).to_be_disabled()
    for index in (2,3):
        expect(page.locator('#next')).to_be_enabled()
        with page.expect_response(lambda r:urlsplit(r.url).path=='/api/v1/experiments') as response:
            page.locator('#next').click()
        assert response.value.status==200
        values=response.value.json();seen.extend(row['id'] for row in values)
        expect(page.locator('#status')).to_contain_text('第 '+str(index)+' 页')
    assert len(seen)==205 and len(set(seen))==205
    expect(page.locator('#next')).to_be_disabled()
    with page.expect_response(lambda r:urlsplit(r.url).path=='/api/v1/experiments') as response:
        page.locator('#prev').click()
    assert [row['id'] for row in response.value.json()]==seen[100:200]
    expect(page.locator('#status')).to_contain_text('第 2 页')
    exact=page.request.get('http://127.0.0.1:8080/api/v1/experiments?page_size=1&experiment_id='+seen[-1],headers={'Authorization':'Bearer '+secret})
    assert exact.status==200 and exact.json()[0]['id']==seen[-1]
    for view in ('batches','outcomes','runs','experiments'):
        with page.expect_response(lambda r:urlsplit(r.url).path=='/api/v1/'+view) as response:
            page.locator('[data-view="'+view+'"]').click()
        assert response.value.status==200
        expect(page.locator('#status')).to_contain_text('已连接')
        expect(page.locator('#prev')).to_be_disabled()
    assert page.evaluate('localStorage.length')==0
    assert page.evaluate('sessionStorage.length')==0
    assert page.request.post('http://127.0.0.1:8080/api/v1/resolutions',
        headers={'Authorization':'Bearer '+secret},data={}).status==403
    page.locator('#token').fill('')
    expect(page.locator('#rows')).to_be_empty()
    expect(page.locator('#next')).to_be_disabled()
    assert secret not in page.content()
    assert not errors, 'Browser script errors occurred (details withheld)'
    browser.close()
print('PASS: Chromium 205-row traversal, back/next and exact filter, authenticated four tabs, no JS errors, token clearing and write denial.')
