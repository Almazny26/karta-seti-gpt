"""3.5.1: схема на ПК не остаётся пустой, если MapLibre не создал слой стиля (поле: «reading 'type'
at _updatePlacement» на каждом кадре). Карта убирает сломанный слой и пересоздаётся; плитки идут без gzip."""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'.audit/deps'), str(ROOT)]
import run
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory() as folder:
    json.dump({'karta_author': 'Проверка'}, open(os.path.join(folder, 'state.json'), 'w'))
    app = run.App(folder, lambda *a: None)
    server = run.Server(('127.0.0.1', 0), run.make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%s' % server.server_address[1]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('KARTA_CHROME') or r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                                        headless=True, args=['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
            for bad, dpr in [(False, 1.25), (True, 1.5)]:
                ctx = browser.new_context(viewport=dict(width=1280, height=720), device_scale_factor=dpr)
                ctx.route('**/*', lambda r: r.continue_() if r.request.url.startswith((base, 'data:', 'blob:')) else r.abort())
                if bad:
                    ctx.add_init_script("window.__kartaTestBadLayer={id:'broken-test',type:'no-such-type',source:'p','source-layer':'roads'};")
                page = ctx.new_page(); errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(base); page.wait_for_function('window.KartaApp && window.KartaMap')
                if not page.evaluate('KartaMap.info().files.length'):
                    print('SKIP: нет файла схемы в map/'); sys.exit(0)
                page.evaluate("map.setView([46.84,35.37],13)")
                page.wait_for_function('KartaMap.info().tiles > 0', timeout=30000)
                n = len(errors); time.sleep(2)
                assert len(errors) == n, ('ошибки продолжаются на каждом кадре', errors[-3:])
                gm = page.evaluate("(()=>{var m=KartaMap.glMap();return m&&m.style?{order:m.style._order.length,miss:m.style._order.filter(i=>!m.style._layers[i]).length,loaded:m.style._loaded}:null})()")
                assert gm and gm['miss'] == 0 and gm['loaded'], gm
                text = page.evaluate('KartaMap.diag()')
                assert 'Проба плитки' in text and 'СЖАТОЙ' not in text, text
                if bad:
                    assert 'broken-test' in text, text
                # смена темы — стиль целиком, без ошибок
                for i in range(3):
                    page.evaluate("document.getElementById('apMenuBtn').click();document.querySelector('#apMenu [data-m=theme]').click()"); time.sleep(.3)
                time.sleep(1.5)
                gm = page.evaluate("(()=>{var m=KartaMap.glMap();return m.style._order.filter(i=>!m.style._layers[i]).length})()")
                assert gm == 0
                if bad:
                    print('ошибки до самолечения:', [e[:90] for e in errors][:3])
                else:
                    assert not errors, errors
                ctx.close()
            browser.close()
    finally:
        server.shutdown()
print('MapLibre self-heal passed: broken style layer skipped, map recreated, tiles uncompressed, theme switch')
