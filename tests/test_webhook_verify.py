from __future__ import annotations

import hashlib
import hmac

import pytest

from services.webhook_verify import (
    WebhookVerificationError,
    verify_forgejo_signature,
    verify_github_signature,
    verify_hmac_signature,
)

SECRET = "test-secret-key-123"
PAYLOAD = b'{"action":"completed"}'


def _sign(payload: bytes, secret: str, algorithm: str = "sha256") -> str:
    digest = hmac.new(secret.encode(), payload, getattr(hashlib, algorithm)).hexdigest()
    return f"{algorithm}={digest}"


class TestVerifyHmacSignature:
    def test_valid_signature(self) -> None:
        sig = _sign(PAYLOAD, SECRET)
        assert verify_hmac_signature(PAYLOAD, sig, SECRET) is True

    def test_invalid_signature_raises(self) -> None:
        with pytest.raises(WebhookVerificationError, match="Invalid signature"):
            verify_hmac_signature(PAYLOAD, "sha256=00000000", SECRET)

    def test_missing_signature_raises(self) -> None:
        with pytest.raises(WebhookVerificationError, match="Missing signature"):
            verify_hmac_signature(PAYLOAD, None, SECRET)

    def test_missing_secret_raises(self) -> None:
        sig = _sign(PAYLOAD, SECRET)
        with pytest.raises(WebhookVerificationError, match="not configured"):
            verify_hmac_signature(PAYLOAD, sig, "")

    def test_without_prefix(self) -> None:
        digest = hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha256).hexdigest()
        assert verify_hmac_signature(PAYLOAD, digest, SECRET) is True


class TestVerifyForgejoSignature:
    def test_valid(self) -> None:
        sig = _sign(PAYLOAD, SECRET)
        assert verify_forgejo_signature(PAYLOAD, sig, SECRET) is True

    def test_invalid_raises(self) -> None:
        with pytest.raises(WebhookVerificationError, match="Invalid signature"):
            verify_forgejo_signature(PAYLOAD, "sha256=bad", SECRET)

    def test_missing_header(self) -> None:
        with pytest.raises(WebhookVerificationError):
            verify_forgejo_signature(PAYLOAD, None, SECRET)


class TestVerifyGithubSignature:
    def test_valid_sha256(self) -> None:
        sig = _sign(PAYLOAD, SECRET, "sha256")
        assert verify_github_signature(PAYLOAD, sig, None, SECRET) is True

    def test_valid_sha1_fallback(self) -> None:
        sig = _sign(PAYLOAD, SECRET, "sha1")
        assert verify_github_signature(PAYLOAD, None, sig, SECRET) is True

    def test_missing_both_headers(self) -> None:
        with pytest.raises(WebhookVerificationError, match="Missing GitHub"):
            verify_github_signature(PAYLOAD, None, None, SECRET)

    def test_sha256_takes_priority(self) -> None:
        sig_256 = _sign(PAYLOAD, SECRET, "sha256")
        sig_1 = _sign(PAYLOAD, "wrong-secret", "sha1")
        assert verify_github_signature(PAYLOAD, sig_256, sig_1, SECRET) is True
