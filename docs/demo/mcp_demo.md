# MCP Demo

## Scope

Phase1 MCP は local-only / stdio 前提である。remote MCP、OAuth、sampling、elicitation、write tools は提供しない。実機 client は環境差があるため、まず JSON-RPC stdio で server 動作を確認する。

## Server Startup

```bash
cd backend
python -m app.mcp.server --version
python -m app.mcp.server --transport stdio
```

Docker Compose で backend container 内から確認する場合:

```bash
docker compose exec -T backend python -m app.mcp.server --version
```

## tools/list

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | docker compose exec -T backend python -m app.mcp.server
```

## rag_search

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"rag_search","arguments":{"query":"What vector database is used by Phase1?","top_k":5,"rerank_top_n":2}}}' \
  | docker compose exec -T backend python -m app.mcp.server
```

## rag_ask

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"rag_ask","arguments":{"question":"How does Phase1 keep CI deterministic?","top_k":5,"rerank_top_n":2}}}' \
  | docker compose exec -T backend python -m app.mcp.server
```

## Document / Job / Evaluation Tools

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"list_documents","arguments":{"page_size":5}}}' \
  | docker compose exec -T backend python -m app.mcp.server

printf '%s\n' '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"get_document_status","arguments":{"logical_document_id":1}}}' \
  | docker compose exec -T backend python -m app.mcp.server

printf '%s\n' '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"get_job_status","arguments":{"job_id":1}}}' \
  | docker compose exec -T backend python -m app.mcp.server

printf '%s\n' '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"list_evaluation_runs","arguments":{"page_size":5}}}' \
  | docker compose exec -T backend python -m app.mcp.server

printf '%s\n' '{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"get_evaluation_result","arguments":{"evaluation_run_id":1}}}' \
  | docker compose exec -T backend python -m app.mcp.server
```

`get_document_status`、`get_job_status`、`get_evaluation_result` は対象 ID が存在しない場合に not found 系の error を返す。先に list 系 tool で ID を確認する。

## Client Config Example

Claude Desktop / Cursor / Codex 等では command と working directory だけを設定する。機密値は設定しない。

```json
{
  "mcpServers": {
    "ragproject": {
      "command": "python",
      "args": ["-m", "app.mcp.server", "--transport", "stdio"],
      "cwd": "C:/Users/kei01/RAGProject/backend"
    }
  }
}
```

実機 client 接続は client 側の MCP 実装差がある。Phase1 の受け入れでは stdio JSON-RPC smoke を基準にする。
