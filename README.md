# 社群輿情爬蟲與情緒分析系統

依 `社群輿情爬蟲系統_架構文件.md` 實作，個人研究用途：每日／每週自動蒐集財經相關
社群輿情（PTT、Dcard、新聞、Mobile01），進行 LLM 情緒分析並產出 HTML 報告、推播 Telegram。

**固定報告網址（GitHub Pages）**：https://joseph177-tw.github.io/sentiment-crawler/

四個頁面（上方導覽列可切換），每次排程自動覆蓋更新，網址本身不會變：
- `/`（`index.html`）：每日報告
- `/weekly.html`：週報
- `/keywords.html`：關鍵字總覽——累積掃描所有貼文，依出現次數列出每個關鍵字的
  總互動數（推+噓加總）、來源分布（平台·板）、相關貼文列表；日報關鍵字雲的字都
  可以點過去對應段落
- `/market.html`：盤勢總覽——自動從累積貼文中偵測提到的 TWSE 上市個股（比對貼文
  文字與 TWSE 證券名稱），依提及次數列出前 15 檔，各自畫出近半年日K線圖（含成交量）
  ＋相關貼文列表

> ⚠️ 因為 GitHub 免費方案不支援 private repo 的 Pages 功能，這個 repo 目前是 **public**
> （原本是 private，是為了要有固定網址才改的）。代表 `config/keywords.yaml` 裡列的公司
> 名稱、競爭對手清單，以及爬蟲邏輯本身、每日報告內容，只要知道 repo 網址任何人都看得到。
> 如果之後這點變成問題，可以考慮升級 GitHub Pro（private repo 也能用 Pages）或改回
> Telegram 附檔的方式，把 repo 設回 private。

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
| 8 | `notify/telegram_bot.py` | 完成，優先推播 GitHub Pages 固定連結，沒設定 `pages_base_url` 時退回附檔 |
| 9 | GitHub Actions（`daily_crawl.yml` / `weekly_report.yml`） | **已上線**，cron 已跑過至少一次真實排程成功（2026-09-04 07:00 台灣時間自動觸發） |
| 10 | `pipeline/aggregate.py`（週報趨勢） | 完成，但架構文件建議「觀察一週真實資料品質後再開發」—— 目前邏輯已可跑，週報趨勢的判讀價值要等累積至少 7 天真實資料後才有意義 |

**已知問題（尚未修）**：`crawlers/dcard_crawler.py`、`crawlers/forum_crawler.py` 在 GitHub Actions
上實際跑都是 0 筆——Dcard 非官方 API 可能擋 GitHub runner 的 IP，Mobile01 的 CSS selector
沒對過真實頁面、可能跟目前排版對不上，需要之後另外 debug。

**後續加的功能**（架構文件原本沒有，依需求擴充）：
- `keyword_lib.py`：共用斷詞邏輯，`report/render.py`（單日關鍵字雲）與 `pipeline/aggregate.py`
  （累積關鍵字索引）共用同一套規則
- `pipeline/aggregate.py --keywords`：從 SQLite 累積貼文建立關鍵字索引
  （`data/raw/keyword_index.json`）
- `pipeline/stock_detect.py`：抓 TWSE 上市股票清單、比對貼文偵測提及個股、
  抓 Yahoo Finance 歷史價格（`data/raw/market_data.json`）
- `report/render.py --keywords` / `--market`：產出 `docs/keywords.html`、`docs/market.html`
  （K 線圖用 ECharts，CDN 載入，不需要額外安裝前端依賴）

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
python pipeline/aggregate.py --keywords
python pipeline/stock_detect.py --offline
python report/render.py
python report/render.py --keywords
python report/render.py --market
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
python pipeline/aggregate.py --keywords
python pipeline/stock_detect.py
python report/render.py
python report/render.py --keywords
python report/render.py --market
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

`.github/workflows/daily_crawl.yml`（每日 07:00 台灣時間）、`weekly_report.yml`
（每週日 07:00 台灣時間）已上線並跑過至少一次真實排程。

- Secrets（`Settings > Secrets and variables > Actions`）：`ANTHROPIC_API_KEY`、
  `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` 已設定
- Actions workflow 權限已設為 read-and-write（`git push` 才能把資料 commit 回 repo；
  `data/db.sqlite` 需要被 commit，因為 GitHub Actions runner 每次都是全新環境，
  歷史趨勢比對要靠這個檔案在 repo 裡持續累積）
- GitHub Pages 已指向 `master` 分支的 `/docs` 目錄
- 觀察至少一週的真實資料品質後，再評估是否要調整 `pipeline/aggregate.py` 的週報趨勢邏輯

新增/修改 `.github/workflows/*.yml` 後想手動測試，可用：
```bash
gh workflow run daily_crawl.yml --repo joseph177-tw/sentiment-crawler
gh run watch --repo joseph177-tw/sentiment-crawler
```
> 提醒：workflow 檔案第一次 push 時 GitHub 有時不會立刻註冊，需要一次「有實際改到
> `.github/workflows/*.yml` 內容」的 push 才會觸發重新掃描（純 `git commit --allow-empty`
> 不會觸發）。`gh workflow list --all` 查得到才代表註冊成功。

## 使用限制提醒

- 本專案僅供個人研究/資訊蒐集用途，非公司正式輿情監測系統
- Dcard 爬蟲屬非官方 API 用法，穩定性可能隨平台調整而變動
- 請控制爬取頻率（已內建請求間隔），避免對來源網站造成負擔或被封鎖
- 若後續需將輸出結果用於任何對外發布或公司決策用途，仍建議先與內部法遵確認
- `pipeline/aggregate.py` 的「話題缺口」判定是「高互動 + 負面情緒 + 提及公司」的近似
  指標，並非精確判定公司是否已正式回應，僅供人工複核參考
