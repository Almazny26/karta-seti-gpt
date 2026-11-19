/* Shared desktop map: optimistic object transactions and presence polling. */
(function () {
  'use strict';
  function request(method, path, body) {
    var xhr = new XMLHttpRequest();
    xhr.open(method, path, false);
    if (body) xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.send(body ? JSON.stringify(body) : null);
    if (xhr.status === 401) { location.replace('/login'); throw Error('Требуется вход'); }
    var data = JSON.parse(xhr.responseText);
    if (xhr.status !== 409 && xhr.status !== 200) throw Error(data.error || 'Сервер не ответил');
    return data;
  }
  var initial = request('GET', '/api/shared'), base = initial.net, revision = initial.revision;
  var user = initial.user, online = initial.online, busy = false, panel, status, pending = null, accepted = false, polling = false;
  var copy = function (v) { return JSON.parse(JSON.stringify(v)); };
  function info() {
    if (status) {
      status.textContent = pending ? 'Правки не сохранены — повторить' : (generic(user.name) ? 'Владелец' : user.name) + ' · в сети ' + online.length;
      status.title = pending ? 'Нажмите, чтобы повторить сохранение. Не закрывайте страницу.' : 'Пользователи и отметки · ' + ({admin:'Администратор',editor:'Редактор',viewer:'Просмотр'})[user.role];
      status.style.color = pending ? 'var(--danger,#b42318)' : 'var(--accent,#145db0)';
    }
  }
  function adopt(data) {
    if (data.net) { base = copy(data.net); revision = data.revision; }
    user = data.user; online = data.online;
    info();
  }
  window.KartaShared = {
    user: function () { return user; },
    initial: base,
    hasPending: function () { return !!pending; },
    wasAccepted: function () { return accepted; },
    showUsers: function () { participants('access'); },
    commit: function (net) {
      accepted = false;
      var ids = new Set(Object.keys(base.objs).concat(Object.keys(net.objs))), ops = [];
      ids.forEach(function (id) {
        var before = base.objs[id] || null, after = net.objs[id] || null;
        if (JSON.stringify(before) !== JSON.stringify(after)) ops.push({id: id, before: before, after: after});
      });
      if (!ops.length) { pending = null; info(); return copy(base); }
      if (user.role === 'viewer') { alert('У вашей учётной записи доступен только просмотр'); return copy(base); }
      busy = true;
      try {
        var data = request('POST', '/api/shared', {ops: ops});
        accepted = !data.conflicts;
        if (data.conflicts) {
          // Preserve a local downloadable copy before resolving a concurrent edit.
          var link = document.createElement('a');
          link.href = URL.createObjectURL(new Blob([JSON.stringify({format:'karta-seti', version:3, data:{net:net}})], {type:'application/json'}));
          link.download = 'Несохранённая_правка_' + Date.now() + '.json';
          link.click();
          setTimeout(function () { URL.revokeObjectURL(link.href); }, 30000);
          var names = data.conflicts.map(function (id) { return (net.objs[id] || base.objs[id]).f.name || id; }).join(', ');
          var keep = confirm('Другой пользователь уже изменил: ' + names + '. Ваша правка сохранена отдельным файлом. Применить вашу версию поверх текущей? Отмена — принять версию сервера.');
          if (keep) {
            ops.forEach(function (op) { op.before = data.net.objs[op.id] || null; });
            data = request('POST', '/api/shared', {ops: ops});
            accepted = !data.conflicts;
            if (data.conflicts) alert('Объект снова изменился. Принята версия сервера. Ваша копия сохранена в файле.');
          }
        }
        adopt(data);
        pending = null; info();
        return copy(base);
      } catch (error) {
        var firstFailure = !pending;
        pending = copy(net); info();
        if (firstFailure) alert('Изменения пока не сохранены: ' + error.message + '. Не закрывайте страницу. Нажмите красную кнопку пользователя, чтобы повторить сохранение.');
        return copy(net);
      } finally { busy = false; }
    }
  };
  window.addEventListener('beforeunload', function (e) { if (pending) { e.preventDefault(); e.returnValue = ''; } });
  function element(tag, text, parent) {
    var el = document.createElement(tag);
    if (text) el.textContent = text;
    if (parent) parent.appendChild(el);
    return el;
  }
  function button(text, parent, cls, click) {
    var b = element('button', text, parent); b.type = 'button';
    if (cls) b.className = cls;
    if (click) b.onclick = click;
    return b;
  }
  function generic(name) { return !name || /^(телефон|компьютер|браузер|серверный компьютер|без подписи)$/i.test(name.trim()); }
  var roleNames = {admin:'Администратор', editor:'Редактирование', viewer:'Просмотр'};
  function participants(startTab) {
    if (panel) panel.remove();
    var currentPanel = panel = element('dialog', '', document.body);
    panel.id = 'apUsersDialog';
    var head = element('div', '', panel); head.className = 'us-head';
    element('h2', 'Пользователи', head);
    button('×', head, 'us-close', function () { currentPanel.close(); }).setAttribute('aria-label','Закрыть');
    var tabs = element('div', '', panel); tabs.className = 'us-tabs'; tabs.setAttribute('role','tablist');
    var content = element('div', '', panel); content.className = 'us-content';
    var members = button('Участники', tabs, '', function () { show('members'); }); members.setAttribute('role','tab');
    var access = null;
    if (user.role === 'admin') { access = button('Доступ', tabs, '', function () { show('access'); }); access.setAttribute('role','tab'); }
    function message(text, error) { var p = element('p',text,content); p.className = error ? 'us-error' : 'us-success'; p.setAttribute('role','status'); }
    function show(tab, notice) {
      members.setAttribute('aria-selected',tab !== 'access');
      if (access) access.setAttribute('aria-selected',tab === 'access');
      content.replaceChildren();
      if (tab === 'access' && access) accounts(notice);
      else marks();
    }
    function marks() {
      element('h3', 'В сети', content);
      var list = element('div','',content); list.className = 'us-online';
      online.forEach(function (u) { var item = element('span',generic(u.name) ? 'Владелец' : u.name,list); item.className = 'us-person'; });
      if (!online.length) { var empty = element('p','Нет активных участников',list); empty.className = 'us-muted'; }
      element('h3','Отметки',content);
      var authors = new Map(), accountsById = new Map();
      online.forEach(function (u) { accountsById.set(u.login,u.name); });
      Object.values(window.KartaNet.data().objs).forEach(function (o) {
        if (o.f.deleted) return;
        var rawName = o.createdBy || o.by || '', id = o.createdById || '';
        var name = generic(rawName) ? 'Без автора' : accountsById.get(id) || rawName;
        var key = generic(rawName) ? 'unassigned' : id || name;
        if (!authors.has(key)) authors.set(key,{name:name, objects:[]});
        authors.get(key).objects.push(o);
      });
      if (!authors.size) { var empty = element('p','На карте пока нет отметок',content); empty.className = 'us-muted'; }
      Array.from(authors.values()).sort(function (a,b) { return a.name.localeCompare(b.name,'ru'); }).forEach(function (group) {
        var details = element('details','',content);
        element('summary',group.name + ' · ' + group.objects.length,details);
        var rendered = false;
        details.ontoggle = function () {
          if (!details.open || rendered) return;
          rendered = true;
          group.objects.forEach(function (o) {
            button(o.f.name || ({node:'Узел',fiber:'Линия ВОЛС',radio:'Радиолиния'})[o.type], details,'us-object',function () { currentPanel.close(); window.KartaApp.showObject(o.id); });
          });
        };
      });
      if (user.login !== '@server') button('Выйти',content,'us-danger',function () { request('POST','/api/logout',{}); location.replace('/login'); });
    }
    function accounts(notice) {
      try {
        var users = request('GET','/api/users').users;
        var toolbar = element('div','',content); toolbar.className = 'us-toolbar';
        element('strong','Доступ по сети',toolbar);
        button('Добавить пользователя',toolbar,'us-primary',function () { editor(null); });
        if (notice) message(notice,false);
        if (!users.length) { var empty = element('p','Добавьте тех, кому нужен доступ к карте.',content); empty.className = 'us-muted'; }
        users.sort(function (a,b) { return a.name.localeCompare(b.name,'ru'); }).forEach(function (u) {
          var row = element('div','',content); row.className = 'us-account';
          var caption = element('div','',row); element('strong',u.name,caption); element('small',u.login + ' · ' + roleNames[u.role],caption);
          button('Изменить',row,'',function () { editor(u); }).setAttribute('aria-label','Изменить доступ: ' + u.name);
        });
      } catch (e) { message(e.message,true); }
    }
    function editor(u) {
      content.replaceChildren();
      element('h3',u ? u.name : 'Новый пользователь',content);
      var form = element('form','',content), grid = element('div','',form); grid.className = 'us-grid';
      var fields = {};
      [['name','Имя'],['login','Логин'],['password','Пароль']].forEach(function (row) {
        var field = element('div','',grid), label = element('label',row[1],field);
        label.htmlFor = 'us-' + row[0];
        var wrap = row[0] === 'password' ? element('div','',field) : field;
        if (wrap !== field) wrap.className = 'us-password';
        var input = element('input','',wrap); input.name = row[0];
        input.id = label.htmlFor;
        input.value = u && row[0] !== 'password' ? u[row[0]] : '';
        input.required = row[0] !== 'password' || !u;
        input.maxLength = row[0] === 'login' ? 40 : row[0] === 'name' ? 80 : 256;
        input.autocomplete = row[0] === 'password' ? 'new-password' : 'off';
        if (row[0] === 'login' && u) input.readOnly = true;
        if (row[0] === 'password') {
          input.type = 'password'; input.minLength = 8;
          input.placeholder = u ? 'Не менять' : 'Не менее 8 символов';
          button('Показать',wrap,'',function () { var show = input.type === 'password'; input.type = show ? 'text' : 'password'; this.textContent = show ? 'Скрыть' : 'Показать'; this.setAttribute('aria-pressed',show); }).setAttribute('aria-label','Показать пароль');
        }
        fields[row[0]] = input;
      });
      var field = element('div','',grid), label = element('label','Права',field), roles = element('select','',field);
      roles.id = 'us-role'; label.htmlFor = roles.id;
      ['editor','viewer','admin'].forEach(function (role) { element('option',roleNames[role],roles).value = role; });
      roles.value = u ? u.role : 'editor';
      var result = element('p','',form); result.className = 'us-error'; result.setAttribute('role','alert');
      if (u) button('Отключить доступ',form,'us-danger',function () {
        if (!confirm('Отключить доступ для «' + u.name + '»?')) return;
        try { request('POST','/api/users',{login:u.login,name:u.name,role:u.role,disable:true}); show('access','Доступ отключён'); }
        catch (e) { result.textContent = e.message; }
      });
      var actions = element('div','',form); actions.className = 'us-actions';
      button('Отмена',actions,'',function () { show('access'); });
      var save = button('Сохранить',actions,'us-primary'); save.type = 'submit';
      form.onsubmit = function (event) {
        event.preventDefault(); save.disabled = true;
        try {
          request('POST','/api/users',{login:fields.login.value.trim(),name:fields.name.value.trim(),password:fields.password.value,role:roles.value});
          show('access',u ? 'Изменения сохранены' : 'Пользователь добавлен');
        } catch (e) { result.textContent = e.message; save.disabled = false; }
      };
      fields.name.focus();
    }
    currentPanel.addEventListener('close',function () { currentPanel.remove(); if (panel === currentPanel) panel = null; });
    show(startTab === 'access' && access ? 'access' : 'members');
    currentPanel.showModal();
  }
  var usersCSS = document.createElement('link'); usersCSS.rel = 'stylesheet'; usersCSS.href = 'users.css'; document.head.appendChild(usersCSS);
  document.addEventListener('DOMContentLoaded', function () {
    status = element('button', '', document.getElementById('apBar'));
    status.id = 'apUsers';
    status.style.cssText = 'min-width:0;max-width:28vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:7px 10px;border:0;border-radius:8px;background:var(--panel,#fff);color:var(--accent,#145db0);cursor:pointer;font:inherit';
    document.querySelector('#apBar .ap-sp').before(status);
    if (user.role === 'viewer') document.querySelector('[data-mode="edit"]').style.display = 'none';
    status.onclick = function () {
      if (pending) { var net = window.KartaShared.commit(pending); if (!pending) window.KartaApp.receiveShared(net); }
      else participants();
    }; info();
    setInterval(async function () {
      if (busy || polling || window.KartaPCSwitching || !window.KartaApp || document.hidden) return;
      polling = true;
      // Polling also acts as a heartbeat while a route/form is being edited.
      try {
        var response = await fetch('/api/shared?revision=' + revision, {cache:'no-store'});
        if (response.status === 401) { location.replace('/login'); return; }
        if (!response.ok) throw Error('Нет связи');
        var data = await response.json();
        if (busy || data.revision < revision) return;
        var current = window.KartaApp.state();
        if (pending || current.tool || (current.view && current.view.k === 'pt') || document.querySelector('#apDlg.show') || document.activeElement.matches('input,textarea,select')) {
          user = data.user; online = data.online; info(); return;
        }
        adopt(data);
        if (data.net) window.KartaApp.receiveShared(copy(base));
      } catch (e) { status.textContent = pending ? 'Правки не сохранены — повторить' : 'Нет связи с сервером · повторное подключение'; }
      finally { polling = false; }
    }, 2000);
  });
})();
