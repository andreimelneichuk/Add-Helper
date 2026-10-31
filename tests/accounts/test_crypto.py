import base64
import os

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adhelper.accounts.adapters.crypto import AesGcmCipher, CipherConfigError, DecryptionFailed

KEY_A = base64.b64encode(b"a" * 32).decode()
KEY_B = base64.b64encode(b"b" * 32).decode()


@given(st.text(), st.text())
def test_round_trip(plaintext: str, context: str) -> None:
    cipher = AesGcmCipher([("a", os.urandom(32))])
    assert cipher.decrypt(cipher.encrypt(plaintext, context), context) == plaintext


def test_ciphertext_hides_plaintext_and_differs_each_time() -> None:
    cipher = AesGcmCipher.from_config(f"a:{KEY_A}")
    first, second = cipher.encrypt("секрет", "c"), cipher.encrypt("секрет", "c")
    assert first != second
    assert "секрет" not in first


def test_token_moved_to_another_record_is_rejected() -> None:
    cipher = AesGcmCipher.from_config(f"a:{KEY_A}")
    with pytest.raises(DecryptionFailed):
        cipher.decrypt(cipher.encrypt("секрет", "кабинет-1"), "кабинет-2")


def test_tampered_ciphertext_is_rejected() -> None:
    cipher = AesGcmCipher.from_config(f"a:{KEY_A}")
    sealed = cipher.encrypt("секрет", "c")
    raw = bytearray(base64.b64decode(sealed.split(".")[1]))
    raw[-1] ^= 1
    with pytest.raises(DecryptionFailed):
        cipher.decrypt(f"a.{base64.b64encode(bytes(raw)).decode()}", "c")


def test_rotation_reads_old_and_writes_new() -> None:
    old = AesGcmCipher.from_config(f"a:{KEY_A}")
    rotated = AesGcmCipher.from_config(f"b:{KEY_B},a:{KEY_A}")
    assert rotated.decrypt(old.encrypt("секрет", "c"), "c") == "секрет"
    assert rotated.encrypt("секрет", "c").startswith("b.")


def test_removed_key_is_reported_not_guessed() -> None:
    old = AesGcmCipher.from_config(f"a:{KEY_A}")
    with pytest.raises(DecryptionFailed):
        AesGcmCipher.from_config(f"b:{KEY_B}").decrypt(old.encrypt("секрет", "c"), "c")


@pytest.mark.parametrize("config", ["", "без-разделителя", "a:не-base64!", "a:c2hvcnQ="])
def test_bad_config_fails_without_echoing_value(config: str) -> None:
    with pytest.raises(CipherConfigError) as error:
        AesGcmCipher.from_config(config)
    if config:
        assert config not in str(error.value)
