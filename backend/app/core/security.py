from __future__ import annotations

import hashlib
from itsdangerous import URLSafeTimedSerializer

from app.core.config import settings


def normalize_email(email: str) -> str:
    return email.strip().lower()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def build_user_identity(email: str, ip: str, fingerprint: str | None) -> str:
    normalized = normalize_email(email)
    raw = f"{normalized}|{ip}|{fingerprint or ''}"
    return sha256_text(raw)


def signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.asset_signing_secret, salt='siteformo-demo')


def session_signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.asset_signing_secret, salt='siteformo-demo-session')


def sign_asset(storage_key: str) -> str:
    return signer().dumps({'k': storage_key})


def unsign_asset(token: str, max_age_seconds: int) -> str:
    data = signer().loads(token, max_age=max_age_seconds)
    return str(data['k'])


def sign_demo_session(request_id: str) -> str:
    return session_signer().dumps({'r': request_id})


def unsign_demo_session(token: str, max_age_seconds: int) -> str:
    data = session_signer().loads(token, max_age=max_age_seconds)
    return str(data['r'])
