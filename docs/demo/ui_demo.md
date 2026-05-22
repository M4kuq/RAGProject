# UI Demo

## 起動

```bash
docker compose up --build
```

UI は `http://localhost:5173` を開く。API は `http://localhost:8000` を使う。

## Login

- admin: `admin@example.com` / `password`
- viewer: `viewer@example.com` / `password`

上記はローカルデモ用の dummy credential である。実運用値ではない。

## Chat UI

1. Chat を開く。
2. `What vector database is used by Phase1?` を送る。
3. assistant message が表示されることを確認する。
4. citation panel を開き、source label と snippet preview を確認する。
5. confidence badge を確認する。
6. `What is the office Wi-Fi password?` のように seed 文書にない質問を送り、no-context 系の扱いを確認する。

## Admin Document UI

1. admin で login する。
2. Admin Documents を開く。
3. seed 文書、old/new version pair、active version を確認する。
4. 小さな Markdown file を upload する。
5. Job 一覧で queued / running / succeeded / failed の表示を確認する。
6. version detail と chunks を確認する。
7. approve を実行し、一覧が更新されることを確認する。

## Admin Job UI

1. Admin Jobs を開く。
2. job status、target、retry action の表示を確認する。
3. failed job がある場合だけ retry を確認する。
4. raw payload や内部 context が表示されないことを確認する。

## Evaluation UI

1. Admin Evaluation を開く。
2. dataset `phase1_smoke` で run を作る。
3. run detail で case、status、metric summary を見る。
4. prompt 全文や context 全文が表示されないことを確認する。

## Viewer 確認

1. viewer で login する。
2. Chat は使えることを確認する。
3. Admin Documents / Jobs / Evaluation へ直接移動し、Forbidden または login guard になることを確認する。
