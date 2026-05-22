# Demo Data

## Accounts

| Role | Email | Purpose |
|---|---|---|
| admin | `admin@example.com` | document、job、evaluation、RAG search の管理導線を確認する。 |
| viewer | `viewer@example.com` | chat と RBAC 境界を確認する。 |

password はローカルデモ用の dummy credential を使う。実運用値ではない。

## Seed Documents

| Title | File type | Purpose |
|---|---|---|
| RAGProject Phase1 Seed Document | Markdown | stack、Qdrant、fake adapter、citation、MCP の基本質問に使う。 |
| Phase1 Design Memo | Markdown old/new pair | old version と active version の表示、citation、confidence を確認する。 |
| Phase1 Operations Policy Memo | TXT | admin / viewer の権限差と運用導線を確認する。 |
| Phase1 Metrics Sample CSV | CSV | evaluation fixture と MCP transport の質問に使う。 |

seed 本文は公開可能な短い demo content のみで構成する。PII、credential、private document は含めない。

## Evaluation Fixture

- path: `backend/app/evaluation/fixtures/phase1_smoke.json`
- dataset: `phase1_smoke`
- cases: Qdrant、fake adapters、citation-aware retrieval traces、confidence labels、stdio MCP

## Upload Fixture

大きな PDF / DOCX は repository に入れない。UI demo では小さな Markdown / TXT / CSV を手元で作り upload する。

```markdown
# Phase1 upload demo
This document confirms upload, approval, ingest, citation, and admin review flows.
```
