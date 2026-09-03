# 社群輿情爬蟲與情緒分析系統

依 `社群輿情爬蟲系統_架構文件.md` 實作，個人研究用途：每日／每週自動蒐集財經相關
社群輿情（PTT、Dcard、新聞、Mobile01），進行 LLM 情緒分析並產出 HTML 報告、推播 Telegram。

## 目前實作狀態

架構文件第十二節建議的 10 個步驟已全部建好並以離線 fixture 資料驗證過整條 pipeline：

| # | 項目 | 狀態 |
|---|------|------|
| 1 | 專案骨架 + requirements.txt | 完成 |
| 2 | `crawlers/news_crawler.py` | 完成（RSS，支援 `--offline`） |
| 3 | `crawlers/ptt_crawler.py` | 完成（支援 `--offline`） |
| 4 | `pipeline/clean.py` | 完成 |
| 5 | `pipeline/sentiment.py` | 完成（需 `ANTHROPIC_API_KEY`；`--offline` 用關鍵字規則模擬，僅測試串接用） |
| 6 | `report/render.py` | 完成（日報，含摘要卡片／熱門話題／關鍵字雲） |
| 7 | `crawlers/dcard_crawler.py`、`crawlers/forum_crawler.py` | 完成（皆支援 `--offline`） |
| 8 | `notify/telegram_bot.py` | 完成（支援 `--dry-run`） |
| 9 | GitHub Actions（`daily_crawl.yml` / `weekly_report.yml`） | 完成，尚未啟用 cron（見下方待辦） |
| 10 | `pipeline/aggregate.py`（週報趨勢） | 完成，但架構文件建議「觀察一週真實資料品質後再開發」—— 目前邏輯已可跑，週報趨勢的判讀價值要等累積至少 7 天真實資料後才有意義 |

## 安裝

```bash
cd sentiment-crawler
python -m venv .venv
source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 本機測試（不打真實網路/API，驗證管線是否串得起來）

```bash
python crawlers/news_crawler.py --offline
python crawlers/ptt_crawler.py --offline
python crawlers/dcard_crawler.py --offline
python crawlers/forum_crawler.py --offline
python pipeline/clean.py
python pipeline/sentiment.py --offline    # 關鍵字規則模擬分類，非正式分析品質
python pipeline/aggregate.py
python report/render.py
python notify/telegram_bot.py --dry-run
```

`--offline` 模式讀取 `fixtures/` 下的測試資料（`news_items.json`、`ptt_pages/*.html`、
`dcard_posts.json`、`mobile01_pages/*.html`），可用來驗證程式邏輯正確、不需要連外網或 API key。

## 正式執行（需要環境變數）

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...

python crawlers/news_crawler.py
python crawlers/ptt_crawler.py
python crawlers/dcard_crawler.py
python crawlers/forum_crawler.py
python pipeline/clean.py
python pipeline/sentiment.py
python pipeline/aggregate.py
python report/render.py
python notify/telegram_bot.py
```

週報：

```bash
python pipeline/aggregate.py --weekly
python report/render.py --weekly
python notify/telegram_bot.py --weekly
```

## 設定檔

- `config/keywords.yaml`：公司/競品/產業關鍵字，以及過濾用的噪音關鍵字
- `config/settings.yaml`：各爬蟲的看板/來源清單、頁數上限、請求間隔、清理與分析參數

## GitHub Actions 排程

`.github/workflows/daily_crawl.yml`、`weekly_report.yml` 已建好，但**排程建議先手動測試**：

1. 到 repo 的 `Settings > Secrets and variables > Actions` 設定：
   - `ANTHROPIC_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
2. 先用 `workflow_dispatch` 手動觸發 `daily_crawl.yml`，確認整條 pipeline 在 CI 環境跑得動、
   資料正確 commit 回 repo（`data/db.sqlite` 需要被 commit，因為 GitHub Actions runner
   每次都是全新環境，歷史趨勢比對要靠這個檔案在 repo 裡持續累積）
3. 確認無誤後，cron 排程即會自動生效（`daily_crawl.yml` 每日 07:00、`weekly_report.yml`
   每週日 07:00，皆為台灣時間）
4. 觀察至少一週的真實資料品質後，再評估是否要調整 `pipeline/aggregate.py` 的週報趨勢邏輯

## 使用限制提醒

- 本專案僅供個人研究/資訊蒐集用途，非公司正式輿情監測系統
- Dcard 爬蟲屬非官方 API 用法，穩定性可能隨平台調整而變動
- 請控制爬取頻率（已內建請求間隔），避免對來源網站造成負擔或被封鎖
- 若後續需將輸出結果用於任何對外發布或公司決策用途，仍建議先與內部法遵確認
- `pipeline/aggregate.py` 的「話題缺口」判定是「高互動 + 負面情緒 + 提及公司」的近似
  指標，並非精確判定公司是否已正式回應，僅供人工複核參考
