from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120.0

# Chat completion output ceiling for providers that require max_tokens.
_MAX_TOKENS = 4096


class LLMResponse:
    """Minimal duck-typed response so callers can read ``.content``.

    Only the ``content`` attribute is consumed upstream (see
    ``nodes._coerce_content``), so a plain string is sufficient.
    """

    def __init__(self, content: str) -> None:
        self.content = content


class LLMError(RuntimeError):
    """A provider rejected the request (non-2xx) or the reply was malformed.

    Carries ``status_code``/``headers`` so ``services.rate_limiter`` can treat
    429s and transient 5xx as retryable and honour ``Retry-After``.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        headers: httpx.Headers | dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers


class _BaseChatModel:
    """Shared HTTP plumbing for the direct-LLM clients.

    ``transport`` is only used by tests (httpx.MockTransport); production calls
    open a normal connection pool.
    """

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._timeout = timeout
        self._transport = transport

    async def _post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        *,
        data: bytes | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            try:
                if data is not None:
                    response = await client.post(url, headers=headers, content=data)
                else:
                    response = await client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                raise TimeoutError(f"LLM request timed out: {exc}") from exc
            except httpx.TransportError as exc:
                raise ConnectionError(f"LLM request failed: {exc}") from exc
        if response.status_code >= 400:
            raise LLMError(
                f"LLM provider returned HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
                headers=response.headers,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise LLMError(
                f"LLM provider returned a non-JSON response: {response.text[:200]}",
                status_code=response.status_code,
            ) from exc


def _to_openai_message(message: Any) -> dict[str, Any]:
    """Map a langchain-style message to OpenAI chat format without importing langchain."""
    role = getattr(message, "type", "user")
    if role == "human":
        role = "user"
    elif role == "ai":
        role = "assistant"
    elif role != "system":
        role = "user"
    return {"role": role, "content": getattr(message, "content", "")}


def _split_system_body(messages: list[Any]) -> tuple[str, list[dict[str, str]]]:
    """Split langchain-style messages into (system text, [{role, content}])."""
    system_parts: list[str] = []
    body: list[dict[str, str]] = []
    for message in messages:
        role = getattr(message, "type", "user")
        content = getattr(message, "content", "")
        if not isinstance(content, str):
            content = str(content)
        if role == "system":
            system_parts.append(content)
        elif role == "ai":
            body.append({"role": "assistant", "content": content})
        else:
            body.append({"role": "user", "content": content})
    return "\n\n".join(system_parts), body


def _openai_response_text(data: dict[str, Any]) -> str:
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected chat completion response: {data}") from exc


def _anthropic_response_text(data: dict[str, Any]) -> str:
    try:
        blocks = data["content"]
    except (KeyError, TypeError) as exc:
        raise LLMError(f"Unexpected Anthropic-style response: {data}") from exc
    return "".join(block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text")


class OpenAICompatChatModel(_BaseChatModel):
    """Minimal OpenAI-compatible chat client.

    Used by OpenAI, Groq, DeepSeek, xAI, Together, Mistral, and Ollama (all
    speak the ``/chat/completions`` protocol).
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        url: str = "",
        extra_headers: dict[str, str] | None = None,
        include_model: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(timeout=timeout, transport=transport)
        self._model = model
        self._api_key = api_key
        self._url = url or f"{base_url.rstrip('/')}/chat/completions"
        self._extra_headers = dict(extra_headers or {})
        self._include_model = include_model

    async def ainvoke(self, messages: list[Any]) -> LLMResponse:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)
        payload: dict[str, Any] = {"messages": [_to_openai_message(m) for m in messages]}
        if self._include_model:
            payload["model"] = self._model
        data = await self._post(self._url, headers, payload)
        return LLMResponse(_openai_response_text(data))


