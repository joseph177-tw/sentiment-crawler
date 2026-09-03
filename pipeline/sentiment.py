# -*- coding: utf-8 -*-
"""
情緒分析與摘要層（架構文件第六節）
讀取 data/raw/<日期>/cleaned.json，批次打包丟給 Claude API 做結構化分類：
    {"sentiment": "positive|neutral|negative", "topic": "...",
     "mentions_company": true/false, "summary": "..."}
輸出：data/raw/<日期>/sentiment.json（cleaned 記錄 + 上述欄位）

需要環境變數 ANTHROPIC_API_KEY（GitHub Actions 中放在 repo secrets）。

用法：
    python pipeline/sentiment.py                # 正式模式，呼叫 Claude API
    python pipeline/sentiment.py --offline       # 離線模式，用關鍵字規則模擬分類（測試 pipeline 用，非正式分析品質）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common

log = common.setup_logging("pipeline.sentiment")

SYSTEM_PROMPT = """你是專門分析台灣財經社群輿情（PTT、Dcard、新聞、論壇）的助理。
這些文字來自台灣網路社群，可能包含反諷、鄉民用語、縮寫黑話，請根據上下文合理判斷語氣，
不要只看表面字詞。只根據貼文/新聞的文字內容判斷，不要臆測未提及的資訊。

對輸入的每一則貼文，輸出一個 JSON 物件，欄位如下：
- sentiment: "positive" | "neutral" | "negative"（整體語氣）
- topic: 簡短話題分類（例如：股價表現、服務評價、App使用體驗、總體經濟、產業動態）
- mentions_company: true/false（是否提及永豐金證券或其競爭對手／同業）
- summary: 15-40字繁體中文一句話摘要

請將所有結果依輸入順序組成一個 JSON 陣列，不要輸出任何陣列以外的文字或 markdown 標記。"""


def _build_user_prompt(batch: list[dict]) -> str:
    items = [
        {"index": i, "title": rec["title"], "content": rec["content"][:800]}
        for i, rec in enumerate(batch)
    ]
    return "請分析以下貼文（JSON 陣列，index 對應輸出順序）：\n" + json.dumps(
        items, ensure_ascii=False
    )


def _extract_json_array(text: str) -> list[dict]:
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\[.*\]", text, flags=re.S)
    if not match:
        raise ValueError(f"回應中找不到 JSON 陣列：{text[:200]}")
    return json.loads(match.group(0))


def classify_batch_online(client, model: str, batch: list[dict], max_retries: int) -> list[dict]:
    prompt = _build_user_prompt(batch)
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(block.text for block in resp.content if hasattr(block, "text"))
            results = _extract_json_array(text)
            if len(results) != len(batch):
                raise ValueError(f"回傳筆數 {len(results)} 與輸入 {len(batch)} 不符")
            return results
        except Exception as e:
            last_err = e
            log.warning("批次分類失敗（第 %d 次）：%s", attempt + 1, e)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"批次分類重試 {max_retries} 次仍失敗：{last_err}")


_POSITIVE_HINTS = ["推薦", "滿意", "不錯", "順", "方便", "看好", "利多", "上漲", "強勢"]
_NEGATIVE_HINTS = ["延遲", "問題", "客訴", "看壞", "利空", "下跌", "破產", "詐騙", "當機", "爛"]


def classify_batch_offline(batch: list[dict]) -> list[dict]:
    """離線關鍵字規則，僅供測試 pipeline 串接，不代表正式分析品質。"""
    keywords = common.load_keywords()
    company_kw = keywords.get("company", []) + keywords.get("competitors", [])
    results = []
    for rec in batch:
        text = f"{rec['title']} {rec['content']}"
        pos = sum(1 for kw in _POSITIVE_HINTS if kw in text)
        neg = sum(1 for kw in _NEGATIVE_HINTS if kw in text)
        if pos > neg:
            sentiment = "positive"
        elif neg > pos:
            sentiment = "negative"
        else:
            sentiment = "neutral"
        results.append({
            "sentiment": sentiment,
            "topic": "未分類（離線模式）",
            "mentions_company": any(kw in text for kw in company_kw),
            "summary": rec["title"][:40],
        })
    return results


def run(offline: bool = False, day: str | None = None) -> Path:
    day = day or common.today_str()
    settings = common.load_settings()
    sent_cfg = settings.get("sentiment", {})
    batch_size = sent_cfg.get("batch_size", 10)
    model = sent_cfg.get("model", "claude-haiku-4-5-20251001")
    max_retries = sent_cfg.get("max_retries", 2)

    cleaned_path = common.RAW_DIR / day / "cleaned.json"
    records = common.read_json(cleaned_path)
    if not records:
        log.warning("沒有待分析資料：%s", cleaned_path)
        out_path = common.RAW_DIR / day / "sentiment.json"
        common.write_json(out_path, [])
        return out_path

    client = None
    if not offline:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("未設定 ANTHROPIC_API_KEY 環境變數，無法呼叫 Claude API")
        client = anthropic.Anthropic(api_key=api_key)

    analyzed: list[dict] = []
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        log.info("分析批次 %d-%d / %d", i, i + len(batch), len(records))
        if offline:
            results = classify_batch_offline(batch)
        else:
            results = classify_batch_online(client, model, batch, max_retries)
        for rec, result in zip(batch, results):
            merged = dict(rec)
            merged["sentiment"] = result.get("sentiment", "neutral")
            merged["topic"] = result.get("topic", "")
            merged["mentions_company"] = bool(result.get("mentions_company", False))
            merged["summary"] = result.get("summary", "")
            analyzed.append(merged)

    out_path = common.RAW_DIR / day / "sentiment.json"
    common.write_json(out_path, analyzed)
    log.info("完成，共 %d 則，寫入 %s", len(analyzed), out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM 情緒分析與摘要")
    parser.add_argument("--offline", action="store_true", help="用關鍵字規則模擬分類，不呼叫 API（測試用）")
    parser.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD，預設今天")
    args = parser.parse_args()
    run(offline=args.offline, day=args.date)
