from __future__ import annotations

import hashlib
import hmac
import json

from app.services.telegram_delivery.models import TOKEN, TelegramStartUpdate


def token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def private_id_hash(value: int | str) -> str:
    return hashlib.sha256(str(value).encode("ascii")).hexdigest()


def verify_webhook_secret(expected: str, supplied: str | None) -> bool:
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def parse_start_update(payload: object, *, max_body_bytes: int = 16_384) -> TelegramStartUpdate:
    try:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_update") from exc
    if len(encoded) > max_body_bytes or not isinstance(payload, dict):
        raise ValueError("invalid_update")
    update_id = payload.get("update_id")
    message = payload.get("message")
    if not isinstance(update_id, int) or update_id < 0 or not isinstance(message, dict):
        raise ValueError("invalid_update")
    chat = message.get("chat")
    text = message.get("text")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        raise ValueError("private_chat_required")
    chat_id = chat.get("id")
    if not isinstance(chat_id, int) or chat_id <= 0 or not isinstance(text, str):
        raise ValueError("invalid_update")
    parts = text.split(" ")
    if len(parts) != 2 or parts[0] != "/start" or not TOKEN.fullmatch(parts[1]):
        raise ValueError("invalid_start_payload")
    return TelegramStartUpdate(update_id, chat_id, parts[1])