class AzureOpenAIChatModel(OpenAICompatChatModel):
    """Azure OpenAI uses the deployment in the URL and an ``api-key`` header."""

    def __init__(
        self,
        *,
        endpoint: str,
        deployment: str,
        api_version: str,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        super().__init__(
            model=deployment,
            url=url,
            extra_headers={"api-key": api_key},
            include_model=False,
            timeout=timeout,
            transport=transport,
        )


class AnthropicChatModel(_BaseChatModel):
    """Direct client for Anthropic's native Messages API."""

    _API_VERSION = "2023-06-01"
    _URL = "https://api.anthropic.com/v1/messages"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(timeout=timeout, transport=transport)
        self._model = model
        self._api_key = api_key

    async def ainvoke(self, messages: list[Any]) -> LLMResponse:
        system, body = _split_system_body(messages)
        payload: dict[str, Any] = {"model": self._model, "max_tokens": _MAX_TOKENS, "messages": body}
        if system:
            payload["system"] = system
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": self._API_VERSION,
        }
        data = await self._post(self._URL, headers, payload)
        return LLMResponse(_anthropic_response_text(data))


class GeminiChatModel(_BaseChatModel):
    """Direct client for Google Gemini's generateContent REST API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(timeout=timeout, transport=transport)
        self._model = model
        self._api_key = api_key

    async def ainvoke(self, messages: list[Any]) -> LLMResponse:
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = getattr(message, "type", "human")
            gemini_role = "model" if role == "ai" else "user"
            content = getattr(message, "content", "")
            if not isinstance(content, str):
                content = str(content)
            contents.append({"role": gemini_role, "parts": [{"text": content}]})
        payload = {"contents": contents}
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{quote(self._model, safe='')}"
            f":generateContent?key={self._api_key}"
        )
        data = await self._post(url, {"Content-Type": "application/json"}, payload)
        try:
            candidates = data["candidates"]
            text = "".join(part.get("text", "") for part in candidates[0]["content"]["parts"] if isinstance(part, dict))
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected Gemini response: {data}") from exc
        return LLMResponse(text)


class CohereChatModel(_BaseChatModel):
    """Direct client for Cohere's v1 chat API (``/v1/chat``)."""

    _URL = "https://api.cohere.com/v1/chat"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(timeout=timeout, transport=transport)
        self._model = model
        self._api_key = api_key

    async def ainvoke(self, messages: list[Any]) -> LLMResponse:
        preamble = ""
        chat_history: list[dict[str, str]] = []
        last_user = ""
        for message in messages:
            role = getattr(message, "type", "user")
            content = getattr(message, "content", "")
            if not isinstance(content, str):
                content = str(content)
            if role == "system":
                preamble = (preamble + "\n" if preamble else "") + content
            elif role == "ai":
                chat_history.append({"role": "CHATBOT", "message": content})
            else:
                if last_user:
                    chat_history.append({"role": "USER", "message": last_user})
                last_user = content
        payload: dict[str, Any] = {"model": self._model, "message": last_user or "Reply."}
        if preamble:
            payload["preamble"] = preamble
        if chat_history:
            payload["chat_history"] = chat_history
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"}
        data = await self._post(self._URL, headers, payload)
        text = data.get("text", "") if isinstance(data, dict) else ""
        return LLMResponse(text)


class BedrockChatModel(_BaseChatModel):
    """Direct client for AWS Bedrock's Claude ``InvokeModel`` endpoint.

    Signs the request with AWS Signature V4 (no boto3 dependency). Best-effort:
    it has not been verified against a live account — only request-shape tested.
    """

    _SERVICE = "bedrock"
    _HOST_TEMPLATE = "bedrock-runtime.{region}.amazonaws.com"

    def __init__(
        self,
        *,
        model: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(timeout=timeout, transport=transport)
        self._model = model
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._session_token = session_token

    async def ainvoke(self, messages: list[Any]) -> LLMResponse:
        system, body = _split_system_body(messages)
        payload: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": _MAX_TOKENS,
            "messages": body,
        }
        if system:
            payload["system"] = system
        raw_body = json.dumps(payload).encode("utf-8")

        host = self._HOST_TEMPLATE.format(region=self._region)
        path = f"/model/{self._model}/invoke"
        headers = _sign_aws_request(
            method="POST",
            service=self._SERVICE,
            region=self._region,
            host=host,
            canonical_uri=path,
            canonical_query="",
            headers={"content-type": "application/json", "accept": "application/json"},
            payload=raw_body,
            access_key=self._access_key_id,
            secret_key=self._secret_access_key,
            session_token=self._session_token,
        )
        data = await self._post(f"https://{host}{path}", headers, data=raw_body)
        return LLMResponse(_anthropic_response_text(data))


