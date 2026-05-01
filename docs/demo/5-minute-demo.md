# 5分デモ手順

1. `docker compose up --build` でローカル検証環境を起動する。
2. `admin@example.com / password` でログインする。
3. `data/demo/sample.md` を文書アップロードする。
4. Worker が ingest job を処理し、文書 version が `ready` になることを確認する。
5. Chat で「RAGProject は何を示すためのものですか？」と質問する。
6. 回答、citation、confidence を確認する。
7. Admin 画面で evaluation を手動実行し、結果と audit log を確認する。

CI/CD の説明では、GitHub Actions が lint、type check、test、Docker build、compose smoke を実行する構成であることを示す。
