# Troubleshooting

## Docker

Symptom: `docker compose config` が失敗する。
Cause: Docker Desktop / Docker Engine が起動していない、または compose plugin が使えない。
Fix: Docker を起動し、repository root で再実行する。
Command: `docker compose config`

## Postgres

Symptom: `/ready` が database error を返す。
Cause: postgres service が healthy になる前に backend を確認している。
Fix: service health を確認し、migrate を再実行する。
Command: `docker compose ps postgres && docker compose run --rm migrate`

## Qdrant

Symptom: RAG search が vector store error になる。
Cause: qdrant service が未起動、または collection 初期化前である。
Fix: qdrant health と backend env を確認する。
Command: `docker compose exec -T backend python -m app.scripts.healthcheck http://qdrant:6333/healthz`

## Frontend

Symptom: `http://localhost:5173` が開かない。
Cause: frontend service が未起動、または port が使用中である。
Fix: compose service と logs を確認する。
Command: `docker compose ps frontend && docker compose logs frontend`

## Backend

Symptom: `/health` は通るが `/ready` が通らない。
Cause: process は起動済みだが DB readiness が満たされていない。
Fix: postgres、migrate、seed の順に確認する。
Command: `docker compose ps && docker compose logs backend`

## Worker

Symptom: job が queued のまま残る。
Cause: worker が起動していない、または enabled job type が限定されている。
Fix: worker logs と env profile を確認する。
Command: `docker compose logs worker`

## Migration

Symptom: `alembic upgrade head` が失敗する。
Cause: DB 接続、migration 履歴、compose env の不整合がある。
Fix: local demo DB であることを確認してから migrate service を再実行する。
Command: `docker compose run --rm migrate`

## Seed

Symptom: seed が失敗する。
Cause: `APP_ENV` が local / ci / test 以外、または schema と seed がずれている。
Fix: compose env と migration 完了を確認する。
Command: `docker compose run --rm seed`

## Upload

Symptom: upload が 4xx になる。
Cause: CSRF header、admin role、file extension、size limit のいずれかに合わない。
Fix: admin login、CSRF、allowlist、file size を確認する。
Command: `docker compose logs backend`

## RAG Ask

Symptom: ask が no-context になる。
Cause: active ready document がない、Qdrant index が空、質問が demo document と合っていない。
Fix: seed document と sample questions を使って確認する。
Command: `cat docs/demo/sample_questions.md`

## Citation

Symptom: citation panel が空になる。
Cause: selected chunk がない、または answer generation が citation marker を返していない。
Fix: RAG search の selected result と fake generation profile を確認する。
Command: `docker compose logs backend`

## Evaluation

Symptom: evaluation run が queued のままになる。
Cause: worker が evaluation job を処理していない。
Fix: worker profile と job logs を確認する。手動テストでは queued 作成と detail 表示を分けて扱う。
Command: `docker compose logs worker`

## MCP

Symptom: MCP client から tools が見えない。
Cause: cwd、Python path、stdio command、`MCP_ENABLED` の設定が合っていない。
Fix: backend directory または backend container で version と tools/list を先に確認する。
Command: `python -m app.mcp.server --version`

## Windows

Symptom: shell script が動かない。
Cause: PowerShell と sh の違い、Docker Desktop の Linux containers 未起動である。
Fix: PowerShell script を使い、Docker Desktop の engine を確認する。
Command: `.\scripts\smoke_phase1.ps1`

## Ubuntu

Symptom: docker command に permission error が出る。
Cause: Docker group または sudo 設定がない。
Fix: Docker の実行権限を設定し、shell を開き直す。
Command: `docker compose version`

## CI

Symptom: CI で model download が必要になって失敗する。
Cause: fake adapter profile から外れている。
Fix: CI compose の fake env を確認する。
Command: `docker compose -f docker-compose.ci.yml config`
