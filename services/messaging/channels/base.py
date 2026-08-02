from __future__ import annotations

from urllib.parse import urlparse

from services.messaging.formats import AlertPayload


class BaseChannel:
    name: str = ""

    def _endpoint(self) -> str:
        raise NotImplementedError

    def build_body(self, payload: AlertPayload) -> dict[str, str]:
        raise NotImplementedError

    async def _post(self, url: str, body: dict[str, str]) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()

    async def send(self, payload: AlertPayload) -> None:
        await self._post(self._endpoint(), self.build_body(payload))


class WebhookChannel(BaseChannel):
    def __init__(self, webhook_url: str = "") -> None:
        self.webhook_url = webhook_url

    def _endpoint(self) -> str:
        if not self.webhook_url:
            raise ValueError(f"{self.name} webhook URL not configured")
        parsed = urlparse(self.webhook_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"{self.name} webhook URL must be a valid http(s) URL")
        return self.webhook_url
