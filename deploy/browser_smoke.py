"""Read-only real Chromium smoke on the local, disposable Compose deployment."""
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

secret=Path('deploy/.local-secrets/dashboard_token').read_text().strip()
with sync_playwright() as p:
    browser=p.chromium.launch()
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://127.0.0.1:8080/',wait_until='networkidle')
    expect(page.locator('h1')).to_have_text('预测与验证工作台')
    with page.expect_response(lambda r:r.url.endswith('/api/v1/experiments')) as response:
        page.locator('#load').click()
    assert response.value.status==401
    expect(page.locator('#status')).to_have_text('访问失败：401')
    page.locator('#token').fill(secret)
    with page.expect_response(lambda r:r.url.endswith('/api/v1/experiments')) as response:
        page.locator('#load').click()
    assert response.value.status==200
    expect(page.locator('#status')).to_contain_text('已连接')
    expect(page.locator('#rows table')).to_be_visible()
    expect(page.locator('#rows')).to_contain_text('FIXTURE')
    expect(page.locator('#rows')).to_contain_text('operation_mode')
    for view in ('batches','outcomes','runs','experiments'):
        with page.expect_response(lambda r:r.url.endswith('/api/v1/'+view)) as response:
            page.locator('[data-view="'+view+'"]').click()
        assert response.value.status==200
        expect(page.locator('#status')).to_contain_text('已连接')
    assert page.evaluate('localStorage.length')==0
    assert page.evaluate('sessionStorage.length')==0
    assert page.request.post('http://127.0.0.1:8080/api/v1/resolutions',
        headers={'Authorization':'Bearer '+secret},data={}).status==403
    page.locator('#token').fill('')
    assert secret not in page.content()
    assert not errors, 'Browser script errors occurred (details withheld)'
    browser.close()
print('PASS: Chromium authenticated rendering, explicit fixture/mode labels, four HTTP-backed tabs, no JS errors, no persistent token storage, write denied.')
