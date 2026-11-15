"""End-to-end test: start server.py over stdio and exercise every primitive.

Run with:  python test_server.py
Uses the SDK's own client, so it checks the real protocol, not just the functions.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).parent / "server.py")


def result_data(result: Any) -> Any:
    """Decode a tool result: prefer structured content, else parse the text block."""
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if structured is not None:
        return structured.get("result", structured) if set(structured) == {"result"} else structured
    return json.loads(result.content[0].text)


def error_text(result: Any) -> str:
    """Assert that a tool call failed and return its message."""
    assert getattr(result, "is_error", getattr(result, "isError", False)), "expected a tool error"
    return result.content[0].text


async def run_tests() -> int:
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            checks = 0

            # Discovery: all three primitives are advertised.
            tools = {t.name for t in (await session.list_tools()).tools}
            assert tools == {"get_order", "list_recent_orders", "find_low_stock", "search_products"}, tools
            resources = {str(r.uri) for r in (await session.list_resources()).resources}
            assert "store://summary" in resources, resources
            prompts = {p.name for p in (await session.list_prompts()).prompts}
            assert "weekly_store_review" in prompts, prompts
            checks += 3

            # get_order: with and without prefix, then bad and unknown ids.
            order = result_data(await session.call_tool("get_order", {"order_id": "ORD-1003"}))
            assert order["id"] == "ORD-1003" and order["status"] == "refunded"
            assert order["items"][0]["title"] == "Steel Water Bottle"
            assert result_data(await session.call_tool("get_order", {"order_id": "1001"}))["total"] == 60.5
            assert "Invalid order_id" in error_text(await session.call_tool("get_order", {"order_id": "drop table"}))
            assert "No order found" in error_text(await session.call_tool("get_order", {"order_id": "ORD-9999"}))
            checks += 4

            # list_recent_orders: ordering, limit, status filter, validation.
            recent = result_data(await session.call_tool("list_recent_orders", {}))
            assert [o["id"] for o in recent] == ["ORD-1012", "ORD-1011", "ORD-1010", "ORD-1009", "ORD-1008"]
            paid = result_data(await session.call_tool("list_recent_orders", {"limit": 10, "status": "paid"}))
            assert {o["status"] for o in paid} == {"paid"} and len(paid) == 4
            assert "limit must be" in error_text(await session.call_tool("list_recent_orders", {"limit": 0}))
            assert "Invalid status" in error_text(await session.call_tool("list_recent_orders", {"status": "shipped"}))
            checks += 4

            # find_low_stock: threshold behaviour and validation.
            low = result_data(await session.call_tool("find_low_stock", {"threshold": 5}))
            assert [p["sku"] for p in low] == ["BTL-007", "NTB-004", "CRD-010"]
            out = result_data(await session.call_tool("find_low_stock", {"threshold": 0}))
            assert [p["sku"] for p in out] == ["BTL-007"]
            assert "threshold must be" in error_text(await session.call_tool("find_low_stock", {"threshold": -1}))
            checks += 3

            # search_products: multi-word AND match, no match, empty query.
            hits = result_data(await session.call_tool("search_products", {"query": "Wool Scarf"}))
            assert [p["sku"] for p in hits] == ["SCF-008"]
            assert result_data(await session.call_tool("search_products", {"query": "zzz"})) == []
            assert "must not be empty" in error_text(await session.call_tool("search_products", {"query": "  "}))
            checks += 3

            # Resource: summary JSON.
            read = await session.read_resource("store://summary")
            summary = json.loads(read.contents[0].text)
            assert summary["total_orders"] == 12 and summary["product_count"] == 10
            assert summary["orders_by_status"]["cancelled"] == 1
            assert summary["out_of_stock"] == ["BTL-007"]
            checks += 1

            # Prompt: renders and mentions the tools it relies on.
            prompt = await session.get_prompt("weekly_store_review", {"focus": "inventory"})
            text = prompt.messages[0].content.text
            assert "inventory" in text and "find_low_stock" in text and "store://summary" in text
            checks += 1

    print(f"All checks passed ({checks} check groups: discovery, tools, errors, resource, prompt).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run_tests()))
