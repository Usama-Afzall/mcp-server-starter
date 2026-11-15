# MCP Server Starter

A small, read-only Model Context Protocol (MCP) server in Python that lets an AI assistant such as Claude query an online store's orders and products. It is a clean starting point for connecting an AI assistant to a real business system.

Built by Usama Afzall, [Website Prism](https://websiteprism.com). Portfolio: [usama.websiteprism.com](https://usama.websiteprism.com).

## What is MCP?

The Model Context Protocol is an open standard that lets AI applications connect to external tools and data sources through one consistent interface. An MCP server describes what it can do (tools, resources, prompts), and an MCP client such as Claude Desktop discovers and uses those capabilities.

## What this server exposes

| Type | Name | Purpose |
|---|---|---|
| Tool | `get_order(order_id)` | Full details of one order, with product titles. Accepts `ORD-1003` or `1003`. |
| Tool | `list_recent_orders(limit, status)` | Newest orders first. `limit` 1 to 50 (default 5). Optional `status`: pending, paid, fulfilled, cancelled, refunded. |
| Tool | `find_low_stock(threshold)` | Products at or below a stock level (default 10), lowest first. |
| Tool | `search_products(query)` | Case-insensitive keyword search over title, SKU, description, category and tags. All words must match. |
| Resource | `store://summary` | JSON snapshot: order counts by status, revenue, out-of-stock SKUs, low-stock count. |
| Prompt | `weekly_store_review(focus)` | A ready-made instruction that tells the assistant how to produce a weekly review using the tools above. |

The data is fictional and lives in `sample_data.json` (12 orders, 10 products).

## Install and run

Requires Python 3.10 or newer.

```bash
git clone https://github.com/Usama-Afzall/mcp-server-starter.git
cd mcp-server-starter
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python test_server.py            # end-to-end test over stdio
python server.py                 # starts the server on stdio
```

`server.py` waits for an MCP client on stdin, so running it directly will appear to do nothing. That is expected. To use a different data file, set the `STORE_DATA_FILE` environment variable.

## Claude Desktop configuration

Add this to your `claude_desktop_config.json`, replacing the paths with absolute paths on your machine (use the Python from your virtual environment so the `mcp` package is found), then restart Claude Desktop:

```json
{
  "mcpServers": {
    "store-assistant": {
      "command": "/ABSOLUTE/PATH/TO/mcp-server-starter/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/mcp-server-starter/server.py"]
    }
  }
}
```

Then try: "Which products are low on stock?", "Show me the three most recent paid orders", or use the `weekly_store_review` prompt.

## Test with the MCP Inspector

```bash
npx @modelcontextprotocol/inspector python server.py
```

This opens a local web UI where you can list and call the tools, read the resource and render the prompt. It requires Node.js and was not part of the automated test in this repository.

## Extend to real Shopify data

The code is split on purpose. `StoreRepository` in `server.py` is the data access layer and contains no MCP code. The decorated functions below it are the MCP layer.

1. Create a Shopify custom app and an Admin API access token with read-only scopes only (for example `read_orders`, `read_products`, `read_inventory`).
2. Store the shop domain and token in environment variables, never in source control.
3. Rewrite the `StoreRepository` methods to call the Shopify Admin GraphQL API and map the responses into the same dictionary shapes the sample data uses. The docstring on the class lists the starting points.
4. Keep the input validation in the MCP layer. Run `test_server.py` against a development store or recorded fixtures.

Check the current Shopify Admin API documentation for exact field names and API versions. This repository does not call Shopify.

## Security notes

- Read-only by design. No tool creates, edits or deletes anything.
- All inputs are validated with clear error messages: order ids are pattern-checked, limits and thresholds are range-checked, statuses are checked against an allowed list, and search text has a length cap.
- Use least-privilege API tokens. Grant only the read scopes the tools need, and rotate tokens.
- Never expose write tools (refunds, cancellations, price changes) without an explicit human confirmation step.
- Treat tool output as sensitive. Real orders contain customer names and emails, so decide what to return and consider redacting personal data.

## Limitations

- Sample data only. Nothing here is connected to a live store.
- Data is loaded once at startup, so edits to the JSON file need a server restart.
- No pagination, authentication, rate limiting or caching. Results are capped by `limit` only on order listings.
- Search is simple substring matching, not ranked or fuzzy.
- Revenue in the summary is a simple sum of paid and fulfilled order totals in USD. It ignores tax, shipping, discounts and refund partials.
- Runs over stdio for local use. A hosted, multi-user deployment would need a network transport and proper authentication.
- Tested with `mcp` 1.30.0 and 2.3.0 on Python 3.13. The `mcp` SDK changes between major versions, so pin a version for production.

## License

Source-available, not open source. You may read and evaluate this code, but commercial or production use requires a written licence from Usama Afzall. See `LICENSE` and https://usama.websiteprism.com/contact.

Need this customized? https://usama.websiteprism.com/contact
