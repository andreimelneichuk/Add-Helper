import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12


class CipherConfigError(ValueError):
    """Текст ошибки никогда не содержит значение ключа."""


class DecryptionFailed(Exception):
    pass


class AesGcmCipher:
    """AES-256-GCM с версиями ключей: шифрует первым, расшифровывает любым.

    Контекст связывает шифртекст с его записью, поэтому токен чужого кабинета,
    подставленный в базе, не расшифруется.
    """

    def __init__(self, keys: list[tuple[str, bytes]]) -> None:
        if not keys:
            raise CipherConfigError("Не задан ни один ключ шифрования")
        if any(len(key) != KEY_BYTES for _, key in keys):
            raise CipherConfigError("Ключ шифрования должен занимать 32 байта")
        self._active = keys[0][0]
        self._keys = {key_id: AESGCM(key) for key_id, key in keys}

    @classmethod
    def from_config(cls, value: str) -> "AesGcmCipher":
        """Формат: `новый:base64,старый:base64`."""
        keys: list[tuple[str, bytes]] = []
        for item in filter(None, (part.strip() for part in value.split(","))):
            key_id, separator, encoded = item.partition(":")
            if not separator or not key_id or "." in key_id:
                raise CipherConfigError("Ожидается формат идентификатор:base64")
            try:
                keys.append((key_id, base64.b64decode(encoded, validate=True)))
            except ValueError:
                raise CipherConfigError("Ключ шифрования не в base64") from None
        return cls(keys)

    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._keys[self._active].encrypt(nonce, plaintext.encode(), context.encode())
        return f"{self._active}.{base64.b64encode(nonce + sealed).decode()}"

    def decrypt(self, ciphertext: str, context: str) -> str:
        key_id, _, encoded = ciphertext.partition(".")
        key = self._keys.get(key_id)
        if key is None:
            raise DecryptionFailed("Нет ключа с таким идентификатором")
        try:
            raw = base64.b64decode(encoded, validate=True)
            return key.decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], context.encode()).decode()
        except (InvalidTag, ValueError):
            raise DecryptionFailed("Шифртекст не расшифровывается") from None
