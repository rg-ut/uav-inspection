"""Persistent demo accounts and server-side sessions; no plaintext passwords or client-side tokens."""
import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from deployment_paths import backend_path

router = APIRouter()
COOKIE_NAME = 'uav_session'
SESSION_SECONDS = 24 * 60 * 60
PASSWORD_ROUNDS = 300_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PASSWORD_ROUNDS)
    return f'{PASSWORD_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}'


def verify_password(password: str, stored: str) -> bool:
    try:
        rounds, salt, digest = stored.split('$')
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), base64.b64decode(salt), int(rounds))
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


@contextmanager
def database():
    path = backend_path('auth', 'accounts.sqlite3')
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL, created_at INTEGER NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL)')
        # Seed only when absent; never reset an existing account's password.
        if not connection.execute('SELECT 1 FROM users WHERE username = ?', ('admin',)).fetchone():
            connection.execute('INSERT OR IGNORE INTO users (username, password_hash, created_at) VALUES (?, ?, ?)', ('admin', hash_password('admin123'), int(time.time())))
        connection.commit()
        yield connection
        connection.commit()
    finally:
        connection.close()


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


def cookie_path(request: Request) -> str:
    prefix = request.scope.get('root_path') or os.getenv('UAV_URL_PREFIX', '')
    return '/' + prefix.strip('/') if prefix.strip('/') else '/'


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(connection, user, request: Request, response: Response):
    now = int(time.time())
    connection.execute('DELETE FROM sessions WHERE expires_at <= ?', (now,))
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        connection.execute('DELETE FROM sessions WHERE token_hash = ?', (token_hash(previous),))
    token = secrets.token_urlsafe(32)
    connection.execute('INSERT INTO sessions VALUES (?, ?, ?)', (token_hash(token), user['id'], now + SESSION_SECONDS))
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, path=cookie_path(request), httponly=True, samesite='lax', secure=request.url.scheme == 'https')
    response.headers['Cache-Control'] = 'no-store'
    return {'code': 200, 'message': '登录成功', 'data': {'id': user['id'], 'username': user['username']}}


@router.post('/register')
def register(data: Credentials, request: Request, response: Response):
    username = data.username.strip()
    if not re.fullmatch(r'[A-Za-z0-9_]{3,24}', username):
        raise HTTPException(422, '用户名需为 3–24 位字母、数字或下划线')
    if len(data.password) < 8:
        raise HTTPException(422, '密码至少需要 8 位')
    with database() as connection:
        try:
            connection.execute('INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)', (username, hash_password(data.password), int(time.time())))
        except sqlite3.IntegrityError:
            raise HTTPException(409, '该用户名已被注册，请更换用户名或直接登录')
        user = connection.execute('SELECT id, username FROM users WHERE username = ?', (username,)).fetchone()
        return start_session(connection, user, request, response)


@router.post('/login')
def login(data: Credentials, request: Request, response: Response):
    with database() as connection:
        user = connection.execute('SELECT * FROM users WHERE username = ?', (data.username.strip(),)).fetchone()
        if not user or not verify_password(data.password, user['password_hash']):
            raise HTTPException(401, '用户名或密码错误')
        return start_session(connection, user, request, response)


@router.get('/me')
def me(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    with database() as connection:
        user = connection.execute('SELECT users.id, users.username FROM sessions JOIN users ON users.id = sessions.user_id WHERE token_hash = ? AND expires_at > ?', (token_hash(request.cookies.get(COOKIE_NAME, '')), int(time.time()))).fetchone()
        if not user:
            raise HTTPException(401, '请先登录')
        return {'code': 200, 'data': dict(user)}


@router.post('/logout')
def logout(request: Request, response: Response):
    with database() as connection:
        connection.execute('DELETE FROM sessions WHERE token_hash = ?', (token_hash(request.cookies.get(COOKIE_NAME, '')),))
    response.delete_cookie(COOKIE_NAME, path=cookie_path(request), httponly=True, samesite='lax', secure=request.url.scheme == 'https')
    response.headers['Cache-Control'] = 'no-store'
    return {'code': 200, 'message': '已退出登录'}
