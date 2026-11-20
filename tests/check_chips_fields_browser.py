"""3.5.4: ряды кнопок без обрезки (виды точек и узлов, прокладка, жилы) и поля ВОЛС «Цвет тубы» / «Цвет волокна».

ПК: панель разной ширины и масштаб интерфейса; KARTA_CHROME92 — то же в Chromium 92 (как Edge 92 на рабочем ПК)."""
import json, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / '.audit/deps'), str(ROOT)]
os.environ['KARTA_MAP_DIR'] = str(ROOT / 'map')
import run
from playwright.sync_api import sync_playwright

pts = [dict(lat=46.8490, lng=35.3650, kind='узел', nid='a')]
for k in range(1, 20):
    pts.append(dict(lat=46.8490 - k * 0.00025, lng=35.3650 + k * 0.0017, kind='колодец' if k == 9 else 'муфта' if k == 5 else 'опора'))
pts.append(dict(lat=46.8440, lng=35.3990, kind='узел', nid='b'))
def node(i, la, ln, name):
    return dict(id=i, type='node', f=dict(name=name, kind='узел доступа', state='работает', lat=la, lng=ln), ft={}, t=1795000000000, by='т')
NET = dict(v=1, journal=[], objs=dict(a=node('a', 46.849, 35.365, 'УА-1'), b=node('b', 46.844, 35.399, 'УД-2'),
           l1=dict(id='l1', type='fiber', f=dict(name='ВОЛС Центр — УД-2', fnodes=pts, lay=['air'] * 9 + ['ground'] * 5 + ['surface'] * 6, fibers='16', cable='ОКСН-16'), ft={}, t=1795000000000, by='т')))

CLIPPED = r'''() => {
  const out = [], vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  for (const root of [...document.querySelectorAll('#apSheet, #apDlgBox')].filter(vis)) {
    const rr = root.getBoundingClientRect();
    root.querySelectorAll('.chips, .chip, .lrow').forEach(e => {
      if (!vis(e)) return;
      const r = e.getBoundingClientRect(), s = getComputedStyle(e);
      if (r.right > rr.right + 1 || r.left < rr.left - 1) out.push('out ' + e.className + ' «' + e.textContent.trim().slice(0, 16) + '»');
      if (e.scrollWidth > e.clientWidth + 1) out.push('scroll ' + e.className + ' «' + e.textContent.trim().slice(0, 16) + '» ' + e.scrollWidth + '>' + e.clientWidth);
      if (e.matches('.chip') && e.parentElement) { const p = e.parentElement.getBoundingClientRect(); if (r.right > p.right + 1 || r.left < p.left - 1) out.push('row ' + e.textContent.trim().slice(0, 16)); }
    });
  }
  return out;
}'''

def free():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

def rect(page, sel):
    return page.evaluate("s=>{var r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}}", sel)

def run_checks(page, label, problems, full):
    page.wait_for_function('window.KartaApp')
    time.sleep(1.5)
    page.evaluate("void map.setView([46.8475,35.381],15)")
    for ui, panel in ([(1, 360), (1.3, 460), (1, 300)] if full else [(1, 360), (1.3, 460)]):
        page.evaluate("([u,p])=>{var s=JSON.parse(localStorage.getItem('karta_set')||'{}');s.ui=u;s.pcPanel=p;localStorage.setItem('karta_set',JSON.stringify(s))}", [ui, panel])
        page.reload(); page.wait_for_function('window.KartaApp'); time.sleep(1.2)
        page.evaluate("void map.setView([46.8475,35.381],15)")
        for name, js in [('point', "KartaApp.setMode('edit'); KartaApp.showPoint('l1',9)"),
                         ('tool_node', "KartaApp.setMode('edit'); KartaApp.startNode()"),
                         ('tool_line', "KartaApp.setMode('edit'); KartaApp.startLine('fiber')"),
                         ('line_menu', "KartaApp.setMode('edit'); KartaApp.showLineMenu('l1',4,L.latLng(46.8478,35.3727))")]:
            page.evaluate("()=>{try{KartaApp.finishTool()}catch(e){}var x=document.querySelector('#apSheet .ap-x');if(x&&x.offsetParent)x.click()}"); time.sleep(.2)
            page.evaluate(js); time.sleep(.6)
            bad = page.evaluate(CLIPPED)
            if bad: problems.append((label, 'ui %s panel %s' % (ui, panel), name, bad[:5]))
        # горизонтальной прокрутки у рядов видов больше нет
        page.evaluate("KartaApp.finishTool(); KartaApp.setMode('edit'); KartaApp.showPoint('l1',9)"); time.sleep(.5)
        kinds = page.evaluate("[...document.querySelectorAll('#apSheet .chips')].map(c=>getComputedStyle(c).overflowX)")
        if 'auto' in kinds or 'scroll' in kinds: problems.append((label, 'kinds still scroll', kinds))

