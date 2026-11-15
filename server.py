"""MCP Server Starter: a read-only Model Context Protocol server for store data.

The file has two clearly separated layers:

1. Data access layer (StoreRepository): knows where the data lives and how to
   query it. It knows nothing about MCP.
2. MCP layer (the decorated functions at the bottom): validates input, calls
   the repository, and shapes results for an AI assistant.

To connect a real store, replace StoreRepository only. The MCP layer should not
need to change.

Run over stdio (what Claude Desktop uses):  python server.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# The official SDK renamed FastMCP to MCPServer in mcp 2.x. Support both.
# ToolError is the exception whose message is shown to the AI client; any other
# exception type is masked as a generic failure in mcp 2.x.
try:
    from mcp.server.mcpserver import MCPServer as FastMCP  # mcp >= 2
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover - depends on installed SDK version
    from mcp.server.fastmcp import FastMCP  # mcp 1.x
    from mcp.server.fastmcp.exceptions import ToolError

DATA_FILE = Path(os.environ.get("STORE_DATA_FILE", Path(__file__).parent / "sample_data.json"))

VALID_STATUSES = ("pending", "paid", "fulfilled", "cancelled", "refunded")
LOW_STOCK_ALERT_LEVEL = 5  # used by the store://summary resource
MAX_LIMIT = 50
MAX_QUERY_LENGTH = 100
ORDER_ID_PATTERN = re.compile(r"^(?:ORD-)?(\d{4,})$")


# ---------------------------------------------------------------------------
# Data access layer (no MCP code in here)
# ---------------------------------------------------------------------------
class StoreRepository:
    """Read-only access to orders and products.

    This sample implementation reads a local JSON file. To use real data,
    keep the method names and return shapes and change the bodies.

    Shopify Admin GraphQL API (note only, nothing here calls Shopify):
      - Replace get_order / list_orders with a query on the `orders` field.
        The Admin API exposes `order(id:)` and `orders(first:, query:, sortKey:)`.
      - Replace products and stock with the `products` field and its variants'
        inventory quantity.
      - Requests go to https://{shop}.myshopify.com/admin/api/{version}/graphql.json
        with an access token in the X-Shopify-Access-Token header.
      - Create the token with read-only scopes only (for example read_orders,
        read_products, read_inventory) and load it from an environment
        variable, never from source code.
      - Map the GraphQL response into the plain dicts returned below so the
        MCP layer stays unchanged. Check the current API docs for exact
        field names and version strings before building.
    """

    def __init__(self, path: Path) -> None:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        self.store_name: str = raw["store"]
        self._products: list[dict[str, Any]] = raw["products"]
        self._orders: list[dict[str, Any]] = raw["orders"]
        self._titles = {p["sku"]: p["title"] for p in self._products}

    def _with_titles(self, order: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of an order with product titles added to line items."""
        items = [{**i, "title": self._titles.get(i["sku"], "Unknown product")} for i in order["items"]]
        return {**order, "items": items}

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        """Return one order by its id, or None if it does not exist."""
        for order in self._orders:
            if order["id"] == order_id:
                return self._with_titles(order)
        return None

    def list_orders(self, limit: int, status: str | None) -> list[dict[str, Any]]:
        """Return the newest orders first, optionally filtered by status."""
        rows = [o for o in self._orders if status is None or o["status"] == status]
        rows.sort(key=lambda o: o["created_at"], reverse=True)
        return [self._with_titles(o) for o in rows[:limit]]

    def low_stock(self, threshold: int) -> list[dict[str, Any]]:
        """Return products with stock at or below the threshold, lowest first."""
        rows = [p for p in self._products if p["stock"] <= threshold]
        return sorted(rows, key=lambda p: (p["stock"], p["sku"]))

    def search_products(self, terms: list[str]) -> list[dict[str, Any]]:
        """Return products where every term appears in title, SKU, tags or text."""
        found = []
        for product in self._products:
            haystack = " ".join(
                [product["title"], product["sku"], product["description"], product["category"], *product["tags"]]
            ).lower()
            if all(term in haystack for term in terms):
                found.append(product)
        return found

    def summary(self) -> dict[str, Any]:
        """Return headline numbers for the whole store."""
        by_status = {s: 0 for s in VALID_STATUSES}
        revenue = 0.0
        for order in self._orders:
            by_status[order["status"]] += 1
            if order["status"] in ("paid", "fulfilled"):  # simple definition of revenue
                revenue += order["total"]
        return {
            "store": self.store_name,
            "currency": "USD",
            "total_orders": len(self._orders),
            "orders_by_status": by_status,
            "revenue_paid_and_fulfilled": round(revenue, 2),
            "product_count": len(self._products),
            "out_of_stock": [p["sku"] for p in self._products if p["stock"] == 0],
            "low_stock_alert_level": LOW_STOCK_ALERT_LEVEL,
            "low_stock_count": len(self.low_stock(LOW_STOCK_ALERT_LEVEL)),
        }


