"""显式传 secret 的 Fernet 加解密工具(用于模型 API key 的落地加密)。

密钥派生算法与 StaffDeck `app/security/encryption.py` 完全一致:
key = urlsafe_b64encode(sha256(secret.encode("utf-8")).digest())
同一个 secret 串在任何项目中派生出同一把 key,密文可跨项目互通。
唯一剥离点:原实现从 `app.config.get_settings().app_secret` 取密钥,
这里改为由调用方显式传入。
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def derive_fernet_key(secret: str) -> bytes:
    """从任意 secret 串派生 Fernet key:b64(sha256(secret)),位兼容原实现。"""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


def encrypt_secret(value: str, key: bytes) -> str:
    return Fernet(key).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str, key: bytes) -> str:
    if not value:
        return ""
    try:
        return Fernet(key).decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Secret cannot be decrypted with the provided secret") from exc


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}-****{value[-4:]}"
