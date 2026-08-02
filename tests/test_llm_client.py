from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from services.llm_client import (
    AnthropicChatModel,
    AzureOpenAIChatModel,
    BedrockChatModel,
    CohereChatModel,
    GeminiChatModel,
    LLMError,
    OpenAICompatChatModel,
    create_chat_model,
)


class _Msg:
    def __init__(self, role: str, content: str) -> None:
        self.type = role
        self.content = content


def _capture(
    *, status_code: int = 200, payload: dict | None = None, text: str | None = None, headers: dict | None = None
):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        if text is not None:
            return httpx.Response(status_code, text=text, headers=headers or {}, request=request)
        return httpx.Response(status_code, json=payload or {}, headers=headers or {}, request=request)

    return httpx.MockTransport(handler), captured


def _openai_reply(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _anthropic_reply(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


class TestFactory:
    def test_returns_groq_openai_compatible_client(self, monkeypatch) -> None:
        monkeypatch.setattr("services.llm_client.settings.groq_model", "llama-3.3-70b-versatile")
        client = create_chat_model("groq")
        assert isinstance(client, OpenAICompatChatModel)
        assert client._url == "https://api.groq.com/openai/v1/chat/completions"
        assert client._model == "llama-3.3-70b-versatile"

    def test_returns_anthropic_client(self) -> None:
        assert isinstance(create_chat_model("anthropic"), AnthropicChatModel)

    def test_returns_azure_client(self) -> None:
        assert isinstance(create_chat_model("azure_openai"), AzureOpenAIChatModel)

    def test_returns_gemini_client(self) -> None:
        assert isinstance(create_chat_model("gemini"), GeminiChatModel)

    def test_returns_cohere_client(self) -> None:
        assert isinstance(create_chat_model("cohere"), CohereChatModel)

    def test_returns_bedrock_client(self) -> None:
        assert isinstance(create_chat_model("bedrock"), BedrockChatModel)

    def test_defaults_to_openai(self) -> None:
        assert isinstance(create_chat_model(""), OpenAICompatChatModel)
        assert isinstance(create_chat_model("bogus"), OpenAICompatChatModel)

    def test_ollama_has_no_authorization(self, monkeypatch) -> None:
        monkeypatch.setattr("services.llm_client.settings.ollama_model", "qwen2.5:3b")
        monkeypatch.setattr("services.llm_client.settings.ollama_base_url", "http://localhost:11434")
        client = create_chat_model("ollama")
        assert isinstance(client, OpenAICompatChatModel)
        assert client._api_key == ""


class TestOpenAICompat:
    @pytest.mark.asyncio
    async def test_ainvoke_posts_openai_shape(self) -> None:
        transport, captured = _capture(payload=_openai_reply("fixed"))
        client = OpenAICompatChatModel(
            model="llama-3.3-70b-versatile",
            api_key="gsk-test",
            base_url="https://api.groq.com/openai/v1",
            transport=transport,
        )
        result = await client.ainvoke([_Msg("system", "be terse"), _Msg("human", "fix it")])
        assert result.content == "fixed"

        req = captured["request"]
        assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert req.headers["authorization"] == "Bearer gsk-test"
        body = json.loads(req.content)
        assert body["model"] == "llama-3.3-70b-versatile"
        assert body["messages"] == [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "fix it"},
        ]

    @pytest.mark.asyncio
    async def test_ainvoke_maps_ai_roles(self) -> None:
        transport, captured = _capture(payload=_openai_reply("ok"))
        client = OpenAICompatChatModel(model="m", api_key="k", transport=transport)
        await client.ainvoke([_Msg("ai", "previous reply")])
        body = json.loads(captured["request"].content)
        assert body["messages"] == [{"role": "assistant", "content": "previous reply"}]

    @pytest.mark.asyncio
    async def test_no_api_key_omits_auth_header(self) -> None:
        transport, captured = _capture(payload=_openai_reply("ok"))
        client = OpenAICompatChatModel(model="m", transport=transport)
        await client.ainvoke([_Msg("human", "hi")])
        assert "authorization" not in captured["request"].headers

    @pytest.mark.asyncio
    async def test_non_2xx_raises_llm_error_with_retry_after(self) -> None:
        transport, captured = _capture(status_code=429, text="rate limited", headers={"retry-after": "13"})
        client = OpenAICompatChatModel(model="m", api_key="k", transport=transport)
        with pytest.raises(LLMError) as excinfo:
            await client.ainvoke([_Msg("human", "hi")])
        assert excinfo.value.status_code == 429
        assert excinfo.value.headers["retry-after"] == "13"
        assert "429" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_non_json_2xx_raises_llm_error(self) -> None:
        transport, _ = _capture(status_code=200, text="<html>oops</html>")
        client = OpenAICompatChatModel(model="m", api_key="k", transport=transport)
        with pytest.raises(LLMError) as excinfo:
            await client.ainvoke([_Msg("human", "hi")])
        assert "non-JSON" in str(excinfo.value)


class TestAzure:
    @pytest.mark.asyncio
    async def test_uses_deployment_url_and_api_key_header(self) -> None:
        transport, captured = _capture(payload=_openai_reply("fixed"))
        client = AzureOpenAIChatModel(
            endpoint="https://acme.openai.azure.com",
            deployment="gpt-4o-dep",
            api_version="2024-08-01-preview",
            api_key="az-key",
            transport=transport,
        )
        result = await client.ainvoke([_Msg("human", "hi")])
        assert result.content == "fixed"
        req = captured["request"]
        assert "/openai/deployments/gpt-4o-dep/chat/completions" in str(req.url)
        assert "api-version=2024-08-01-preview" in str(req.url)
        assert req.headers["api-key"] == "az-key"
        body = json.loads(req.content)
        assert "model" not in body


class TestAnthropic:
    @pytest.mark.asyncio
    async def test_splits_system_and_parses_text_blocks(self) -> None:
        transport, captured = _capture(payload=_anthropic_reply("here is the fix"))
        client = AnthropicChatModel(model="claude-x", api_key="sk-ant", transport=transport)
        result = await client.ainvoke([_Msg("system", "rules"), _Msg("human", "fix it")])
        assert result.content == "here is the fix"
        req = captured["request"]
        assert str(req.url) == "https://api.anthropic.com/v1/messages"
        assert req.headers["x-api-key"] == "sk-ant"
        assert req.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(req.content)
        assert body["system"] == "rules"
        assert body["messages"] == [{"role": "user", "content": "fix it"}]
        assert body["max_tokens"] > 0


class TestGemini:
    @pytest.mark.asyncio
    async def test_key_in_url_and_parses_candidates(self) -> None:
        transport, captured = _capture(payload={"candidates": [{"content": {"parts": [{"text": "gen fix"}]}}]})
        client = GeminiChatModel(model="gemini-2.0-flash", api_key="g-key", transport=transport)
        result = await client.ainvoke([_Msg("human", "fix it")])
        assert result.content == "gen fix"
        req = captured["request"]
        assert "key=g-key" in str(req.url)
        assert "gemini-2.0-flash" in str(req.url)
        body = json.loads(req.content)
        assert body["contents"] == [{"role": "user", "parts": [{"text": "fix it"}]}]


class TestCohere:
    @pytest.mark.asyncio
    async def test_builds_chat_history_and_preamble(self) -> None:
        transport, captured = _capture(payload={"text": "cohere fix"})
        client = CohereChatModel(model="command-r-plus", api_key="c-key", transport=transport)
        result = await client.ainvoke([_Msg("system", "be brief"), _Msg("human", "fix it")])
        assert result.content == "cohere fix"
        req = captured["request"]
        assert str(req.url) == "https://api.cohere.com/v1/chat"
        assert req.headers["authorization"] == "Bearer c-key"
        body = json.loads(req.content)
        assert body["model"] == "command-r-plus"
        assert body["message"] == "fix it"
        assert body["preamble"] == "be brief"


class TestBedrock:
    @pytest.mark.asyncio
    async def test_signs_request_and_parses_anthropic_body(self) -> None:
        transport, captured = _capture(payload=_anthropic_reply("aws fix"))
        client = BedrockChatModel(
            model="anthropic.claude-3-5-sonnet-20241022-v2:0",
            region="us-east-1",
            access_key_id="AKID",
            secret_access_key="secret",
            transport=transport,
        )
        result = await client.ainvoke([_Msg("human", "fix it")])
        assert result.content == "aws fix"
        req = captured["request"]
        assert req.url.host == "bedrock-runtime.us-east-1.amazonaws.com"
        assert req.url.path == "/model/anthropic.claude-3-5-sonnet-20241022-v2:0/invoke"
        assert req.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
        body_hash = hashlib.sha256(req.content).hexdigest()
        assert req.headers["x-amz-content-sha256"] == body_hash
        body = json.loads(req.content)
        assert body["anthropic_version"] == "bedrock-2023-05-31"
        assert body["messages"] == [{"role": "user", "content": "fix it"}]
