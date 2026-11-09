#!/usr/bin/env python3
"""Telegram activation gateway. Python 3.10+, standard library only."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent


class APIError(Exception):
    def __init__(self, code):
        self.code = code


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS customer_keys(
          hash TEXT PRIMARY KEY, alias TEXT NOT NULL, remaining INTEGER NOT NULL
          CHECK(remaining>=0), owner INTEGER);
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, key_hash TEXT);
        CREATE TABLE IF NOT EXISTS orders(
          id TEXT PRIMARY KEY, user INTEGER NOT NULL, key_hash TEXT NOT NULL,
          alias TEXT NOT NULL, email TEXT NOT NULL, task_id TEXT,
          state TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', notified INTEGER DEFAULT 0);
        CREATE UNIQUE INDEX IF NOT EXISTS one_open_order ON orders(user)
          WHERE state IN ('sending','unknown','submitted');
        CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
        """)
        # A crash during an HTTP submission has an unknown outcome, not a failure.
        with self.db:
            self.db.execute("UPDATE orders SET state='unknown' WHERE state='sending'")

    @staticmethod
    def digest(key):
        return hashlib.sha256(key.strip().encode()).hexdigest()

    def mint(self, alias, count):
        key = 'KAR-' + secrets.token_hex(12).upper()
        with self.db:
            self.db.execute('INSERT INTO customer_keys VALUES(?,?,?,NULL)',
                            (self.digest(key), alias, count))
        return key

    def redeem(self, user, key):
        h = self.digest(key)
        with self.db:
            row = self.db.execute('SELECT * FROM customer_keys WHERE hash=?', (h,)).fetchone()
            if not row or row['owner'] not in (None, user):
                raise ValueError('Key မမှန်ပါ သို့မဟုတ် အခြား user နဲ့ချိတ်ထားပါတယ်။')
            self.db.execute('UPDATE customer_keys SET owner=? WHERE hash=?', (user, h))
            self.db.execute('INSERT OR REPLACE INTO users VALUES(?,?)', (user, h))
        return row['remaining']

    def current(self, user):
        return self.db.execute('SELECT c.* FROM customer_keys c JOIN users u ON '
                               'u.key_hash=c.hash WHERE u.id=?', (user,)).fetchone()

    def reserve(self, user, email):
        order = secrets.token_hex(6)
        with self.db:
            key = self.current(user)
            if not key or key['remaining'] < 1:
                raise ValueError('Customer key ထည့်ပါ။ လက်ကျန် အနည်းဆုံး 1 လိုပါတယ်။')
            self.db.execute('INSERT INTO orders(id,user,key_hash,alias,email,state) '
                            'VALUES(?,?,?,?,?,?)',
                            (order, user, key['hash'], key['alias'], email, 'sending'))
            self.db.execute('UPDATE customer_keys SET remaining=remaining-1 WHERE hash=?',
                            (key['hash'],))
        return order, key['alias']

    def attach(self, order, task_id):
        with self.db:
            self.db.execute("UPDATE orders SET task_id=?, state='submitted' "
                            "WHERE id=? AND state IN ('sending','unknown')", (task_id, order))

    def finish(self, order, success, note):
        with self.db:
            row = self.db.execute('SELECT * FROM orders WHERE id=?', (order,)).fetchone()
            if not row or row['state'] not in ('sending', 'submitted', 'unknown'):
                return False
            if not success:
                self.db.execute('UPDATE customer_keys SET remaining=remaining+1 WHERE hash=?',
                                (row['key_hash'],))
            self.db.execute('UPDATE orders SET state=?, note=?, notified=0 WHERE id=?',
                            ('success' if success else 'refunded', note, order))
        return True

    def unknown(self, order):
        with self.db:
            self.db.execute("UPDATE orders SET state='unknown' WHERE id=? AND state='sending'",
                            (order,))


