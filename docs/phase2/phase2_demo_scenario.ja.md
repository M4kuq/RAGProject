# Phase2 デモシナリオ

この手順は Phase2 完了後の引き継ぎデモ用です。
ローカル Docker Compose 環境で 5-10 分程度の説明を想定しています。
実ユーザーの prompt、顧客文書、secret、raw chunk、full context は画面・メモ・ログに出さないでください。

## 前提

- Docker Compose の backend / frontend / worker / postgres / qdrant が起動している。
- migration と seed が完了している。
- ローカル demo admin でログインできる。
- seeded documents と `phase2_strategy_smoke` dataset が存在する。
- fresh DB の場合は、Advanced Import の説明前に以下の deterministic fixture を Admin Documents から upload / approve し、ingest ready になるまで待つ。
  - `docs/phase2/demo_fixtures/phase2_strategy_overview.xlsx`
  - `docs/phase2/demo_fixtures/phase2_strategy_walkthrough.pptx`
  - `docs/phase2/demo_fixtures/phase2_source_page.html`
  - `docs/phase2/demo_fixtures/phase2_source_feed.xml`
- URL ingest は offline demo では任意。実施する場合は presenter が所有または確認済みの public HTML/XML URL だけを使う。localhost、private network、metadata IP、credential を含む URL、顧客 URL は使わない。
- LangSmith export と SentenceTransformers local experiment は明示的に opt-in しない限り無効のままにする。

## デモの流れ

1. **起動確認**
   - `docker compose config` が通ることを示す。
   - `http://localhost:5173` を開く。
   - `/health` と `/ready` が healthy であることを確認する。

2. **Admin ログイン**
   - ローカル demo admin でログインする。
   - 実 credential や `.env` の値は表示しない。

3. **Document ingest 状態**
   - Admin Documents を開く。
   - seeded documents と demo fixtures が ready であることを確認する。
   - spreadsheet、presentation、HTML、XML の source label、chunk count、metadata を確認する。
   - URL ingest も同じ safe metadata path を使うが、offline demo では必須ではないことを説明する。

4. **Dense / Sparse / Hybrid 比較**
   - Retrieval Debug を開く。
   - 同じ safe query を `dense`、`sparse`、`hybrid` で実行する。
   - score breakdown、retrieval source、latency、selected item count を比較する。

5. **Agentic Router**
   - `strategy=agentic_router` で検索する。
   - query plan、router decision、execution strategy、fallback state、sufficiency summary、retrieval call count、latency を確認する。
   - これは LLM が tool を選ぶモードではなく、rule-based router であることを明確に説明する。

6. **LLM Agentic RAG**
   - Chat 画面を開く。
   - RAG mode selector で **LLM Agentic RAG** を選択する。
   - safe synthetic comparison query を送る。
   - 根拠が十分なら citations / confidence 付き回答、根拠不足なら `no_context_found` になることを確認する。
   - Retrieval Debug で該当 `llm_tool_orchestrator` run を開き、tool call count、search call count、finalize flag、budget flag、latency summary を確認する。

7. **Retrieval Debug UI v2**
   - retrieval run detail を開く。
   - `query_plan_json`、`strategy_decision_json`、`retrieval_settings_json`、`score_breakdown_json`、`latency_breakdown_json` が safe summary として見えることを確認する。

8. **Strategy Evaluation**
   - Evaluations を開く。
   - 既存 run または小さい manual run で `dense,hybrid,agentic_router` を比較する。
   - recall、MRR、citation coverage、no-context rate、p95 latency、agentic metrics を確認する。

9. **Failure promotion**
   - evaluation run の failure candidates を表示する。
   - 小さく filter した failure を active dataset へ promote する。
   - 2 回目は skipped / already exists になる idempotency を説明する。

10. **CI Retrieval Evaluation**
    - `.github/workflows/retrieval-eval-smoke.yml` を開く。
    - `workflow_dispatch`、optional schedule、warn/fail mode、JSON/Markdown artifact、blocked artifact を説明する。

11. **Optional observability**
    - LangSmith optional adapter docs を開く。
    - default は no-op であり、外部 export には明示設定と repository 外の secret が必要であることを説明する。

12. **SentenceTransformers experiment**
    - dry-run 例を示す。
      `scripts/run_retrieval_model_experiment.ps1 -Mode dry-run -DownloadPolicy never`
    - local mode は opt-in で、default では model download しないことを説明する。

13. **Advanced Import**
    - `.xlsx` / `.pptx` の sheet / slide metadata を確認する。
    - `.html` / `.xml` の heading / XML path metadata を確認する。
    - URL ingest は safe public URL だけを対象にし、SSRF guard が localhost、private IP、metadata host、credential 付き URL を拒否することを説明する。
    - crawler、JavaScript rendering、OCR は対象外であることを説明する。

14. **Document Diff / Citation Navigation**
    - Document Detail > Version Compare で 2 version を比較する。
    - metadata diff、chunk diff count、bounded preview を確認する。
    - Chat の citation から View source を開き、安全な source locator / bounded preview を確認する。

15. **MCP Advanced RAG**
    - local stdio MCP の tools/resources/prompts を確認する。
    - `rag_search(strategy=hybrid)` または `rag_search_hybrid` を safe synthetic query で実行する。
    - `rag_search_agentic` / `rag_ask_agentic` の safe summary を確認する。
    - MCP から upload、archive、approve、retry、remote MCP、OAuth、raw prompt、full context、raw chunk は扱えないことを説明する。

## Safe demo query 例

| 種別 | 例 |
|---|---|
| Keyword-heavy | `RAGProject Qdrant sparse retrieval settings` |
| Semantic | `How does the system choose retrieval evidence?` |
| Comparison | `Compare dense and hybrid retrieval behavior.` |
| Version-specific | `What changed in the newer policy version?` |
| No context | `What is the weather on Mars today?` |
| Office metadata | `Which sheet or slide mentions retrieval strategy?` |
| HTML/XML source | `Which imported page or feed describes SSRF guard behavior?` |

## Presenter notes

- `.env` や環境変数の値を開かない。
- raw retrieval payload、raw chunk、full prompt、full context を表示しない。
- DB dump ではなく UI の bounded preview を使う。
- Phase3 の対象は Graph-RAG、OCR、multimodal UI、AWS/S3、OIDC、online evaluation であると説明する。