# ---------------------------------------------------------------------------
# MCP layer
# ---------------------------------------------------------------------------
repo = StoreRepository(DATA_FILE)
mcp = FastMCP("store-assistant")


@mcp.tool()
def get_order(order_id: str) -> dict[str, Any]:
    """Look up a single order by its id and return its full details.

    Args:
        order_id: The order id, for example "ORD-1003". The "ORD-" prefix is optional.

    Returns the status, customer, line items with product titles, and the total.
    """
    match = ORDER_ID_PATTERN.match(order_id.strip().upper())
    if not match:
        raise ToolError(f"Invalid order_id {order_id!r}. Expected a value like 'ORD-1003' or '1003'.")
    normalized = f"ORD-{match.group(1)}"
    order = repo.get_order(normalized)
    if order is None:
        raise ToolError(f"No order found with id {normalized}. Use list_recent_orders to see valid ids.")
    return order


@mcp.tool()
def list_recent_orders(limit: int = 5, status: str | None = None) -> list[dict[str, Any]]:
    """List the most recent orders, newest first, optionally filtered by status.

    Args:
        limit: How many orders to return, from 1 to 50. Defaults to 5.
        status: Optional filter. One of: pending, paid, fulfilled, cancelled, refunded.
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise ToolError(f"limit must be between 1 and {MAX_LIMIT}, got {limit}.")
    if status is not None:
        status = status.strip().lower()
        if status not in VALID_STATUSES:
            raise ToolError(f"Invalid status {status!r}. Choose one of: {', '.join(VALID_STATUSES)}.")
    return repo.list_orders(limit, status)


@mcp.tool()
def find_low_stock(threshold: int = 10) -> list[dict[str, Any]]:
    """Find products whose stock is at or below a threshold, lowest stock first.

    Args:
        threshold: Maximum stock level to include, from 0 to 10000. Defaults to 10.
            Use 0 to list only products that are out of stock.
    """
    if not 0 <= threshold <= 10_000:
        raise ToolError(f"threshold must be between 0 and 10000, got {threshold}.")
    return repo.low_stock(threshold)


@mcp.tool()
def search_products(query: str) -> list[dict[str, Any]]:
    """Search the product catalog by keyword.

    Every word in the query must match the product title, SKU, description,
    category, or tags. Matching is case-insensitive.

    Args:
        query: Search text, 1 to 100 characters, for example "wool scarf".
    """
    cleaned = query.strip().lower()
    if not cleaned:
        raise ToolError("query must not be empty.")
    if len(cleaned) > MAX_QUERY_LENGTH:
        raise ToolError(f"query is too long ({len(cleaned)} characters). Maximum is {MAX_QUERY_LENGTH}.")
    return repo.search_products(cleaned.split())


@mcp.resource("store://summary", mime_type="application/json")
def store_summary() -> str:
    """Headline store numbers: order counts by status, revenue, and stock alerts."""
    return json.dumps(repo.summary(), indent=2)


@mcp.prompt()
def weekly_store_review(focus: str = "overall performance") -> str:
    """Prepare a weekly store review using the store's live data.

    Args:
        focus: What the review should emphasize, for example "inventory" or "fulfilment".
    """
    return (
        f"You are reviewing the online store for the past week. Focus: {focus}.\n\n"
        "Steps:\n"
        "1. Read the store://summary resource for headline numbers.\n"
        "2. Call list_recent_orders with limit 10 and note anything pending or refunded.\n"
        "3. Call find_low_stock with threshold 10 and list products that need restocking.\n"
        "4. Write a short report with three sections: What went well, What needs attention, "
        "Recommended actions.\n\n"
        "Only use figures returned by the tools. If data is missing, say so instead of guessing."
    )


if __name__ == "__main__":
    mcp.run()  # stdio transport by default