def _sign_aws_request(
    *,
    method: str,
    service: str,
    region: str,
    host: str,
    canonical_uri: str,
    canonical_query: str,
    headers: dict[str, str],
    payload: bytes,
    access_key: str,
    secret_key: str,
    session_token: str = "",
) -> dict[str, str]:
    """Apply AWS Signature V4 and return the request headers (in place of boto3)."""
    now = datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()

    signed = dict(headers)
    signed["host"] = host
    signed["x-amz-date"] = amz_date
    signed["x-amz-content-sha256"] = payload_hash
    if session_token:
        signed["x-amz-security-token"] = session_token

    canonical_headers = "".join(f"{key.lower()}:{str(value).strip()}\n" for key, value in sorted(signed.items()))
    signed_headers = ";".join(key.lower() for key in sorted(signed))

    canonical_request = "\n".join(
        [method.upper(), canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, credential_scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )

    k_date = hmac.new(("AWS4" + secret_key).encode(), date_stamp.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode(), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode(), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    signed["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return signed


def create_chat_model(provider: str) -> Any:
    """Build a direct-HTTP chat model for ``provider`` (no langchain packages)."""
    provider = (provider or "").strip().lower()

    if provider == "anthropic":
        return AnthropicChatModel(model=settings.anthropic_model, api_key=settings.anthropic_api_key)
    if provider == "bedrock":
        return BedrockChatModel(
            model=settings.bedrock_model,
            region=settings.bedrock_region,
            access_key_id=settings.bedrock_aws_access_key_id,
            secret_access_key=settings.bedrock_aws_secret_access_key,
        )
    if provider == "azure_openai":
        return AzureOpenAIChatModel(
            endpoint=settings.azure_openai_endpoint,
            deployment=settings.azure_openai_deployment,
            api_version=settings.azure_openai_api_version,
            api_key=settings.azure_openai_api_key,
        )
    if provider == "gemini":
        return GeminiChatModel(model=settings.gemini_model, api_key=settings.gemini_api_key)
    if provider == "mistral":
        return OpenAICompatChatModel(
            model=settings.mistral_model, api_key=settings.mistral_api_key, base_url="https://api.mistral.ai/v1"
        )
    if provider == "cohere":
        return CohereChatModel(model=settings.cohere_model, api_key=settings.cohere_api_key)
    if provider == "groq":
        return OpenAICompatChatModel(
            model=settings.groq_model, api_key=settings.groq_api_key, base_url="https://api.groq.com/openai/v1"
        )
    if provider == "together":
        return OpenAICompatChatModel(
            model=settings.together_model,
            api_key=settings.together_api_key,
            base_url="https://api.together.xyz/v1",
        )
    if provider == "deepseek":
        return OpenAICompatChatModel(
            model=settings.deepseek_model, api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url
        )
    if provider == "xai":
        return OpenAICompatChatModel(
            model=settings.xai_model, api_key=settings.xai_api_key, base_url=settings.xai_base_url
        )
    if provider == "ollama":
        return OpenAICompatChatModel(model=settings.ollama_model, base_url=f"{settings.ollama_base_url.rstrip('/')}/v1")
    # Default: OpenAI
    return OpenAICompatChatModel(model=settings.openai_model, api_key=settings.openai_api_key)
