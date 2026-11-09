import tempfile
import unittest
from pathlib import Path
from bot import APIError, Bot, Store


class FakeHTTP:
    def __init__(self):
        self.submissions = 0
        self.error = None
        self.messages = []
        self.task_status = 'success'

    def telegram(self, method, payload):
        self.messages.append(payload)
        return {}

    def pixel(self, path, payload=None, query=None):
        if path.endswith('/status'):
            return {'accept_new_tasks': True, 'require_totp': True}
        if path.endswith('/verify'):
            return {'remaining': 20}
        if path.endswith('/submit-task'):
            self.submissions += 1
            if self.error:
                raise self.error
            assert payload['key'] == 'PRIVATE-REAL-KEY'
            return {'task': {'id': 'task-1'}}
        if path.endswith('/tasks'):
            return {'tasks': [{'id': 'task-1', 'email': 'test@example.com',
                    'status': self.task_status, 'fail_code': 'E_AUTH_PASSWORD'}], 'total': 1}
        raise AssertionError(path)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.store = Store(self.path)
        self.key = self.store.mint('main', 2)
        self.store.redeem(1, self.key)
        self.http = FakeHTTP()
        self.bot = Bot({'admin_ids': [99], 'real_keys': {'main': 'PRIVATE-REAL-KEY'}}, self.store, self.http)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def submit(self):
        self.bot.submit(1, 'test@example.com\nsecret-password\nABCDEFGHIJKLMNOP234567ABCD')

    def test_key_bound_to_first_user(self):
        with self.assertRaises(ValueError):
            self.store.redeem(2, self.key)

    def test_success_charges_once_and_no_credentials_stored(self):
        self.submit()
        self.bot.poll()
        self.bot.poll()
        self.assertEqual(self.store.current(1)['remaining'], 1)
        dump = '\n'.join(self.store.db.iterdump())
        for secret in (self.key, 'PRIVATE-REAL-KEY', 'secret-password', 'ABCDEFGHIJKLMNOP234567ABCD'):
            self.assertNotIn(secret, dump)
            self.assertNotIn(secret, str(self.http.messages))

    def test_refund_idempotent(self):
        self.submit()
        self.http.task_status = 'failed'
        self.bot.poll()
        self.bot.poll()
        self.assertEqual(self.store.current(1)['remaining'], 2)

    def test_duplicate_submission_blocked(self):
        self.submit()
        with self.assertRaises(ValueError):
            self.submit()
        self.assertEqual(self.http.submissions, 1)
        self.assertEqual(self.store.current(1)['remaining'], 1)

    def test_timeout_does_not_refund_or_replay(self):
        self.http.error = TimeoutError()
        self.submit()
        self.assertEqual(self.store.current(1)['remaining'], 1)
        self.assertEqual(self.store.db.execute('SELECT state FROM orders').fetchone()[0], 'unknown')
        with self.assertRaises(ValueError):
            self.submit()
        self.assertEqual(self.http.submissions, 1)

    def test_explicit_rejection_refunds(self):
        self.http.error = APIError(30001)
        self.submit()
        self.assertEqual(self.store.current(1)['remaining'], 2)

    def test_generic_server_error_is_uncertain(self):
        self.http.error = APIError(50000)
        self.submit()
        self.assertEqual(self.store.current(1)['remaining'], 1)
        self.assertEqual(self.store.db.execute('SELECT state FROM orders').fetchone()[0], 'unknown')

    def test_crash_reservation_becomes_unknown(self):
        self.store.reserve(1, 'test@example.com')
        self.store.db.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.db.execute('SELECT state FROM orders').fetchone()[0], 'unknown')
        self.assertEqual(self.store.current(1)['remaining'], 1)

    def test_group_messages_ignored(self):
        self.bot.message({'chat': {'type': 'group'}, 'from': {'id': 99}, 'text': '/newkey main 10'})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM customer_keys').fetchone()[0], 1)

    def test_nonadmin_cannot_mint(self):
        self.bot.message({'chat': {'type': 'private'}, 'from': {'id': 1}, 'text': '/newkey main 10'})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM customer_keys').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
