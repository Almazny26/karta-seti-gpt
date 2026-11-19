/* Карта сети — заменитель оболочки Android для программы на компьютере.
 *
 * Программа run.py отдаёт ту же страницу, что и приложение на телефоне, и подключает этот файл сразу
 * после Leaflet, до кода приложения. Код приложения при этом не меняется:
 *   - данные приложения (localStorage) хранятся в файле data\state.json на компьютере: при запуске
 *     они загружаются оттуда, при каждом изменении — записываются обратно;
 *   - офлайн-карта https://local.tiles/... и онлайн-карты (схема, спутник, подписи) идут через
 *     программу на компьютере: просмотренное хранится на диске и видно без интернета;
 *   - без галочки «Онлайн» программа в интернет не ходит (как на телефоне);
 *   - AndroidBridge: рельеф считает программа на компьютере по тому же файлу dem.bin,
 *     PNG профиля сохраняется в «Загрузки».
 */
(function () {
  'use strict';

  window.KartaPC = true;
  window.KartaOfflineOnly = true;
  var EXPECTED = '3.4.5';   // версия run.py, для которой написана эта страница
  window.KartaPCVersion = EXPECTED;
  var OWN = /^(wf_|karta_)/;

  function request(method, url, body) {
    var x = new XMLHttpRequest();
    x.open(method, url, false);   // синхронно: приложение ждёт ответа так же, как от оболочки Android
    if (body != null) x.setRequestHeader('Content-Type', 'application/json');
    x.send(body == null ? null : body);
    return x;
  }

  // ---------------------------------------------------------------- хранилище в файле на компьютере

  var proto = Storage.prototype, setItem = proto.setItem, removeItem = proto.removeItem;
  var pendingKey = 'pc_pending_state', warning;
  function warnSave(failed) {
    if (!document.body) return;
    if (failed && !warning) {
      warning = document.createElement('div');
      warning.setAttribute('role', 'alert');
      warning.style.cssText = 'position:fixed;z-index:3100;bottom:12px;left:50%;transform:translateX(-50%);max-width:90%;padding:10px 14px;background:#a32018;color:white;border-radius:10px;font:14px system-ui';
      warning.textContent = 'Не удалось записать карту на диск. Изменения пока хранятся в браузере.';
      document.body.appendChild(warning);
    } else if (!failed && warning) { warning.remove(); warning = null; }
  }
  try {
    var r = localStorage.getItem(pendingKey) ? null : request('GET', '/api/state');
    // Shared sessions keep personal preferences in each browser.
    if (!r) {
      var probe = request('GET', '/api/state');
      if (probe.getResponseHeader('X-Karta-Shared') === '1') r = probe;
    }
    window.KartaSharedMode = window.KartaSharedMode || !!(r && r.getResponseHeader('X-Karta-Shared') === '1');
    if (window.KartaSharedMode && r && r.status === 200) {
      removeItem.call(localStorage, pendingKey);
      ['karta_net', 'karta_author'].forEach(function (k) { setItem.call(localStorage, k, JSON.parse(r.responseText)[k]); });
      r = null;
    }
    if (r && r.status === 200 && r.getResponseHeader('X-State') !== 'none') {
      var saved = JSON.parse(r.responseText), drop = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (OWN.test(k) && !(k in saved)) drop.push(k);
      }
      drop.forEach(function (k) { removeItem.call(localStorage, k); });
      Object.keys(saved).forEach(function (k) { setItem.call(localStorage, k, saved[k]); });
    }
  } catch (e) { /* программа не ответила — работаем с тем, что есть в браузере */ }

  setItem.call(localStorage, 'wf_online', '0');
  function persist() {
    if (window.KartaSharedMode || window.KartaPCSwitching) return;
    var all = {};
    for (var i = 0; i < localStorage.length; i++) {
      var k = localStorage.key(i);
      if (OWN.test(k)) all[k] = localStorage.getItem(k);
    }
    var ok = false;
    try {
      setItem.call(localStorage, pendingKey, '1');
      ok = request('POST', '/api/state', JSON.stringify(all)).status === 200;
      if (ok) removeItem.call(localStorage, pendingKey);
    } catch (e) { /* повторим при следующем изменении или запуске */ }
    warnSave(!ok);
  }
  proto.setItem = function (k, v) {
    setItem.call(this, k, v);
    if (this === localStorage && OWN.test(k)) persist();
  };
  proto.removeItem = function (k) {
    removeItem.call(this, k);
    if (this === localStorage && OWN.test(k)) persist();
  };
  document.addEventListener('DOMContentLoaded', function () {
    if (localStorage.getItem(pendingKey)) persist();
  });

  // ---------------------------------------------------------------- карты

  // офлайн-карта и онлайн-карты идут через программу на компьютере: она хранит просмотренные
  // плитки в data\tilecache и показывает их без интернета; в интернет ходит только при «Онлайн»
  var REWRITE = [
    [/^https:\/\/local\.tiles\//, '/'],
    // ?v= — чтобы браузер не показывал плитки, запомненные прежней версией (заглушки Esri)
    [/^https:\/\/tile\.openstreetmap\.org\/.*/, '/tc/osm/{z}/{x}/{y}?v=' + EXPECTED],
    [/^https:\/\/server\.arcgisonline\.com\/ArcGIS\/rest\/services\/World_Imagery\/.*/, '/tc/sat/{z}/{x}/{y}?v=' + EXPECTED],
    [/^https:\/\/core-renderer-tiles\.maps\.yandex\.net\/tiles\?l=skl.*/, '/tc/skl/{z}/{x}/{y}?v=' + EXPECTED]
  ];
  if (window.L && L.tileLayer) {
    var tileLayer = L.tileLayer;
    L.tileLayer = function (url, opts) {
      if (typeof url === 'string') {
        for (var i = 0; i < REWRITE.length; i++) {
          if (REWRITE[i][0].test(url)) { url = url.replace(REWRITE[i][0], REWRITE[i][1]); break; }
        }
      }
      return tileLayer.call(this, url, opts);
    };
    // Браузер грузит с одного адреса не больше 6 плиток сразу. Программа отвечает и по адресам
    // t1…t4.localhost (это тот же компьютер) — плитки идут вчетверо параллельнее. Сначала проверяем,
    // что такие адреса работают; до ответа и при ошибке — обычный адрес.
    var spread = false, port = location.port;
    window.__kartaTileUrl = function (u, x, y) {
      return spread && u.charAt(0) === '/' ? 'http://t' + (1 + ((x + y) & 3)) + '.localhost:' + port + u : u;
    };
    var probe = new Image();
    probe.onload = function () { spread = true; };
    if (!window.KartaSharedMode && !window.KartaOfflineOnly) probe.src = 'http://t1.localhost:' + port + '/icon.png?probe=' + Date.now();
    var getTileUrl = L.TileLayer.prototype.getTileUrl;
    L.TileLayer.prototype.getTileUrl = function (coords) {
      return window.__kartaTileUrl(getTileUrl.call(this, coords), coords.x, coords.y);
    };
  }

  // ---------------------------------------------------------------- версия программы
  // Страница новая, а программа (чёрное окно, может быть свёрнуто) — старая: подсказать перезапуск.
  try {
    var info = JSON.parse(request('GET', '/api/info').responseText);
    if (info.version !== EXPECTED) {
      document.addEventListener('DOMContentLoaded', function () {
        var b = document.createElement('div');
        b.style.cssText = 'position:fixed;left:50%;top:56px;transform:translateX(-50%);z-index:200000;max-width:640px;' +
          'background:#b3261e;color:#fff;border-radius:12px;padding:10px 16px;font:14px/1.4 system-ui,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.3)';
        b.textContent = 'Работает прежняя версия программы (' + info.version + '). Закройте её чёрное окно (оно может быть ' +
          'свёрнуто на панели задач) и запустите «Карта сети» заново.';
        document.body.appendChild(b);
      });
    }
  } catch (e) { /* не важно */ }

  // ---------------------------------------------------------------- замена оболочки Android

  window.AndroidBridge = {
    fetchElevations: function (locs) {
      var r = request('POST', '/api/elev', JSON.stringify({ locs: locs, online: localStorage.getItem('wf_online') === '1' }));
      return r.status === 200 ? r.responseText : '{"status":"ERROR","error":"offline"}';
    },
    saveImage: function (dataUrl) {
      var name = 'fresnel_' + Date.now() + '.png';
      var a = document.createElement('a');
      a.href = dataUrl; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      return 'OK:' + name;
    },
    setOnline: function () { /* признак хранится в wf_online, программа читает его при запросе рельефа */ }
  };
})();
