# -*- coding: utf-8 -*-
"""
報告產出層（架構文件第八節）
讀取 data/raw/<日期>/sentiment.json，產出單一自含 HTML 報告：
  1. 摘要卡片：本日總聲量、正負面比例
  2. 情緒分佈（正/中/負則數）
  3. 熱門話題 Top N（依聲量排序，含連結、摘要、情緒標籤）
  4. 關鍵字雲（依標題+摘要斷詞出現次數，簡易版）
與既有台股儀表板（05_industry_tracker/report.py）採同一套視覺風格（配色、字體、卡片式排版）。

用法：
    python report/render.py                   # 產出今天的報告
    python report/render.py --date 2026-09-01  # 指定日期
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import jieba

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common

log = common.setup_logging("report.render")
jieba.setLogLevel(20)  # 抑制 jieba 初始化時的 INFO log


def _load_finance_dict() -> None:
    """把 keywords.yaml 的公司/產業關鍵字餵給 jieba，避免「永豐金證券」「台積電」
    這類專有名詞被預設字典拆散（例如拆成「永豐」+「金證券」）。"""
    keywords = common.load_keywords()
    terms = (
        keywords.get("company", [])
        + keywords.get("competitors", [])
        + keywords.get("industry", [])
    )
    for term in terms:
        jieba.add_word(term, freq=100000)


_load_finance_dict()

SENTIMENT_LABEL = {"positive": "正面", "neutral": "中性", "negative": "負面"}
SENTIMENT_COLOR = {"positive": "#c62828", "neutral": "#9e9e9e", "negative": "#2e7d32"}

_STOPWORDS = {
    "的", "是", "在", "了", "與", "及", "和", "也", "就", "都", "而", "或", "被",
    "這", "那", "有", "為", "對", "中", "上", "下", "不", "台股", "新聞",
    "多數", "用戶", "調查", "心得", "分享",
}
_VALID_WORD_RE = re.compile(r"^[一-鿿A-Za-z]{2,}$")


def _keyword_cloud(records: list[dict], top_n: int = 20) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for rec in records:
        text = f"{rec.get('title', '')} {rec.get('summary', '')}"
        for word in jieba.cut_for_search(text):
            word = word.strip()
            if word in _STOPWORDS or not _VALID_WORD_RE.match(word):
                continue
            counter[word] += 1
    return counter.most_common(top_n)


def build_stats(records: list[dict]) -> dict:
    total = len(records)
    counts = Counter(r.get("sentiment", "neutral") for r in records)
    pos, neu, neg = counts.get("positive", 0), counts.get("neutral", 0), counts.get("negative", 0)
    pct = lambda n: round(n / total * 100, 1) if total else 0.0
    return {
        "total": total,
        "positive": pos,
        "neutral": neu,
        "negative": neg,
        "positive_pct": pct(pos),
        "neutral_pct": pct(neu),
        "negative_pct": pct(neg),
    }


def _engagement_score(rec: dict) -> int:
    eng = rec.get("engagement", {}) or {}
    return eng.get("push", 0) - eng.get("boo", 0)


def top_topics(records: list[dict], n: int) -> list[dict]:
    ranked = sorted(records, key=_engagement_score, reverse=True)
    return ranked[:n]


def _topic_row(rec: dict) -> str:
    sentiment = rec.get("sentiment", "neutral")
    label = SENTIMENT_LABEL.get(sentiment, sentiment)
    color = SENTIMENT_COLOR.get(sentiment, "#9e9e9e")
    eng = rec.get("engagement", {}) or {}
    eng_text = f'推{eng.get("push", 0)} / 噓{eng.get("boo", 0)}' if (eng.get("push") or eng.get("boo")) else "—"
    title_html = html.escape(rec.get("title", ""))
    url = html.escape(rec.get("url", "") or "#")
    summary = html.escape(rec.get("summary", ""))
    return (
        f'<tr><td><a href="{url}" target="_blank">{title_html}</a>'
        f'<div class="role">{html.escape(rec.get("platform", ""))} · {html.escape(rec.get("board", ""))}</div></td>'
        f'<td class="muted">{summary}</td>'
        f'<td class="num">{eng_text}</td>'
        f'<td><span class="badge" style="background:{color}">{label}</span></td></tr>'
    )


def render_html(records: list[dict], day: str, top_n: int = 10) -> str:
    stats = build_stats(records)
    topics = top_topics(records, top_n)
    cloud = _keyword_cloud(records)

    topic_rows = "".join(_topic_row(r) for r in topics) or '<tr><td colspan="4" class="muted">今日無資料</td></tr>'
    cloud_html = "".join(
        f'<span class="tag" style="font-size:{11 + min(count, 8) * 2}px">{html.escape(w)}</span>'
        for w, count in cloud
    ) or '<span class="muted">無足夠資料產生關鍵字雲</span>'

    generated_at = datetime.now(common.get_timezone()).strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>社群輿情日報 · {day}</title>
<style>
:root {{ --ink:#1a2332; --sub:#5a6a7e; --line:#dfe5ec; --bg:#f5f7fa; --card:#ffffff;
        --up:#c62828; --down:#2e7d32; --accent:#12406b; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
       font-family:"Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif; font-size:14px; }}
.wrap {{ max-width:1180px; margin:0 auto; padding:28px 20px 60px; }}
header {{ border-left:6px solid var(--accent); padding:4px 0 4px 16px; margin-bottom:24px; }}
header h1 {{ margin:0; font-size:22px; letter-spacing:1px; }}
header .sub {{ color:var(--sub); margin-top:4px; }}
section {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:18px 20px; margin-bottom:18px; }}
h2 {{ font-size:15px; margin:0 0 12px; color:var(--accent); letter-spacing:.5px; }}
.summary-cards {{ display:flex; gap:16px; flex-wrap:wrap; }}
.card {{ flex:1; min-width:140px; border:1px solid var(--line); border-radius:8px; padding:14px 16px; }}
.card .n {{ font-size:26px; font-weight:700; }}
.card .l {{ font-size:12px; color:var(--sub); margin-top:4px; }}
table {{ width:100%; border-collapse:collapse; }}
th {{ text-align:left; font-size:12px; color:var(--sub); font-weight:500;
     border-bottom:2px solid var(--line); padding:6px 8px; white-space:nowrap; }}
td {{ padding:9px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
tr:last-child td {{ border-bottom:none; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }} .muted {{ color:var(--sub); }}
.role {{ font-size:11px; color:var(--sub); margin-top:2px; }}
.badge {{ color:#fff; font-size:11px; padding:2px 10px; border-radius:20px; white-space:nowrap; }}
a {{ color:var(--accent); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
.tag {{ display:inline-block; margin:3px 6px 3px 0; padding:2px 8px; border-radius:12px;
       background:#eef2f7; color:var(--accent); }}
</style></head><body><div class="wrap">
<header>
  <h1>社群輿情日報</h1>
  <div class="sub">資料日期 {day}｜產生時間 {generated_at}（台北）｜來源：PTT / Dcard / 新聞 / 論壇</div>
</header>

<section><h2>摘要</h2>
<div class="summary-cards">
  <div class="card"><div class="n">{stats['total']}</div><div class="l">今日聲量（則）</div></div>
  <div class="card"><div class="n up">{stats['positive']} ({stats['positive_pct']}%)</div><div class="l">正面</div></div>
  <div class="card"><div class="n muted">{stats['neutral']} ({stats['neutral_pct']}%)</div><div class="l">中性</div></div>
  <div class="card"><div class="n down">{stats['negative']} ({stats['negative_pct']}%)</div><div class="l">負面</div></div>
</div></section>

<section><h2>熱門話題 Top {top_n}</h2>
<table><tr><th>標題</th><th>摘要</th><th class="num">互動</th><th>情緒</th></tr>
{topic_rows}</table></section>

<section><h2>關鍵字雲</h2><div>{cloud_html}</div></section>

<section><h2>方法論</h2><div class="muted" style="font-size:12px; line-height:1.7;">
情緒分類與話題摘要由 LLM（Claude API）逐則分析；互動分數為 PTT 推文數減噓文數，
Dcard／新聞／論壇無推噓機制者以按讚數或留言數近似。本報告僅彙整公開社群資訊供個人研究參考，
非公司正式輿情監測系統，亦非投資建議。
</div></section>
</div></body></html>"""