with tempfile.TemporaryDirectory() as folder:
    json.dump({'karta_author': 'Проверка', 'karta_net': json.dumps(NET, ensure_ascii=False)}, open(os.path.join(folder, 'state.json'), 'w'), ensure_ascii=False)
    app = run.App(folder, lambda *a: None)
    server = run.Server(('127.0.0.1', 0), run.make_handler(app)); threading.Thread(target=server.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%s' % server.server_address[1]
    problems = []; c92 = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('KARTA_CHROME'), args=['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
            for w, h in [(1366, 768), (1024, 700), (1920, 1080)]:
                ctx = browser.new_context(viewport=dict(width=w, height=h)); page = ctx.new_page(); errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(base); run_checks(page, '%dx%d' % (w, h), problems, w == 1366)
                if w == 1366:
                    # окно «Линия ВОЛС»: число жил — кнопками, текущее выбрано; цвета тубы и волокна
                    page.evaluate("KartaApp.setMode('edit'); KartaApp.showObject('l1')"); time.sleep(.5)
                    page.click('#apSheet [data-a=name]'); time.sleep(.4)
                    assert page.evaluate("document.querySelectorAll('#apDlgBox select').length") == 0, 'native select left in line dialog'
                    assert page.evaluate("document.querySelector('#apDlgBox .formchips .chip.on').textContent") == '16'
                    labels = page.evaluate("[...document.querySelectorAll('#apDlgBox label')].map(l=>l.textContent)")
                    assert 'Цвет тубы' in labels and 'Цвет волокна' in labels, labels
                    head = page.evaluate("(()=>{var h=document.querySelector('#apDlgBox .ap-dhead h3').getBoundingClientRect(),x=document.querySelector('#apDlgBox .ap-dx').getBoundingClientRect();return [h.top+h.height/2,x.top+x.height/2]})()")
                    assert abs(head[0] - head[1]) < 3, ('title and × not on one line', head)
                    page.click('#apDlgBox .formchips [data-v="32"]')
                    tube = page.locator('#apDlgBox input[list]').nth(0); fibre = page.locator('#apDlgBox input[list]').nth(1)
                    assert tube.get_attribute('maxlength') == '60'
                    tube.fill('синий'); fibre.fill('красный с чёрной полосой')
                    page.click('#apOk'); time.sleep(.5)
                    f = page.evaluate("JSON.parse(localStorage.getItem('karta_net')).objs.l1.f")
                    assert (f['fibers'], f['tubeColor'], f['fiberColor']) == ('32', 'синий', 'красный с чёрной полосой'), f
                    page.evaluate("KartaApp.showObject('l1')"); time.sleep(.4)
                    card = page.evaluate("document.querySelector('#apSheet').textContent")
                    assert 'Цвет тубы' in card and 'синий' in card and 'Цвет волокна' in card, card[:300]
                    # повторное нажатие на выбранное число жил снимает выбор
                    page.click('#apSheet [data-a=name]'); time.sleep(.3)
                    page.click('#apDlgBox .formchips [data-v="32"]'); page.click('#apOk'); time.sleep(.3)
                    f = page.evaluate("JSON.parse(localStorage.getItem('karta_net')).objs.l1.f")
                    assert f['fibers'] == '' and f['tubeColor'] == 'синий', f
                    # раскрывающийся блок — своя «галочка», системный треугольник скрыт
                    page.evaluate("KartaApp.showPoint('l1',9)"); time.sleep(.4)
                    marker = page.evaluate("getComputedStyle(document.querySelector('#apSheet details>summary'),'::before').borderRightWidth")
                    assert marker == '2px', marker
                assert not errors, errors
                ctx.close()
            browser.close()
            exe92 = os.environ.get('KARTA_CHROME92')
            if exe92:
                port = free()
                c92 = subprocess.Popen([exe92, '--headless', '--no-sandbox', '--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--remote-debugging-port=%d' % port,
                                        '--user-data-dir=%s' % os.path.join(folder, 'c92'), 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(3)
                b92 = p.chromium.connect_over_cdp('http://127.0.0.1:%d' % port)
                ctx = b92.new_context(viewport=dict(width=1366, height=768)); page = ctx.new_page(); errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(base); run_checks(page, 'chromium92 1366', problems, True)
                assert not errors, errors
    finally:
        server.shutdown()
        if c92: c92.kill()
    assert not problems, json.dumps(problems, ensure_ascii=False, indent=1)
    print('Chips and line fields passed: no clipped or scrolling button rows (PC widths, UI scale, panel widths%s), fiber-count chips and tube/fiber colours in the line dialog, chevrons' % (', Chromium 92' if os.environ.get('KARTA_CHROME92') else ''))
