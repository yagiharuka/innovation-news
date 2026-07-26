# World Innovation Brief

世界の技術・政策ニュースを、信頼できる情報源だけから毎朝収集し、重複を除いて公開する仕組みです。

## 見られるもの

- `docs/index.html` — ブラウザ版。地域・分野・情報源・政策関連度で絞り込み
- `docs/innovation_news_ledger.xlsx` — Excel台帳
- `data/news.csv` — 全件のマスターデータ
- `data/run_log.json` — 日次収集の実行履歴
- `data/source_status.json` — 情報源ごとの取得成否

## 収集方針

`config/sources.json` を唯一の情報源リストとして使います。対象は政府・国際機関、企業公式発表、主要報道機関、著名な政策研究機関、主要学術誌・業界団体です。匿名ブログ、転載サイト、コンテンツファームは自動探索しません。

対象分野は次の8分類です。

- AI
- ロボティクス
- 半導体・通信
- 量子
- 核融合
- バイオテクノロジー
- ヘルスケア
- イノベーション政策

対象地域は米国、アジア、EU・欧州、中東、およびグローバルです。URLの正規化、追跡パラメータの除去、タイトル指紋の照合で重複を除きます。

## 自動更新

GitHub Actions が毎日 06:00（日本時間）に `scripts/collect.py` を実行します。初回は過去14日、2回目以降は直近96時間を確認します。個別のRSS取得に失敗しても、残りの情報源の収集は継続します。

手動実行は GitHub の **Actions → Daily innovation brief → Run workflow** から行えます。

## GitHub Pages を有効にする

リポジトリの **Settings → Pages** で、Source を **Deploy from a branch**、Branch を **main / docs** に設定すると、ブラウザ版を公開できます。

## ローカル実行

```bash
python -m pip install -r requirements.txt
python scripts/collect.py
```

APIキーは不要です。