class HTTP:
    def __init__(self, config):
        self.config = config
        self.last = {}

    def request(self, url, payload=None, timeout=35):
        raw = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(url, data=raw, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read())
            except (ValueError, UnicodeError):
                raise RuntimeError('HTTP request failed') from None

    def telegram(self, method, payload):
        result = self.request('https://api.telegram.org/bot' +
                              self.config['telegram_token'] + '/' + method, payload)
        if not result.get('ok'):
            raise RuntimeError('Telegram request failed')
        return result['result']

    def pixel(self, path, payload=None, query=None):
        bucket = 'keys' if path.startswith('/api/pixel-keys/') else 'bridge'
        interval = 3.2 if bucket == 'keys' else 1.15
        time.sleep(max(0, self.last.get(bucket, 0) + interval - time.monotonic()))
        self.last[bucket] = time.monotonic()
        url = self.config['pixel_base_url'].rstrip('/') + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        result = self.request(url, payload)
        if not isinstance(result, dict) or 'code' not in result:
            raise RuntimeError('Invalid upstream response')
        if result['code'] != 0:
            raise APIError(result['code'])
        return result['data']


class Bot:
    def __init__(self, config, store, http):
        self.config, self.store, self.http = config, store, http
        self.awaiting = {}  # Expiring input prompts only; no account passwords.

    def send(self, user, text, buttons=False):
        data = {'chat_id': user, 'text': text[:4000]}
        if buttons:
            data['reply_markup'] = {'keyboard': [['Activate', 'Balance'], ['Orders']],
                                    'resize_keyboard': True}
        self.http.telegram('sendMessage', data)

    def is_admin(self, user):
        return user in self.config['admin_ids']

    def submit(self, user, text):
        lines = text.strip().splitlines()
        if len(lines) not in (2, 3, 4) or '@' not in lines[0] or not lines[1]:
            raise ValueError('တစ်ကြောင်းစီထည့်ပါ: Email၊ Password၊ 2FA secret၊ Recovery email (optional)။')
        account = {'email': lines[0].strip(), 'password': lines[1], 'twofa_type': 'totp'}
        if len(lines) >= 3:
            account['totp_secret'] = lines[2].replace(' ', '').upper()
        if len(lines) == 4:
            account['recovery'] = lines[3].strip()
        status = self.http.pixel('/api/pixel-bridge/status')
        if not status.get('accept_new_tasks'):
            raise ValueError('Service ခေတ္တရပ်ထားပါတယ်။ နောက်မှပြန်စမ်းပါ။')
        if status.get('require_totp') and not account.get('totp_secret'):
            raise ValueError('2FA secret လိုပါတယ်။ 6-digit code မဟုတ်ပါ။')
        secret = account.get('totp_secret')
        if secret and (len(secret) < 16 or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567' for c in secret)):
            raise ValueError('2FA secret မမှန်ပါ။ Authenticator secret ကိုထည့်ပါ။')
        key = self.store.current(user)
        if not key or key['alias'] not in self.config['real_keys']:
            raise ValueError('Customer key အရင်ထည့်ပါ။')
        real = self.config['real_keys'][key['alias']]
        quota = self.http.pixel('/api/pixel-keys/verify', {'key': real})
        if quota.get('remaining', 0) < 1:
            raise ValueError('Service quota ကုန်နေပါတယ်။ Admin ကိုဆက်သွယ်ပါ။')
        try:
            order, alias = self.store.reserve(user, account['email'])
        except sqlite3.IntegrityError:
            raise ValueError('ယခင် order လုပ်နေဆဲဖြစ်ပါတယ်။ /orders နဲ့စစ်ပါ။') from None
        try:
            result = self.http.pixel('/api/pixel-bridge/submit-task', {'key': real, **account})
            task_id = result['task']['id']
            if not isinstance(task_id, str) or not task_id:
                raise RuntimeError('No task identifier')
            self.store.attach(order, task_id)
        except APIError as exc:
            # Only an explicit, documented rejection proves no task was created.
            rejected = {10000,10001,10002,10003,10004,10005,20001,20002,20003,
                        20004,30001,40003,42900,50001}
            if exc.code in rejected:
                self.store.finish(order, False, 'Rejected: ' + str(exc.code))
            else:
                self.store.unknown(order)
            self.awaiting.pop(user, None)
            return self.send(user, f'Order {order}: /orders နဲ့စစ်ပါ။ Code: {exc.code}')
        except Exception:
            self.store.unknown(order)
            self.awaiting.pop(user, None)
            return self.send(user, f'Order {order}: response မရပါ။ ထပ်မတင်ပါနဲ့။ Admin စစ်ပေးပါမယ်။')
        self.awaiting.pop(user, None)
        self.send(user, f'Order {order} တင်ပြီးပါပြီ။ Result ကို ဒီမှာပြန်ပို့ပေးမယ်။')

    def message(self, message):
        if message.get('chat', {}).get('type') != 'private':
            return
        user = message['from']['id']
        text = message.get('text', '').strip()
        if not text:
            return
        parts = text.split()
        command = parts[0].split('@')[0].lower()
        if command == '/start':
            self.send(user, 'Activation bot\n/redeem CUSTOMER_KEY နဲ့ key ထည့်ပါ။\n'
                      '/activate — activation တင်ရန်\n/balance — လက်ကျန်\n/orders — order စစ်ရန်\n'
                      '/cancel — input ပယ်ဖျက်ရန်\n/myid — Telegram ID', True)
        elif command == '/myid':
            self.send(user, str(user))
        elif command == '/cancel':
            self.awaiting.pop(user, None)
            self.send(user, 'Input ပယ်ဖျက်ပြီးပါပြီ။ တင်ပြီးသား task ကိုတော့ မပယ်ဖျက်ပါ။')
        elif command == '/redeem' and len(parts) == 2:
            remaining = self.store.redeem(user, parts[1])
            self.send(user, f'Key ချိတ်ပြီးပါပြီ။ လက်ကျန် {remaining}', True)
        elif command in ('/balance', 'balance'):
            key = self.store.current(user)
            self.send(user, f"လက်ကျန် {key['remaining']}" if key else '/redeem CUSTOMER_KEY နဲ့ key ထည့်ပါ။')
        elif command in ('/orders', 'orders'):
            rows = self.store.db.execute('SELECT * FROM orders WHERE user=? ORDER BY rowid DESC LIMIT 10', (user,))
            self.send(user, '\n'.join(f"{r['id']} | {r['state']} | {r['note']}" for r in rows) or 'Order မရှိသေးပါ။')
        elif command in ('/activate', 'activate'):
            if not self.store.current(user):
                raise ValueError('/redeem CUSTOMER_KEY နဲ့ key အရင်ထည့်ပါ။')
            self.awaiting[user] = time.monotonic() + 600
            self.send(user, 'ကိုယ်ပိုင်/ခွင့်ပြုချက်ရှိတဲ့ account ကိုသာ တင်ပါ။\n'
                      'အောက်ပါအတိုင်း message တစ်စောင်ထဲ တစ်ကြောင်းစီပို့ပါ:\n'
                      'Email\nPassword\n2FA secret (6-digit code မဟုတ်ပါ)\nRecovery email (optional)\n'
                      'ဒီ message ဟာ Telegram history ထဲမှာကျန်ပါမယ်။ /cancel နဲ့ပယ်ဖျက်နိုင်ပါတယ်။')
        elif command == '/newkey' and self.is_admin(user) and len(parts) == 3:
            alias, count = parts[1], int(parts[2])
            if alias not in self.config['real_keys'] or not 1 <= count <= 10000:
                raise ValueError('/newkey main 1 — alias နဲ့ count စစ်ပါ။')
            self.send(user, self.store.mint(alias, count))
        elif command == '/pending' and self.is_admin(user):
            rows = self.store.db.execute("SELECT * FROM orders WHERE state IN ('unknown','sending','submitted') LIMIT 30")
            self.send(user, '\n'.join(f"{r['id']} | {r['alias']} | {r['email']} | {r['state']}" for r in rows) or 'Pending မရှိပါ။')
        elif command == '/attach' and self.is_admin(user) and len(parts) == 3:
            row = self.store.db.execute("SELECT * FROM orders WHERE id=? AND state='unknown'", (parts[1],)).fetchone()
            if not row:
                raise ValueError('Unknown order မတွေ့ပါ။')
            task = self.find_task(row['alias'], parts[2])
            if not task or task['email'].lower() != row['email'].lower():
                raise ValueError('အဲဒီ Real key ရဲ့ task နဲ့ email မကိုက်ပါ။')
            self.store.attach(parts[1], parts[2])
            self.send(user, 'Task ချိတ်ပြီးပါပြီ။')
        elif command == '/refund' and self.is_admin(user) and len(parts) == 3 and parts[2] == 'CONFIRM':
            row = self.store.db.execute("SELECT * FROM orders WHERE id=? AND state='unknown'", (parts[1],)).fetchone()
            if not row:
                raise ValueError('Unknown order မတွေ့ပါ။')
            self.store.finish(parts[1], False, 'Admin confirmed no upstream charge')
            self.send(user, 'Customer quota ပြန်ဖြည့်ပြီးပါပြီ။')
        elif self.awaiting.get(user, 0) > time.monotonic():
            self.submit(user, text)
        else:
            self.send(user, '/start နဲ့ command တွေကြည့်ပါ။')

    def tasks(self, alias):
        page = 1
        while True:
            data = self.http.pixel('/api/pixel-bridge/tasks', query={
                'key': self.config['real_keys'][alias], 'page': page, 'limit': 50})
            tasks = data['tasks']
            yield from tasks
            if not tasks or page * 50 >= data['total']:
                break
            page += 1

    def find_task(self, alias, task_id):
        return next((t for t in self.tasks(alias) if t['id'] == task_id), None)

    def poll(self):
        rows = self.store.db.execute("SELECT * FROM orders WHERE state='submitted'").fetchall()
        for alias in dict.fromkeys(r['alias'] for r in rows):
            wanted = {r['task_id']: r for r in rows if r['alias'] == alias}
            try:
                for task in self.tasks(alias):
                    row = wanted.get(task['id'])
                    if not row:
                        continue
                    if task['status'] == 'success':
                        link = (task.get('result_json') or {}).get('eft_link', '')
                        if not isinstance(link, str) or not link.startswith('https://'):
                            link = ''
                        self.store.finish(row['id'], True, 'Activation အောင်မြင်ပါပြီ။ ' + link)
                    elif task['status'] in ('failed', 'cancelled', 'timeout'):
                        self.store.finish(row['id'], False, 'Quota ပြန်ဖြည့်ပြီးပါပြီ။ ' + str(task.get('fail_code', '')))
                    wanted.pop(task['id'])
                    if not wanted:
                        break
            except Exception:
                print('Task polling failed; will retry. No quota changed.', flush=True)
        for row in self.store.db.execute("SELECT * FROM orders WHERE state IN ('success','refunded') AND notified=0").fetchall():
            try:
                self.send(row['user'], f"Order {row['id']}: {row['note']}")
                with self.store.db:
                    self.store.db.execute('UPDATE orders SET notified=1 WHERE id=?', (row['id'],))
            except Exception:
                print('Notification deferred.', flush=True)

    def run(self):
        last_poll = 0
        while True:
            if time.monotonic() - last_poll >= 15:
                self.poll()
                last_poll = time.monotonic()
            row = self.store.db.execute("SELECT v FROM settings WHERE k='offset'").fetchone()
            try:
                updates = self.http.telegram('getUpdates', {'offset': int(row['v']) if row else 0,
                    'timeout': 5, 'allowed_updates': ['message']})
            except Exception:
                print('Telegram polling failed; retrying.', flush=True)
                time.sleep(5)
                continue
            for update in updates:
                # Consume before processing: never replay a credential submission after a crash.
                with self.store.db:
                    self.store.db.execute("INSERT OR REPLACE INTO settings VALUES('offset',?)",
                                          (str(update['update_id'] + 1),))
                message = update.get('message', {})
                try:
                    self.message(message)
                except Exception as exc:
                    if message.get('chat', {}).get('type') == 'private':
                        if isinstance(exc, ValueError):
                            msg = str(exc) if not isinstance(exc, json.JSONDecodeError) else 'Service response မမှန်ပါ။'
                        elif isinstance(exc, APIError):
                            msg = f'Service error code: {exc.code}. /orders နဲ့စစ်ပါ။'
                        else:
                            msg = 'Connection အခက်အခဲရှိပါတယ်။ /orders နဲ့အရင်စစ်ပါ။'
                        try:
                            self.send(message['from']['id'], msg)
                        except Exception:
                            pass
                    print('Message handling failed (details hidden).', flush=True)


def main():
    os.umask(0o077)
    # Prevent a second bot process from polling/refunding concurrently.
    lock = open(ROOT / 'bot.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = json.loads((ROOT / 'config.json').read_text())
    if not config.get('telegram_token') or not config.get('admin_ids') or not config.get('real_keys'):
        raise SystemExit('Fill in config.json first.')
    if not config.get('pixel_base_url', '').startswith('https://'):
        raise SystemExit('pixel_base_url must use HTTPS.')
    os.chmod(ROOT / 'config.json', 0o600)
    store = Store(ROOT / 'state.sqlite3')
    print('Activation bot running.', flush=True)
    Bot(config, store, HTTP(config)).run()


if __name__ == '__main__':
    main()
