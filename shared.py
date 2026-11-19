"""Authenticated shared desktop sessions and atomic map updates."""
import copy
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie


class SharedError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class DataLock:
    """Only one desktop process may write a given state directory, on any port."""
    def __init__(self, folder):
        self.file = open(os.path.join(folder, 'program.lock'), 'a+b')
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise

    def close(self):
        if not self.file.closed:
            self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200000).hex()


class Shared:
    def __init__(self, state, folder):
        self.state = state
        self.path = os.path.join(folder, 'shared-users.json')
        self.lock = threading.RLock()
        self.sessions = {}
        self.failures = {}
        self.revision = 0
        if os.path.exists(self.path):
            with open(self.path, encoding='utf-8') as f:
                self.users = json.load(f)
        else:
            self.users = {}
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump({}, f)

    def set_user(self, data, local_owner=False):
        if not isinstance(data, dict):
            raise SharedError(400, 'Неверные данные пользователя')
        with self.lock:
            login = str(data.get('login', '')).strip().lower()
            name = str(data.get('name', '')).strip()
            role = data.get('role')
            if not login or login == '@server' or len(login) > 40 or not name or len(name) > 80 or role not in ('admin', 'editor', 'viewer'):
                raise SharedError(400, 'Укажите логин, имя и роль')
            users = copy.deepcopy(self.users)
            old = users.get(login, {})
            if data.get('disable'):
                users.pop(login, None)
            else:
                password = str(data.get('password', ''))
                if password and len(password) < 8 or not old and not password:
                    raise SharedError(400, 'Пароль должен содержать не менее 8 символов')
                user = dict(old, login=login, name=name, role=role)
                if password:
                    user['salt'] = secrets.token_hex(16)
                    user['hash'] = password_hash(password, user['salt'])
                users[login] = user
            if not local_owner and any(u['role'] == 'admin' for u in self.users.values()) and not any(u['role'] == 'admin' for u in users.values()):
                raise SharedError(400, 'Нужно сохранить хотя бы одного администратора')
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(users, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.users = users
            for token, session in list(self.sessions.items()):
                if session['login'] == login:
                    del self.sessions[token]

    def login(self, data, ip):
        if not isinstance(data, dict):
            raise SharedError(400, 'Неверные данные входа')
        with self.lock:
            count, ts = self.failures.get(ip, (0, 0))
            if count >= 5 and time.time() - ts < 60:
                raise SharedError(429, 'Повторите вход через минуту')
            user = self.users.get(str(data.get('login', '')).strip().lower())
            if not user or not hmac.compare_digest(user['hash'], password_hash(str(data.get('password', '')), user['salt'])):
                self.failures[ip] = (count + 1 if time.time() - ts < 60 else 1, time.time())
                raise SharedError(401, 'Неверный логин или пароль')
            self.failures.pop(ip, None)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = dict(login=user['login'], ip=ip, seen=time.time(), since=time.time())
            return token

    def identify(self, cookie):
        cookies = SimpleCookie()
        try:
            cookies.load(cookie or '')
            token = cookies['karta_session'].value
        except (KeyError, ValueError):
            raise SharedError(401, 'Войдите в программу')
        with self.lock:
            session = self.sessions.get(token)
            if not session or time.time() - session['seen'] > 12 * 3600 or session['login'] not in self.users:
                self.sessions.pop(token, None)
                raise SharedError(401, 'Войдите в программу')
            session['seen'] = time.time()
            return self.public(self.users[session['login']]), token

    @staticmethod
    def public(user):
        return {k: user[k] for k in ('login', 'name', 'role')}

    def net(self):
        return json.loads((self.state.load() or {}).get('karta_net', '{"v":1,"objs":{},"journal":[]}'))

    def snapshot(self, user, revision=None):
        with self.lock:
            online = {}
            for session in self.sessions.values():
                if time.time() - session['seen'] < 15 and session['login'] in self.users:
                    u = self.public(self.users[session['login']])
                    online[u['login']] = dict(u, ip=session['ip'])
            result = dict(revision=self.revision, user=user, online=list(online.values()))
            local = getattr(self, 'local_presence', None)
            if local and time.time() - local[1] < 15:
                result['online'].insert(0, dict(local[0], ip='127.0.0.1'))
            if revision != str(self.revision):
                result['net'] = self.net()
            return result

    @staticmethod
    def validate(obj, id):
        if not isinstance(obj, dict) or obj.get('id') != id or obj.get('type') not in ('node', 'fiber', 'radio') or not isinstance(obj.get('f'), dict):
            raise SharedError(400, 'Неверная структура объекта')
        f = obj['f']
        if f.get('deleted'):
            return
        points = [f] if obj['type'] == 'node' else f.get('fnodes' if obj['type'] == 'fiber' else 'points')
        if not isinstance(points, list) or (obj['type'] != 'node' and len(points) < 2):
            raise SharedError(400, 'У линии должно быть не меньше двух точек')
        for p in points:
            if not isinstance(p, dict) or any(not isinstance(p.get(k), (int, float)) or not math.isfinite(p[k]) or abs(p[k]) > limit for k, limit in [('lat', 90), ('lng', 180)]):
                raise SharedError(400, 'Неверные координаты')
        if obj['type'] == 'radio' and len(points) != 2:
            raise SharedError(400, 'Радиолиния соединяет две точки')
        if obj['type'] == 'fiber' and (not isinstance(f.get('lay'), list) or len(f['lay']) != len(points)-1 or any(k not in ('air','ground','surface','air_guess','ground_guess') for k in f['lay'])):
            raise SharedError(400, 'Неверные участки ВОЛС')

    def apply(self, user, data):
        if user['role'] == 'viewer':
            raise SharedError(403, 'Доступен только просмотр')
        ops = data.get('ops') if isinstance(data, dict) else None
        if not isinstance(ops, list) or len(ops) > 10000:
            raise SharedError(400, 'Неверный список изменений')
        with self.lock:
            net = self.net()
            ids = set()
            conflicts = []
            for op in ops:
                if not isinstance(op, dict):
                    raise SharedError(400, 'Неверное изменение')
                id = op.get('id')
                if not isinstance(id, str) or not id or len(id) > 200 or id in ('__proto__', 'constructor', 'prototype') or id in ids:
                    raise SharedError(400, 'Неверный идентификатор')
                ids.add(id)
                if net['objs'].get(id) != op.get('before'):
                    conflicts.append(id)
                if op.get('after') is not None:
                    self.validate(op['after'], id)
            if conflicts:
                return dict(conflicts=conflicts, **self.snapshot(user)), 409
            now = int(time.time() * 1000)
            for op in ops:
                id, old, obj = op['id'], net['objs'].get(op['id']), copy.deepcopy(op.get('after'))
                if obj is None:
                    if not old:
                        continue
                    obj = copy.deepcopy(old)
                    obj['f']['deleted'] = True
                obj['by'], obj['byId'], obj['t'] = user['name'], user['login'], now
                if old:
                    obj['createdBy'] = old.get('createdBy', old.get('by', user['name']))
                    obj['createdById'] = old.get('createdById', old.get('byId', ''))
                else:
                    obj['createdBy'], obj['createdById'] = user['name'], user['login']
                ft = dict((old or {}).get('ft', {}))
                for key in obj['f']:
                    if not old or obj['f'].get(key) != old['f'].get(key):
                        ft[key] = max(now, ft.get(key, 0) + 1)
                obj['ft'] = ft
                net['objs'][id] = obj
                net.setdefault('journal', []).insert(0, dict(t=now, by=user['name'], byId=user['login'], id=id, type=obj['type'], name=obj['f'].get('name', ''), what='удалён' if obj['f'].get('deleted') else 'изменён' if old else 'добавлен'))
            # Node coordinates are authoritative for attached endpoints.
            for line in net['objs'].values():
                if line['type'] == 'node' or line['f'].get('deleted'):
                    continue
                f = line['f']
                previous_fields = copy.deepcopy(f)
                pts = f.get('fnodes' if line['type'] == 'fiber' else 'points', [])
                for p in pts:
                    if not p.get('nid'):
                        continue
                    node = net['objs'].get(p['nid'])
                    if node and node['type'] == 'node' and not node['f'].get('deleted'):
                        p.update(lat=node['f']['lat'], lng=node['f']['lng'], name=node['f'].get('name', ''))
                    else:
                        p.pop('nid', None)
                        p['name'] = ''
                        if p.get('kind') == 'узел':
                            p['kind'] = 'точка'
                f['from'], f['to'] = pts[0].get('nid', ''), pts[-1].get('nid', '')
                distance = 0
                for a, b in zip(pts, pts[1:]):
                    lat1, lat2 = math.radians(a['lat']), math.radians(b['lat'])
                    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(math.radians(b['lng']-a['lng'])/2)**2
                    distance += 6371 * 2 * math.asin(min(1, math.sqrt(h)))
                f['dist'] = round(distance, 4)
                if f != previous_fields:
                    line.setdefault('createdBy', line.get('by', user['name']))
                    line.setdefault('createdById', line.get('byId', ''))
                    line['t'], line['by'], line['byId'] = now, user['name'], user['login']
                    for key in f:
                        if f.get(key) != previous_fields.get(key):
                            ft = line.setdefault('ft', {})
                            ft[key] = max(now, ft.get(key, 0) + 1)
            net['journal'] = net.get('journal', [])[:1000]
            state = self.state.load() or {}
            state['karta_net'] = json.dumps(net, ensure_ascii=False)
            self.state.save(state)
            if ops:
                self.revision += 1
            return self.snapshot(user), 200
