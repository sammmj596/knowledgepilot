from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.config import MCP_ENABLED_TOOLS, TAVILY_API_KEY

logger = logging.getLogger(__name__)

_mcp_tools: list[BaseTool] | None = None
_mcp_load_error: str | None = None


def _mcp_client_config() -> dict[str, Any] | None:
    if not TAVILY_API_KEY:
        return None
    return {
        "tavily": {
            "transport": "http",
            "url": (
                "https://mcp.tavily.com/mcp/?tavilyApiKey="
                f"{quote(TAVILY_API_KEY, safe='')}"
            ),
        }
    }


def _filter_tools(tools: list[BaseTool]) -> list[BaseTool]:
    if not MCP_ENABLED_TOOLS:
        return tools
    allowed = {name.strip() for name in MCP_ENABLED_TOOLS.split() if name.strip()}
    if not allowed:
        return tools
    return [tool for tool in tools if tool.name in allowed]


async def _load_mcp_tools_async() -> list[BaseTool]:
    config = _mcp_client_config()
    if not config:
        return []
    client = MultiServerMCPClient(config)
    tools = await client.get_tools()
    return _filter_tools(tools)


def get_mcp_tools() -> list[BaseTool]:
    global _mcp_tools, _mcp_load_error
    if _mcp_tools is not None:
        return _mcp_tools
    try:
        _mcp_tools = asyncio.run(_load_mcp_tools_async())
        if _mcp_tools:
            names = [t.name for t in _mcp_tools]
            logger.info("Loaded MCP tools: %s", ", ".join(names))
    except Exception as exc:
        _mcp_load_error = str(exc)
        logger.warning("Failed to load MCP tools: %s", exc)
        _mcp_tools = []
    return _mcp_tools


def mcp_tools_available() -> bool:
    return bool(get_mcp_tools())


def mcp_load_error() -> str | None:
    get_mcp_tools()
    return _mcp_load_error