def run(day: str | None = None, top_n: int | None = None) -> Path:
    day = day or common.today_str()
    settings = common.load_settings()
    top_n = top_n or settings.get("report", {}).get("top_topics", 10)

    sentiment_path = common.RAW_DIR / day / "sentiment.json"
    records = common.read_json(sentiment_path)
    if not records:
        log.warning("沒有已分析資料，仍會產出空白報告：%s", sentiment_path)

    html_text = render_html(records, day, top_n)

    out_dir = common.BASE_DIR / "report" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"daily_{day}.html"
    out_path.write_text(html_text, encoding="utf-8")
    log.info("報告完成：%s", out_path)
    print(str(out_path))
    return out_path


def _weekly_topic_row(rec: dict) -> str:
    sentiment = rec.get("sentiment", "neutral")
    label = SENTIMENT_LABEL.get(sentiment, sentiment)
    color = SENTIMENT_COLOR.get(sentiment, "#9e9e9e")
    title_html = html.escape(rec.get("title", ""))
    url = html.escape(rec.get("url", "") or "#")
    eng_text = f'推{rec.get("push", 0)} / 噓{rec.get("boo", 0)}'
    return (
        f'<tr><td><a href="{url}" target="_blank">{title_html}</a>'
        f'<div class="role">{html.escape(rec.get("platform", ""))} · {html.escape(rec.get("board", ""))}</div></td>'
        f'<td class="num">{eng_text}</td>'
        f'<td><span class="badge" style="background:{color}">{label}</span></td></tr>'
    )


