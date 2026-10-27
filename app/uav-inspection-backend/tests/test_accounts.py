import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from authapp import api


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='uav-accounts-')
        self.root = Path(self.temporary.name)
        self.path_patch = patch.object(api, 'backend_path', side_effect=lambda *parts: self.root.joinpath(*parts))
        self.path_patch.start()
        self.env = patch.dict('os.environ', {'UAV_URL_PREFIX': ''})
        self.env.start()
        self.app = FastAPI()
        self.app.include_router(api.router, prefix='/api/v1/auth')
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.path_patch.stop()
        self.temporary.cleanup()

    def login(self, username='admin', password='admin123', client=None):
        return (client or self.client).post('/api/v1/auth/login', json={'username': username, 'password': password})

    def test_default_account_session_uses_hashed_secrets_and_httponly_cookie(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['username'], 'admin')
        cookie = response.headers['set-cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=lax', cookie)
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 200)
        token = self.client.cookies.get(api.COOKIE_NAME)
        with closing(sqlite3.connect(self.root / 'auth/accounts.sqlite3')) as connection:
            password_hash = connection.execute('SELECT password_hash FROM users').fetchone()[0]
            stored_token = connection.execute('SELECT token_hash FROM sessions').fetchone()[0]
        self.assertNotIn('admin123', password_hash)
        self.assertTrue(api.verify_password('admin123', password_hash))
        self.assertNotEqual(stored_token, token)
        self.assertEqual(stored_token, hashlib.sha256(token.encode()).hexdigest())

    def test_wrong_password_and_unknown_username_do_not_create_sessions(self):
        a = self.login(password='incorrect')
        b = self.login(username='missing')
        self.assertEqual(a.status_code, 401)
        self.assertEqual(a.json(), b.json())
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 401)

    def test_registration_auto_login_and_persistence_across_clients(self):
        response = self.client.post('/api/v1/auth/register', json={'username': 'Inspector_01', 'password': 'review12345'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/v1/auth/me').json()['data']['username'], 'Inspector_01')
        with TestClient(self.app) as second:
            self.assertEqual(self.login('inspector_01', 'review12345', second).status_code, 200)
            self.assertEqual(second.get('/api/v1/auth/me').status_code, 200)

    def test_duplicate_and_reserved_usernames_are_case_insensitive(self):
        for name in ['ADMIN', 'admin']:
            self.assertEqual(self.client.post('/api/v1/auth/register', json={'username': name, 'password': 'newpass123'}).status_code, 409)
        self.client.post('/api/v1/auth/register', json={'username': 'reviewer', 'password': 'newpass123'})
        self.assertEqual(self.client.post('/api/v1/auth/register', json={'username': 'REVIEWER', 'password': 'another123'}).status_code, 409)

    def test_invalid_registration_and_oversized_password_are_rejected(self):
        for username, password in [('ab', 'password123'), ('bad name', 'password123'), ('reviewer', '1234567'), ('x' * 25, 'password123'), ('reviewer', 'x' * 129)]:
            self.assertEqual(self.client.post('/api/v1/auth/register', json={'username': username, 'password': password}).status_code, 422)
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 401)

    def test_session_expiration_is_enforced_server_side(self):
        self.login()
        with patch.object(api.time, 'time', return_value=time.time() + api.SESSION_SECONDS + 5):
            self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 401)

    def test_logout_revokes_the_cookie_even_if_the_old_token_is_reused(self):
        self.login()
        token = self.client.cookies.get(api.COOKIE_NAME)
        response = self.client.post('/api/v1/auth/logout')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Max-Age=0', response.headers['set-cookie'])
        self.assertEqual(self.client.get('/api/v1/auth/me', headers={'Cookie': f'{api.COOKIE_NAME}={token}'}).status_code, 401)

    def test_mounted_subpath_uses_matching_cookie_path_and_https_secure_flag(self):
        gateway = FastAPI()
        gateway.mount('/uav', self.app)
        with TestClient(gateway, base_url='https://example.test') as client:
            response = client.post('/uav/api/v1/auth/login', json={'username': 'admin', 'password': 'admin123'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('Path=/uav', response.headers['set-cookie'])
            self.assertIn('Secure', response.headers['set-cookie'])
            self.assertEqual(client.get('/uav/api/v1/auth/me').status_code, 200)
            self.assertEqual(client.post('/uav/api/v1/auth/logout').status_code, 200)
            self.assertEqual(client.get('/uav/api/v1/auth/me').status_code, 401)


if __name__ == '__main__':
    unittest.main()
