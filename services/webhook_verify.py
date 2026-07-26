from __future__ import annotations

import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)


class WebhookVerificationError(Exception):
    pass


def verify_hmac_signature(
    payload_body: bytes,
    signature_header: str | None,
    secret: str,
    algorithm: str = "sha256",
) -> bool:
    if not signature_header:
        raise WebhookVerificationError("Missing signature header")

    if not secret:
        raise WebhookVerificationError("Webhook secret not configured")

    prefix = f"{algorithm}="
    if signature_header.startswith(prefix):
        signature_hex = signature_header[len(prefix) :]
    else:
        signature_hex = signature_header

    expected = hmac.new(
        secret.encode("utf-8"),
        payload_body,
        getattr(hashlib, algorithm),
    ).hexdigest()

    if not hmac.compare_digest(signature_hex, expected):
        raise WebhookVerificationError("Invalid signature")

    return True


def verify_forgejo_signature(
    payload_body: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    return verify_hmac_signature(
        payload_body, signature_header, secret, algorithm="sha256"
    )


def verify_github_signature(
    payload_body: bytes,
    signature_256: str | None,
    signature_1: str | None,
    secret: str,
) -> bool:
    if signature_256:
        return verify_hmac_signature(
            payload_body, signature_256, secret, algorithm="sha256"
        )
    if signature_1:
        logger.warning("Using SHA-1 signature fallback; upgrade to SHA-256")
        return verify_hmac_signature(
            payload_body, signature_1, secret, algorithm="sha1"
        )
    raise WebhookVerificationError("Missing GitHub signature header")