def _weekly_gap_row(rec: dict) -> str:
    title_html = html.escape(rec.get("title", ""))
    url = html.escape(rec.get("url", "") or "#")
    eng_text = f'推{rec.get("push", 0)} / 噓{rec.get("boo", 0)}'
    return (
        f'<tr><td><a href="{url}" target="_blank">{title_html}</a>'
        f'<div class="role">{html.escape(rec.get("platform", ""))} · {html.escape(rec.get("board", ""))}</div></td>'
        f'<td class="num">{eng_text}</td></tr>'
    )


def render_weekly_html(summary: dict) -> str:
    end_date = summary["end_date"]
    week_days = summary["week_days"]
    this_week = summary["this_week"]
    prev_week = summary["prev_week"]
    change = summary.get("volume_change_pct")
    change_text = f"{change:+.1f}%" if change is not None else "—（無上週資料可比對）"
    change_cls = "muted"
    if change is not None:
        change_cls = "up" if change > 0 else ("down" if change < 0 else "muted")

    topic_rows = "".join(_weekly_topic_row(r) for r in summary.get("top_topics", [])) \
        or '<tr><td colspan="3" class="muted">本週無資料</td></tr>'
    gap_rows = "".join(_weekly_gap_row(r) for r in summary.get("topic_gaps", [])) \
        or '<tr><td colspan="2" class="muted">本週無高互動負面話題</td></tr>'

    generated_at = datetime.now(common.get_timezone()).strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>社群輿情週報 · {week_days[0]} ~ {end_date}</title>
