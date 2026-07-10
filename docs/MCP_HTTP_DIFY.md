# MCP HTTP Transport for Local Dify

## Scope

RAGProject exposes a minimal MCP Streamable HTTP transport for local clients:

- `POST /mcp` accepts a single JSON-RPC message and returns `application/json`.
- JSON-RPC notifications such as `notifications/initialized` return `202 Accepted` with no body.
- `GET /mcp` returns `405 Method Not Allowed`; SSE streaming is not implemented.
- `MCP-Protocol-Version: 2025-06-18` is accepted. Unknown protocol versions are rejected.
- `Authorization: Bearer <key>` is required.
- Browser `Origin` headers are allowed only for localhost or loopback origins.

This transport reuses the existing read-mostly MCP tools, resources, prompts, and redaction logic. It does not enable write tools.

## Enable HTTP Transport

Set the local environment values before starting the backend:

```bash
MCP_TRANSPORT=http
MCP_HTTP_API_KEY=<set-a-long-random-local-key>
```

With Docker Compose, these values are read from `.env` and passed to the backend service. The backend port remains bound to localhost:

```text
127.0.0.1:8000:8000
```

Start or recreate the backend after changing the values:

```bash
docker compose up -d backend worker
```

## Curl Smoke Test

```bash
curl -X POST http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2025-06-18" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

A successful response contains a JSON-RPC `result.tools` list with tools such as `rag_search`.

## Dify v1.6.0+ Connection

In local Dify, add an MCP server/tool entry with:

```text
URL: http://host.docker.internal:8000/mcp
Header: Authorization: Bearer <key>
Header: Accept: application/json, text/event-stream
Header: MCP-Protocol-Version: 2025-06-18
```

Use the same key as `MCP_HTTP_API_KEY`. Do not paste `.env` contents into prompts, datasets, or workflow nodes.

## Docker Network Fallback

If Dify cannot reach `host.docker.internal`, attach the Dify container to the RAGProject Compose network and use the backend service name:

```text
URL: http://backend:8000/mcp
```

The exact Docker command depends on how Dify was started. Confirm the RAGProject network name with:

```bash
docker network ls
```

Then connect the Dify container to that network and retry the same MCP URL with the `backend` hostname.

## Security Notes

- Keep the backend port bound to `127.0.0.1:8000`; do not expose it on LAN or the public internet.
- `MCP_HTTP_API_KEY` is required when `MCP_TRANSPORT=http`.
- Do not commit real API keys or `.env` values.
- The HTTP transport is stateless. `Mcp-Session-Id`, OAuth, token issuance, and SSE streaming are intentionally not implemented.
- MCP outputs continue to use the existing redaction layer for raw chunks, full context, prompts, storage paths, tokens, and credentials.
