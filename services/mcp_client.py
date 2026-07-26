from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class MCPClientError(Exception):
    pass


class MCPClient:
    def __init__(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        self._server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
        )
        self._session: ClientSession | None = None
        self._exit_stack: Any = None

    async def connect(self) -> None:
        from contextlib import AsyncExitStack

        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(self._server_params)
        )
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        self._session = session
        await session.initialize()
        logger.info("MCP client connected to server")

    async def disconnect(self) -> None:
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
            logger.info("MCP client disconnected")

    async def send_notification(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        session = self._session
        if session is None:
            raise MCPClientError("MCP client not connected")

        try:
            result = await session.call_tool(tool_name, arguments=arguments)

            if result.isError:
                error_parts: list[str] = []
                for block in result.content:
                    if isinstance(block, types.TextContent):
                        error_parts.append(block.text)
                error_msg = (
                    "\n".join(error_parts) if error_parts else "Unknown MCP error"
                )
                raise MCPClientError(f"MCP server error: {error_msg}")

            content_parts: list[str] = []
            for block in result.content:
                if isinstance(block, types.TextContent):
                    content_parts.append(block.text)
            return "\n".join(content_parts) if content_parts else "Notification sent"

        except MCPClientError:
            raise
        except Exception as e:
            logger.error("Failed to send MCP notification: %s", e)
            return f"Notification failed: {e}"

    async def send_alert(self, arguments: dict[str, Any]) -> str:
        return await self.send_notification("send_alert", arguments)

    async def __aenter__(self) -> MCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()


@asynccontextmanager
async def create_mcp_client(
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
) -> AsyncIterator[MCPClient]:
    client = MCPClient(command=command, args=args, env=env)
    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()