<style>
:root {{ --ink:#1a2332; --sub:#5a6a7e; --line:#dfe5ec; --bg:#f5f7fa; --card:#ffffff;
        --up:#c62828; --down:#2e7d32; --accent:#12406b; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
       font-family:"Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif; font-size:14px; }}
.wrap {{ max-width:1180px; margin:0 auto; padding:28px 20px 60px; }}
header {{ border-left:6px solid var(--accent); padding:4px 0 4px 16px; margin-bottom:24px; }}
header h1 {{ margin:0; font-size:22px; letter-spacing:1px; }}
header .sub {{ color:var(--sub); margin-top:4px; }}
section {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:18px 20px; margin-bottom:18px; }}
h2 {{ font-size:15px; margin:0 0 12px; color:var(--accent); letter-spacing:.5px; }}
.summary-cards {{ display:flex; gap:16px; flex-wrap:wrap; }}
.card {{ flex:1; min-width:140px; border:1px solid var(--line); border-radius:8px; padding:14px 16px; }}
.card .n {{ font-size:26px; font-weight:700; }}
.card .l {{ font-size:12px; color:var(--sub); margin-top:4px; }}
table {{ width:100%; border-collapse:collapse; }}
th {{ text-align:left; font-size:12px; color:var(--sub); font-weight:500;
     border-bottom:2px solid var(--line); padding:6px 8px; white-space:nowrap; }}
td {{ padding:9px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
tr:last-child td {{ border-bottom:none; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }} .muted {{ color:var(--sub); }}
.role {{ font-size:11px; color:var(--sub); margin-top:2px; }}
.badge {{ color:#fff; font-size:11px; padding:2px 10px; border-radius:20px; white-space:nowrap; }}
a {{ color:var(--accent); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
</style></head><body><div class="wrap">
<header>
  <h1>社群輿情週報</h1>
  <div class="sub">統計區間 {week_days[0]} ~ {end_date}｜產生時間 {generated_at}（台北）</div>
</header>

<section><h2>本週 vs 上週</h2>
<div class="summary-cards">
  <div class="card"><div class="n">{this_week['total']}</div><div class="l">本週聲量（上週 {prev_week['total']}）</div></div>
  <div class="card"><div class="n {change_cls}">{change_text}</div><div class="l">聲量變化</div></div>
  <div class="card"><div class="n up">{this_week['positive']}</div><div class="l">正面（上週 {prev_week['positive']}）</div></div>
  <div class="card"><div class="n down">{this_week['negative']}</div><div class="l">負面（上週 {prev_week['negative']}）</div></div>
</div></section>

<section><h2>本週熱門話題 Top 10</h2>
<table><tr><th>標題</th><th class="num">互動</th><th>情緒</th></tr>
{topic_rows}</table></section>

<section><h2>話題缺口（討論度高、情緒偏負、提及公司）</h2>
<div class="role" style="margin-bottom:8px;">近似指標，非公司官方回應狀態的精確判定，僅供人工複核參考</div>
<table><tr><th>標題</th><th class="num">互動</th></tr>
{gap_rows}</table></section>

<section><h2>方法論</h2><div class="muted" style="font-size:12px; line-height:1.7;">
本週彙整比對過去7天與前7天的聲量、情緒分布變化；話題缺口以「高互動 + 負面情緒 + 提及公司」
做為近似指標，用於提醒需人工複核，非精確判定公司是否已official回應。
本報告僅彙整公開社群資訊供個人研究參考，非公司正式輿情監測系統，亦非投資建議。
</div></section>
</div></body></html>"""


def run_weekly(end_date: str | None = None) -> Path:
    end_date = end_date or common.today_str()
    weekly_json = common.RAW_DIR / f"weekly_{end_date}.json"
    summary = common.read_json(weekly_json, default=None)
    if summary is None:
        raise FileNotFoundError(
            f"找不到週彙整資料：{weekly_json}（請先執行 pipeline/aggregate.py --weekly）"
        )

    html_text = render_weekly_html(summary)
    out_dir = common.BASE_DIR / "report" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"weekly_{end_date}.html"
    out_path.write_text(html_text, encoding="utf-8")
    log.info("週報完成：%s", out_path)
    print(str(out_path))
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="產出社群輿情報告 HTML")
    parser.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD，預設今天")
    parser.add_argument("--weekly", action="store_true", help="產出週報而非日報")
    args = parser.parse_args()
    if args.weekly:
        run_weekly(end_date=args.date)
    else:
        run(day=args.date)
