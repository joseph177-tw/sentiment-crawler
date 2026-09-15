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
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common
import keyword_lib

log = common.setup_logging("report.render")

SENTIMENT_LABEL = {"positive": "正面", "neutral": "中性", "negative": "負面"}
SENTIMENT_COLOR = {"positive": "#c62828", "neutral": "#9e9e9e", "negative": "#2e7d32"}

# 四個頁面（日報/週報/關鍵字/盤勢）共用的樣式與導覽列，拆成常數避免四邊各自維護一份
# 幾乎一樣的 <style>，改一次顏色卻要記得改四個地方。
BASE_CSS = """
:root { --ink:#1a2332; --sub:#5a6a7e; --line:#dfe5ec; --bg:#f5f7fa; --card:#ffffff;
        --up:#c62828; --down:#2e7d32; --accent:#12406b; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font-family:"Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif; font-size:14px; }
.wrap { max-width:1180px; margin:0 auto; padding:28px 20px 60px; }
nav.topnav { display:flex; align-items:center; gap:18px; padding:12px 20px; background:var(--accent); }
nav.topnav a { color:#fff; opacity:.75; font-size:13px; font-weight:500; text-decoration:none; }
nav.topnav a.active, nav.topnav a:hover { opacity:1; text-decoration:underline; }
.nav-search { position:relative; margin-left:auto; }
.nav-search input { border:1px solid rgba(255,255,255,.35); background:rgba(255,255,255,.12); color:#fff;
                     border-radius:6px; padding:5px 10px; font-size:13px; width:160px; outline:none; }
.nav-search input::placeholder { color:rgba(255,255,255,.65); }
.nav-search input:focus { background:rgba(255,255,255,.2); }
.nav-search .results { position:absolute; top:34px; right:0; background:var(--card); border:1px solid var(--line);
                        border-radius:8px; box-shadow:0 4px 14px rgba(0,0,0,.18); min-width:200px; max-height:280px;
                        overflow-y:auto; z-index:50; display:none; }
.nav-search .results.show { display:block; }
.nav-search .result-item { padding:8px 12px; font-size:13px; color:var(--ink); cursor:pointer; }
.nav-search .result-item:hover { background:#eef2f7; }
.nav-search .result-item .code { color:var(--sub); font-size:11px; margin-left:6px; }
header { border-left:6px solid var(--accent); padding:4px 0 4px 16px; margin:24px 0; }
header h1 { margin:0; font-size:22px; letter-spacing:1px; }
header .sub { color:var(--sub); margin-top:4px; }
section { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:18px 20px; margin-bottom:18px; }
h2 { font-size:15px; margin:0 0 12px; color:var(--accent); letter-spacing:.5px; }
h3 { font-size:14px; margin:0 0 8px; color:var(--ink); }
.summary-cards { display:flex; gap:16px; flex-wrap:wrap; }
.card { flex:1; min-width:140px; border:1px solid var(--line); border-radius:8px; padding:14px 16px; }
.card .n { font-size:26px; font-weight:700; }
.card .l { font-size:12px; color:var(--sub); margin-top:4px; }
table { width:100%; border-collapse:collapse; }
th { text-align:left; font-size:12px; color:var(--sub); font-weight:500;
     border-bottom:2px solid var(--line); padding:6px 8px; white-space:nowrap; }
td { padding:9px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
tr:last-child td { border-bottom:none; }
.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
.up { color:var(--up); } .down { color:var(--down); } .muted { color:var(--sub); }
.role { font-size:11px; color:var(--sub); margin-top:2px; }
.badge { color:#fff; font-size:11px; padding:2px 10px; border-radius:20px; white-space:nowrap; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
.tag { display:inline-block; margin:3px 6px 3px 0; padding:2px 8px; border-radius:12px;
       background:#eef2f7; color:var(--accent); }
.chip { display:inline-block; margin:2px 6px 2px 0; padding:2px 8px; border-radius:10px;
        background:#eef2f7; color:var(--sub); font-size:11px; }
"""


def _nav_html(active: str, prefix: str = "") -> str:
    """prefix 給子目錄頁面用（例如 docs/stocks/2330.html 要用 "../" 才能連回上層頁面）。"""
    items = [("index.html", "日報"), ("weekly.html", "週報"),
             ("keywords.html", "關鍵字"), ("market.html", "盤勢")]
    links = "".join(
        f'<a href="{prefix}{href}"{" class=active" if key == active else ""}>{label}</a>'
        for key, (href, label) in zip(["index", "weekly", "keywords", "market"], items)
    )

    # 個股代號/名稱搜尋：清單來自 docs/stock_index.json（run_market() 產出，
    # 只包含目前有 docs/stocks/<code>.html 的個股），找不到檔案時搜尋就是靜默無結果。
    search = f"""<div class="nav-search">
  <input type="text" id="stock-search-input" placeholder="搜尋個股代號/名稱" autocomplete="off">
  <div class="results" id="stock-search-results"></div>
</div>
<script>
(function() {{
  const input = document.getElementById('stock-search-input');
  const results = document.getElementById('stock-search-results');
  let stocks = [];
  fetch('{prefix}stock_index.json').then(r => r.ok ? r.json() : []).then(data => {{ stocks = data; }}).catch(() => {{}});

  function render(matches) {{
    results.innerHTML = matches.map(s =>
      `<div class="result-item" data-code="${{s.code}}">${{s.name}}<span class="code">${{s.code}}</span></div>`
    ).join('');
    results.classList.toggle('show', matches.length > 0);
  }}

  function doSearch(q) {{
    q = q.trim().toLowerCase();
    if (!q) {{ render([]); return; }}
    const matches = stocks.filter(s => s.code.includes(q) || s.name.toLowerCase().includes(q)).slice(0, 8);
    render(matches);
  }}

  function go(code) {{ window.location.href = '{prefix}stocks/' + code + '.html'; }}

  input.addEventListener('input', () => doSearch(input.value));
  input.addEventListener('keydown', (e) => {{
    if (e.key === 'Enter') {{
      const first = results.querySelector('.result-item');
      if (first) go(first.dataset.code);
    }}
  }});
  results.addEventListener('click', (e) => {{
    const item = e.target.closest('.result-item');
    if (item) go(item.dataset.code);
  }});
  document.addEventListener('click', (e) => {{
    if (!e.target.closest('.nav-search')) results.classList.remove('show');
  }});
}})();
</script>"""

    return f'<nav class="topnav">{links}{search}</nav>'


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
    cloud = keyword_lib.keyword_cloud(records)

    topic_rows = "".join(_topic_row(r) for r in topics) or '<tr><td colspan="4" class="muted">今日無資料</td></tr>'
    cloud_html = "".join(
        f'<a class="tag" style="font-size:{11 + min(count, 8) * 2}px" '
        f'href="keywords.html#{html.escape(w)}">{html.escape(w)}</a>'
        for w, count in cloud
    ) or '<span class="muted">無足夠資料產生關鍵字雲</span>'

    generated_at = datetime.now(common.get_timezone()).strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>社群輿情日報 · {day}</title>
<style>{BASE_CSS}</style></head><body>{_nav_html('index')}<div class="wrap">
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

    # 同步覆蓋 docs/index.html，讓 GitHub Pages 的固定網址永遠指向最新一天的報告
    pages_dir = common.BASE_DIR / "docs"
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "index.html").write_text(html_text, encoding="utf-8")

    log.info("報告完成：%s（同步更新 docs/index.html）", out_path)
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
<style>{BASE_CSS}</style></head><body>{_nav_html('weekly')}<div class="wrap">
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

    pages_dir = common.BASE_DIR / "docs"
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "weekly.html").write_text(html_text, encoding="utf-8")

    log.info("週報完成：%s（同步更新 docs/weekly.html）", out_path)
    print(str(out_path))
    return out_path


def _keyword_post_row(post: dict) -> str:
    sentiment = post.get("sentiment", "neutral")
    label = SENTIMENT_LABEL.get(sentiment, sentiment)
    color = SENTIMENT_COLOR.get(sentiment, "#9e9e9e")
    title_html = html.escape(post.get("title", ""))
    url = html.escape(post.get("url", "") or "#")
    eng_text = f'推{post.get("push", 0)} / 噓{post.get("boo", 0)}'
    return (
        f'<tr><td><a href="{url}" target="_blank">{title_html}</a>'
        f'<div class="role">{html.escape(post.get("platform", ""))} · {html.escape(post.get("board", ""))}'
        f' · {html.escape(post.get("day", ""))}</div></td>'
        f'<td class="num">{eng_text}</td>'
        f'<td><span class="badge" style="background:{color}">{label}</span></td></tr>'
    )


def _keyword_section(kw: dict) -> str:
    word = html.escape(kw["word"])
    source_chips = "".join(
        f'<span class="chip">{html.escape(src)} × {n}</span>'
        for src, n in kw["sources"].items()
    )
    rows = "".join(_keyword_post_row(p) for p in kw["posts"])
    return f"""<section id="{word}">
<h2>{word}<span class="badge" style="background:var(--accent); margin-left:8px;">{kw['count']} 則</span></h2>
<div class="role" style="margin-bottom:10px;">總互動數（推+噓）{kw['total_engagement']}｜來源分布：{source_chips}</div>
<table><tr><th>標題</th><th class="num">互動</th><th>情緒</th></tr>
{rows}</table>
</section>"""


def render_keywords_page(index: dict) -> str:
    keywords = index.get("keywords", [])[:50]
    generated_at = datetime.now(common.get_timezone()).strftime("%Y-%m-%d %H:%M")

    bubble_payload = json.dumps(
        [{"word": kw["word"], "count": kw["count"]} for kw in keywords], ensure_ascii=False
    )
    bubble_html = (
        '<div id="bubble-chart" style="width:100%; height:520px;"></div>'
        if keywords else '<span class="muted">尚無足夠資料</span>'
    )
    bubble_script = f"""<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>
const BUBBLE_DATA = {bubble_payload};
if (BUBBLE_DATA.length) {{
  const el = document.getElementById('bubble-chart');
  let lastWidth = 0;

  function drawBubbles(animate) {{
    const width = el.clientWidth, height = el.clientHeight;
    if (!width || !height) return;
    lastWidth = width;
    el.innerHTML = '';
    const svg = d3.select(el).append('svg').attr('width', width).attr('height', height);

    const root = d3.pack()
      .size([width - 4, height - 4])
      .padding(4)(d3.hierarchy({{children: BUBBLE_DATA}}).sum(d => d.count));

    const color = d3.scaleSequential(d3.interpolateBlues)
      .domain([0, d3.max(BUBBLE_DATA, d => d.count)]);

    const node = svg.selectAll('g')
      .data(root.leaves())
      .join('g')
      .attr('transform', d => `translate(${{d.x}},${{d.y}})`)
      .style('cursor', 'pointer')
      .on('click', (event, d) => {{
        const target = document.getElementById(d.data.word);
        if (target) target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
      }});

    const circle = node.append('circle')
      .attr('fill', d => color(d.data.count))
      .attr('stroke', '#12406b')
      .attr('stroke-width', 0.5);
    if (animate) {{
      circle.attr('r', 0).transition().duration(700).ease(d3.easeCubicOut).attr('r', d => d.r);
    }} else {{
      circle.attr('r', d => d.r);
    }}

    node.append('title').text(d => `${{d.data.word}}（${{d.data.count}} 則）`);

    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', '0.32em')
      .style('fill', d => d.data.count > (d3.max(BUBBLE_DATA, x => x.count) * 0.4) ? '#fff' : '#12406b')
      .style('font-size', d => Math.max(10, Math.min(16, d.r / 2.6)) + 'px')
      .style('font-weight', 600)
      .style('pointer-events', 'none')
      .text(d => d.r > 18 ? d.data.word : '');
  }}

  drawBubbles(true);

  // 只在「寬度」真的變了才重畫（手機捲動時網址列收合展開只會動高度，
  // 若不過濾會在每次捲動都觸發 resize，整頁卡住／閃爍），並且用
  // debounce 避免拖曳視窗時瘋狂重算。
  let resizeTimer = null;
  window.addEventListener('resize', () => {{
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {{
      if (el.clientWidth !== lastWidth) drawBubbles(false);
    }}, 200);
  }});
}}
</script>"""

    sections = "".join(_keyword_section(kw) for kw in keywords)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>關鍵字總覽 · 社群輿情</title>
<style>{BASE_CSS}</style></head><body>{_nav_html('keywords')}<div class="wrap">
<header>
  <h1>關鍵字總覽</h1>
  <div class="sub">累積掃描 {index.get('total_posts_scanned', 0)} 則貼文｜產生時間 {generated_at}（台北）
  ｜以下僅列前 50 名，泡泡大小＝出現則數，點擊泡泡可跳到下方詳細列表</div>
</header>

<section><h2>關鍵字泡泡圖</h2>{bubble_html}</section>

{sections}

<section><h2>方法論</h2><div class="muted" style="font-size:12px; line-height:1.7;">
關鍵字取自各貼文標題與 LLM 摘要，經 jieba 斷詞後過濾常見虛詞與 PTT/Dcard 標題分類標籤
（如「情報」「閒聊」）。「總互動數」為該關鍵字所有相關貼文的 PTT 推文數+噓文數加總
（Dcard／新聞／論壇無推噓機制者以按讚數/留言數近似），代表社群關注度、非網頁點擊次數。
出現次數低於 2 次的關鍵字不列入，避免單一貼文的偶發用字灌爆列表。
</div></section>
</div>
{bubble_script}
</body></html>"""


def run_keywords() -> Path:
    index_path = common.RAW_DIR / "keyword_index.json"
    index = common.read_json(index_path, default=None)
    if index is None:
        raise FileNotFoundError(f"找不到關鍵字索引：{index_path}（請先執行 pipeline/aggregate.py --keywords）")

    html_text = render_keywords_page(index)
    pages_dir = common.BASE_DIR / "docs"
    pages_dir.mkdir(parents=True, exist_ok=True)
    out_path = pages_dir / "keywords.html"
    out_path.write_text(html_text, encoding="utf-8")
    log.info("關鍵字頁完成：%s", out_path)
    print(str(out_path))
    return out_path


def _market_post_row(post: dict) -> str:
    sentiment = post.get("sentiment", "neutral")
    label = SENTIMENT_LABEL.get(sentiment, sentiment)
    color = SENTIMENT_COLOR.get(sentiment, "#9e9e9e")
    title_html = html.escape(post.get("title", ""))
    url = html.escape(post.get("url", "") or "#")
    eng_text = f'推{post.get("push", 0)} / 噓{post.get("boo", 0)}'
    return (
        f'<tr><td><a href="{url}" target="_blank">{title_html}</a>'
        f'<div class="role">{html.escape(post.get("platform", ""))} · {html.escape(post.get("board", ""))}'
        f' · {html.escape(post.get("day", ""))}</div></td>'
        f'<td class="num">{eng_text}</td>'
        f'<td><span class="badge" style="background:{color}">{label}</span></td></tr>'
    )


def _market_section(stock: dict) -> str:
    code = html.escape(stock["code"])
    name = html.escape(stock["name"])
    rows = "".join(_market_post_row(p) for p in stock["posts"])
    return f"""<section id="{code}">
<h2><a href="stocks/{code}.html">{name}</a><span class="muted" style="font-weight:400;">（{code}）</span>
<span class="badge" style="background:var(--accent); margin-left:8px;">貼文提及 {stock['mention_count']} 次</span>
<a href="stocks/{code}.html" class="tag" style="margin-left:8px; font-size:12px;">詳細行情 →</a></h2>
<div id="chart-{code}" style="height:360px;"></div>
<h3 style="margin-top:14px;">相關貼文</h3>
<table><tr><th>標題</th><th class="num">互動</th><th>情緒</th></tr>
{rows}</table>
</section>"""


def render_market_page(market_data: dict) -> str:
    stocks = market_data.get("stocks", [])
    generated_at = datetime.now(common.get_timezone()).strftime("%Y-%m-%d %H:%M")

    index_chips = "".join(
        f'<a class="tag" href="#{html.escape(s["code"])}">{html.escape(s["name"])} '
        f'<span class="muted">({s["mention_count"]})</span></a>'
        for s in stocks
    ) or '<span class="muted">尚未偵測到任何個股提及</span>'

    sections = "".join(_market_section(s) for s in stocks)

    chart_payload = json.dumps(
        {s["code"]: {"name": s["name"], "prices": s["prices"]} for s in stocks},
        ensure_ascii=False,
    )

    chart_script = f"""<script src="https://cdnjs.cloudflare.com/ajax/libs/echarts/5.6.0/echarts.min.js"></script>
<script>
const MARKET_DATA = {chart_payload};
Object.entries(MARKET_DATA).forEach(([code, stock]) => {{
  const el = document.getElementById('chart-' + code);
  if (!el) return;
  const chart = echarts.init(el);
  const dates = stock.prices.map(p => p.date);
  const candles = stock.prices.map(p => [p.open, p.close, p.low, p.high]);
  const volumes = stock.prices.map(p => p.volume);
  chart.setOption({{
    tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }},
    grid: [
      {{ left: 56, right: 20, top: 20, height: 220 }},
      {{ left: 56, right: 20, top: 260, height: 60 }}
    ],
    xAxis: [
      {{ type: 'category', data: dates, gridIndex: 0, axisLabel: {{ show: false }} }},
      {{ type: 'category', data: dates, gridIndex: 1, axisLabel: {{ fontSize: 10 }} }}
    ],
    yAxis: [
      {{ type: 'value', gridIndex: 0, scale: true, axisLabel: {{ fontSize: 10 }} }},
      {{ type: 'value', gridIndex: 1, show: false }}
    ],
    dataZoom: [
      {{ type: 'inside', xAxisIndex: [0, 1] }},
      {{ type: 'slider', xAxisIndex: [0, 1], height: 14, bottom: 0 }}
    ],
    series: [
      {{
        type: 'candlestick', data: candles, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: {{ color: '#c62828', color0: '#2e7d32', borderColor: '#c62828', borderColor0: '#2e7d32' }}
      }},
      {{ type: 'bar', data: volumes, xAxisIndex: 1, yAxisIndex: 1, itemStyle: {{ color: '#9fb3c8' }} }}
    ]
  }});
  let resizeTimer = null;
  window.addEventListener('resize', () => {{
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => chart.resize(), 200);
  }});
}});
</script>"""

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>盤勢總覽 · 社群輿情</title>
<style>{BASE_CSS}</style></head><body>{_nav_html('market')}<div class="wrap">
<header>
  <h1>盤勢總覽</h1>
  <div class="sub">產生時間 {generated_at}（台北）｜資料來源：TWSE 上市清單（自動偵測貼文提及個股）
  ＋ Yahoo Finance 歷史價格</div>
</header>

<section><h2>索引</h2><div>{index_chips}</div></section>

{sections}

<section><h2>方法論</h2><div class="muted" style="font-size:12px; line-height:1.7;">
個股偵測範圍為 TWSE 上市股票（不含上櫃 TPEx），比對方式為貼文標題／摘要與 TWSE 證券
名稱的子字串比對，非官方全稱比對，可能有漏抓或極少數誤判。K 線圖為近半年日 K，
資料來源 Yahoo Finance，非即時報價（有延遲），僅供研究參考，非投資建議。
</div></section>
</div>
{chart_script}
</body></html>"""


def _valuation_html(valuation: dict | None) -> str:
    if not valuation:
        return '<div class="muted">尚無估值資料</div>'
    items = [
        ("本益比", valuation.get("pe"), ""), ("殖利率", valuation.get("yield_pct"), "%"),
        ("股價淨值比", valuation.get("pb"), ""),
    ]
    cards = "".join(
        f'<div class="card"><div class="n" style="font-size:18px;">{v:.2f}{suffix}</div><div class="l">{k}</div></div>'
        for k, v, suffix in items if v is not None
    )
    return f'<div class="summary-cards">{cards}</div>' if cards else '<div class="muted">尚無估值資料</div>'


def _margin_html(margin: dict | None) -> str:
    if not margin:
        return '<div class="muted">尚無融資融券資料</div>'
    mb, mp = margin.get("margin_balance"), margin.get("margin_prev_balance")
    sb, sp = margin.get("short_balance"), margin.get("short_prev_balance")
    m_change = (mb - mp) if (mb is not None and mp is not None) else None
    s_change = (sb - sp) if (sb is not None and sp is not None) else None
    m_cls = "up" if (m_change or 0) > 0 else ("down" if (m_change or 0) < 0 else "muted")
    s_cls = "up" if (s_change or 0) > 0 else ("down" if (s_change or 0) < 0 else "muted")
    return f"""<div class="summary-cards">
  <div class="card"><div class="n" style="font-size:18px;">{mb:,}</div><div class="l">融資餘額（張）</div>
    <div class="{m_cls}" style="font-size:11px; margin-top:2px;">較前日 {m_change:+,}</div></div>
  <div class="card"><div class="n" style="font-size:18px;">{sb:,}</div><div class="l">融券餘額（張）</div>
    <div class="{s_cls}" style="font-size:11px; margin-top:2px;">較前日 {s_change:+,}</div></div>
</div>
<div class="role" style="margin-top:8px;">資料日期 {html.escape(margin.get("date", ""))}</div>""" if mb is not None and sb is not None \
        else '<div class="muted">尚無融資融券資料</div>'


def _revenue_html(revenue: dict | None) -> str:
    if not revenue or revenue.get("revenue") is None:
        return '<div class="muted">尚無月營收資料</div>'
    ym = str(revenue.get("year_month", ""))
    ym_text = f"{int(ym[:3]) + 1911}年{ym[3:]}月" if len(ym) >= 5 else ym
    rev_yi = revenue["revenue"] / 1e8
    acc_yi = (revenue.get("accumulated_revenue") or 0) / 1e8
    mom, yoy, acc_yoy = revenue.get("mom_pct"), revenue.get("yoy_pct"), revenue.get("accumulated_yoy_pct")

    def _pct_card(label, val):
        if val is None:
            return ""
        cls = "up" if val > 0 else ("down" if val < 0 else "muted")
        return f'<div class="card"><div class="n {cls}" style="font-size:18px;">{val:+.1f}%</div><div class="l">{label}</div></div>'

    cards = (
        f'<div class="card"><div class="n" style="font-size:18px;">{rev_yi:.2f} 億</div><div class="l">當月營收（{ym_text}）</div></div>'
        + _pct_card("月增率 MoM", mom) + _pct_card("年增率 YoY", yoy)
        + f'<div class="card"><div class="n" style="font-size:18px;">{acc_yi:.2f} 億</div><div class="l">累計營收</div></div>'
        + _pct_card("累計年增率", acc_yoy)
    )
    industry = revenue.get("industry")
    industry_html = f'<div class="role" style="margin-top:8px;">產業別：{html.escape(industry)}</div>' if industry else ""
    return f'<div class="summary-cards">{cards}</div>{industry_html}'


def _company_html(company: dict | None) -> str:
    if not company:
        return '<div class="muted">尚無公司基本資料</div>'
    capital = company.get("capital")
    capital_text = f"{int(capital) / 1e8:.1f} 億元" if capital and capital.isdigit() else "—"

    def _date(s):
        # TWSE 公司基本資料的上市/成立日期是西元 8 碼 YYYYMMDD（例如 "19940905"），
        # 跟其他 TWSE 端點常見的民國 7 碼格式（例如出表日期 "1150913"）不一樣，
        # 不要套用民國+1911 轉換，之前這裡搞混過導致日期整個算錯。
        if not s or len(s) < 8 or not s.isdigit():
            return "—"
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"

    rows = [
        ("公司全名", company.get("full_name") or "—"),
        ("實收資本額", capital_text),
        ("上市日期", _date(company.get("listed_date"))),
        ("成立日期", _date(company.get("established_date"))),
        ("董事長", company.get("chairman") or "—"),
        ("總經理", company.get("general_manager") or "—"),
        ("發言人", company.get("spokesperson") or "—"),
    ]
    items = "".join(f'<li><b>{k}</b>：{html.escape(str(v))}</li>' for k, v in rows)
    website = company.get("website")
    if website:
        items += f'<li><b>官網</b>：<a href="{html.escape(website)}" target="_blank">{html.escape(website)}</a></li>'
    return f'<ul style="margin:0; padding-left:18px; font-size:13px; line-height:2;">{items}</ul>'


def _material_info_html(records: list[dict]) -> str:
    if not records:
        return '<div class="muted">近期無重大訊息公告</div>'

    items = ""
    for r in records:
        subject = html.escape((r.get("subject") or "").replace("\r\n", " ").strip())
        detail = html.escape((r.get("detail") or "").strip())
        clause = html.escape(r.get("clause") or "")
        date = html.escape(r.get("announce_date") or "")
        detail_html = (
            f'<details style="margin-top:4px;"><summary class="muted" style="cursor:pointer; font-size:11px;">'
            f'查看完整說明</summary><div style="white-space:pre-wrap; font-size:12px; margin-top:6px; '
            f'color:var(--sub);">{detail}</div></details>' if detail else ""
        )
        items += (
            f'<li style="margin-bottom:12px;"><div>{subject}</div>'
            f'<div class="role">{date}{" · " + clause if clause else ""}</div>{detail_html}</li>'
        )
    return f'<ul style="margin:0; padding-left:18px; font-size:13px;">{items}</ul>'


def _institutional_html(institutional: list[dict], code: str) -> tuple[str, str]:
    """回傳 (HTML, chart_script)。近期趨勢用 ECharts 長條圖，並列出最新一日的三大法人明細。"""
    if not institutional:
        return '<div class="muted">尚無三大法人買賣超資料</div>', ""

    latest = institutional[-1]

    def _stat(label, val):
        cls = "up" if val > 0 else ("down" if val < 0 else "muted")
        return (f'<div class="card"><div class="n {cls}" style="font-size:16px;">{val / 1000:+,.0f} 張</div>'
                f'<div class="l">{label}</div></div>')

    cards = (
        _stat("外資買賣超", latest["foreign"]) + _stat("投信買賣超", latest["trust"]) +
        _stat("自營商買賣超", latest["dealer"]) + _stat("三大法人合計", latest["total"])
    )
    html_out = (
        f'<div class="role" style="margin-bottom:8px;">最新資料日期 {html.escape(latest["date"])}</div>'
        f'<div class="summary-cards" style="margin-bottom:14px;">{cards}</div>'
        f'<div id="inst-chart-{code}" style="height:220px;"></div>'
    )

    payload = json.dumps(institutional, ensure_ascii=False)
    script = f"""
(function() {{
  const data = {payload};
  const el = document.getElementById('inst-chart-{code}');
  if (!el || !data.length) return;
  const chart = echarts.init(el);
  chart.setOption({{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ top: 0, textStyle: {{ fontSize: 10 }} }},
    grid: {{ left: 60, right: 20, top: 28, bottom: 24 }},
    xAxis: {{ type: 'category', data: data.map(d => d.date), axisLabel: {{ fontSize: 10 }} }},
    yAxis: {{ type: 'value', name: '張', axisLabel: {{ fontSize: 10, formatter: v => (v/1000).toLocaleString() }} }},
    series: [
      {{ name: '外資', type: 'bar', data: data.map(d => d.foreign) }},
      {{ name: '投信', type: 'bar', data: data.map(d => d.trust) }},
      {{ name: '自營商', type: 'bar', data: data.map(d => d.dealer) }}
    ]
  }});
  window.addEventListener('resize', () => chart.resize());
}})();
"""
    return html_out, script


def _slice_indicators(indicators: dict | None, n: int) -> dict | None:
    """把技術指標（在 2 年基礎資料上算出來的）尾端切 n 筆，對齊某個分頁籤的日期範圍。"""
    if not indicators or n <= 0:
        return None

    def tail(arr):
        return arr[-n:] if isinstance(arr, list) else arr

    return {
        "dates": tail(indicators["dates"]),
        "ma": {k: tail(v) for k, v in indicators["ma"].items()},
        "rsi14": tail(indicators["rsi14"]),
        "macd": {k: tail(v) for k, v in indicators["macd"].items()},
        "kd": {k: tail(v) for k, v in indicators["kd"].items()},
        "dmi": {k: tail(v) for k, v in indicators["dmi"].items()},
        "boll": {k: tail(v) for k, v in indicators["boll"].items()},
        "bias20": tail(indicators["bias20"]),
        "obv": tail(indicators["obv"]),
    }


def render_stock_detail_page(stock_code: str, stock_meta: dict, detail: dict) -> str:
    name = html.escape(stock_meta.get("name", stock_code))
    posts = stock_meta.get("posts", [])
    detail = detail or {}
    quote = detail.get("quote") or {}
    series = detail.get("series") or {}
    indicators = detail.get("indicators")

    price = quote.get("last_price")
    change = quote.get("change")
    change_pct = quote.get("change_pct")
    price_cls = "up" if (change or 0) > 0 else ("down" if (change or 0) < 0 else "muted")
    price_text = f"{price:.2f}" if price is not None else "—"
    change_text = f"{change:+.2f} ({change_pct:+.2f}%)" if change is not None else "尚無即時行情資料"

    stat_items = [
        ("開盤", quote.get("open")), ("最高", quote.get("high")), ("最低", quote.get("low")),
        ("漲停", quote.get("limit_up")), ("跌停", quote.get("limit_down")),
    ]
    stats_html = "".join(
        f'<div class="card"><div class="n" style="font-size:18px;">{v:.2f}</div><div class="l">{k}</div></div>'
        for k, v in stat_items if v is not None
    )
    if quote.get("total_volume"):
        stats_html += (f'<div class="card"><div class="n" style="font-size:18px;">'
                        f'{quote["total_volume"]:,}</div><div class="l">總量（張）</div></div>')

    tab_labels = list(series.keys()) or ["六月"]
    default_tab = next((t for t in ["六月", "三月", "近月"] if t in series and series[t]), tab_labels[0])
    tabs_html = "".join(
        f'<button class="tab-btn{" active" if t == default_tab else ""}" data-tab="{html.escape(t)}">{html.escape(t)}</button>'
        for t in tab_labels
    )

    # 技術指標只對「日線」分頁（近月/三月/六月/一年）有意義，切到跟各分頁一樣的長度
    indicators_by_tab = {
        label: _slice_indicators(indicators, len(points))
        for label, points in series.items() if len(points) and indicators
    }

    posts_rows = "".join(_market_post_row(p) for p in posts) or \
        '<tr><td colspan="3" class="muted">尚無相關貼文</td></tr>'

    perplexity = detail.get("perplexity")
    if perplexity and perplexity.get("summary"):
        pplx_html = f'<div style="font-size:13px; line-height:1.8; white-space:pre-wrap;">{html.escape(perplexity["summary"])}</div>'
        if perplexity.get("citations"):
            cites = "".join(
                f'<li><a href="{html.escape(c)}" target="_blank">{html.escape(c)}</a></li>'
                for c in perplexity["citations"][:5]
            )
            pplx_html += f'<ul style="margin-top:8px; font-size:11px;">{cites}</ul>'
    else:
        pplx_html = '<div class="muted">尚未設定 Perplexity API Key，暫無深度產業/基本面分析。</div>'

    institutional_html, institutional_script = _institutional_html(detail.get("institutional") or [], stock_code)
    valuation_html_ = _valuation_html(detail.get("valuation"))
    margin_html_ = _margin_html(detail.get("margin"))
    revenue_html_ = _revenue_html(detail.get("revenue"))
    company_html_ = _company_html(detail.get("company"))
    material_info_html_ = _material_info_html(detail.get("material_info") or [])

    series_payload = json.dumps(series, ensure_ascii=False)
    indicators_payload = json.dumps(indicators_by_tab, ensure_ascii=False)
    generated_at = datetime.now(common.get_timezone()).strftime("%Y-%m-%d %H:%M")

    chart_script = f"""<script src="https://cdnjs.cloudflare.com/ajax/libs/echarts/5.6.0/echarts.min.js"></script>
<script>
const SERIES = {series_payload};
const INDICATORS_BY_TAB = {indicators_payload};
const chartEl = document.getElementById('detail-chart');
const chart = echarts.init(chartEl);

const MA_COLORS = {{5:'#e57373', 10:'#f6b93b', 20:'#3498db', 60:'#8e44ad', 120:'#16a085', 240:'#7f8c8d'}};
let activeMAs = new Set(['20']);
let showBoll = false;
let oscillator = 'rsi14';

function currentTabLabel() {{
  const btn = document.querySelector('.tab-btn.active');
  return btn ? btn.dataset.tab : Object.keys(SERIES)[0];
}}

function buildOscillatorSeries(ind) {{
  if (!ind || oscillator === 'none') return [];
  if (oscillator === 'rsi14') return [
    {{ name: 'RSI14', type: 'line', data: ind.rsi14, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: {{ width: 1 }} }}
  ];
  if (oscillator === 'macd') return [
    {{ name: 'DIF', type: 'line', data: ind.macd.dif, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: {{ width: 1 }} }},
    {{ name: 'DEA', type: 'line', data: ind.macd.dea, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: {{ width: 1 }} }},
    {{ name: 'MACD', type: 'bar', data: ind.macd.hist, xAxisIndex: 2, yAxisIndex: 2, itemStyle: {{ color: '#9fb3c8' }} }}
  ];
  if (oscillator === 'kd') return [
    {{ name: 'K', type: 'line', data: ind.kd.k, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: {{ width: 1 }} }},
    {{ name: 'D', type: 'line', data: ind.kd.d, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: {{ width: 1 }} }}
  ];
  if (oscillator === 'dmi') return [
    {{ name: '+DI', type: 'line', data: ind.dmi.plus_di, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: {{ width: 1 }} }},
    {{ name: '-DI', type: 'line', data: ind.dmi.minus_di, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: {{ width: 1 }} }},
    {{ name: 'ADX', type: 'line', data: ind.dmi.adx, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: {{ width: 1, type: 'dashed' }} }}
  ];
  if (oscillator === 'bias20') return [
    {{ name: 'BIAS20', type: 'line', data: ind.bias20, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: {{ width: 1 }} }}
  ];
  if (oscillator === 'obv') return [
    {{ name: 'OBV', type: 'line', data: ind.obv, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: {{ width: 1 }} }}
  ];
  return [];
}}

function renderTab(label) {{
  const points = SERIES[label] || [];
  const dates = points.map(p => p.t);
  const candles = points.map(p => [p.open, p.close, p.low, p.high]);
  const volumes = points.map(p => p.volume);
  const ind = INDICATORS_BY_TAB[label];

  document.getElementById('indicator-controls').style.display = ind ? 'flex' : 'none';
  document.getElementById('indicator-unavailable').style.display = ind ? 'none' : 'block';

  const overlay = [];
  if (ind) {{
    activeMAs.forEach(n => {{
      overlay.push({{ name: 'MA' + n, type: 'line', data: ind.ma[n], xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: {{ width: 1 }}, color: MA_COLORS[n] }});
    }});
    if (showBoll) {{
      overlay.push(
        {{ name: '布林上緣', type: 'line', data: ind.boll.upper, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: {{ width: 1, type: 'dashed', color: '#999' }} }},
        {{ name: '布林中軌', type: 'line', data: ind.boll.mid, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: {{ width: 1, color: '#999' }} }},
        {{ name: '布林下緣', type: 'line', data: ind.boll.lower, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: {{ width: 1, type: 'dashed', color: '#999' }} }}
      );
    }}
  }}
  const oscSeries = buildOscillatorSeries(ind);

  chart.setOption({{
    tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }},
    legend: {{ show: overlay.length > 0 || oscSeries.length > 1, top: 0, textStyle: {{ fontSize: 10 }} }},
    grid: [
      {{ left: 56, right: 20, top: 26, height: 190 }},
      {{ left: 56, right: 20, top: 220, height: 50 }},
      {{ left: 56, right: 20, top: 288, height: 80 }}
    ],
    xAxis: [
      {{ type: 'category', data: dates, gridIndex: 0, axisLabel: {{ show: false }} }},
      {{ type: 'category', data: dates, gridIndex: 1, axisLabel: {{ show: false }} }},
      {{ type: 'category', data: dates, gridIndex: 2, axisLabel: {{ fontSize: 10 }} }}
    ],
    yAxis: [
      {{ type: 'value', gridIndex: 0, scale: true, axisLabel: {{ fontSize: 10 }} }},
      {{ type: 'value', gridIndex: 1, show: false }},
      {{ type: 'value', gridIndex: 2, scale: true, axisLabel: {{ fontSize: 10 }} }}
    ],
    dataZoom: [
      {{ type: 'inside', xAxisIndex: [0, 1, 2] }},
      {{ type: 'slider', xAxisIndex: [0, 1, 2], height: 12, bottom: 0 }}
    ],
    series: [
      {{
        type: 'candlestick', data: candles, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: {{ color: '#c62828', color0: '#2e7d32', borderColor: '#c62828', borderColor0: '#2e7d32' }}
      }},
      ...overlay,
      {{ type: 'bar', data: volumes, xAxisIndex: 1, yAxisIndex: 1, itemStyle: {{ color: '#9fb3c8' }} }},
      ...oscSeries
    ]
  }}, true);
}}

document.querySelectorAll('.tab-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderTab(btn.dataset.tab);
  }});
}});
document.querySelectorAll('.ma-toggle').forEach(cb => {{
  cb.addEventListener('change', () => {{
    if (cb.checked) activeMAs.add(cb.value); else activeMAs.delete(cb.value);
    renderTab(currentTabLabel());
  }});
}});
document.getElementById('boll-toggle').addEventListener('change', (e) => {{
  showBoll = e.target.checked;
  renderTab(currentTabLabel());
}});
document.getElementById('osc-select').addEventListener('change', (e) => {{
  oscillator = e.target.value;
  renderTab(currentTabLabel());
}});

renderTab('{default_tab}');
let detailResizeTimer = null;
window.addEventListener('resize', () => {{
  clearTimeout(detailResizeTimer);
  detailResizeTimer = setTimeout(() => chart.resize(), 200);
}});
{institutional_script}
</script>"""

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name}（{stock_code}）· 個股詳情</title>
<style>{BASE_CSS}
.tab-btn {{ border:1px solid var(--line); background:var(--card); color:var(--sub); font-size:12px;
           padding:5px 12px; border-radius:14px; cursor:pointer; margin-right:6px; }}
.tab-btn.active {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
#indicator-controls label {{ font-size:12px; color:var(--sub); margin-right:4px; cursor:pointer; }}
#indicator-controls select {{ font-size:12px; padding:2px 4px; }}
</style></head><body>{_nav_html('market', prefix='../')}<div class="wrap">
<header>
  <h1>{name}<span class="muted" style="font-weight:400; font-size:16px;">（{stock_code}）</span></h1>
  <div class="sub"><a href="../market.html">← 回盤勢總覽</a>｜資料時間 {quote.get('date', '')} {quote.get('time', '')}
  （台北）｜產生時間 {generated_at}</div>
</header>

<section>
<div style="display:flex; align-items:baseline; gap:14px; margin-bottom:14px;">
  <div class="n {price_cls}" style="font-size:34px;">{price_text}</div>
  <div class="{price_cls}" style="font-size:16px;">{change_text}</div>
</div>
<div class="summary-cards">{stats_html}</div>
</section>

<section><h2>走勢圖與技術指標</h2>
<div style="margin-bottom:10px;">{tabs_html}</div>
<div id="indicator-controls" style="margin-bottom:10px; display:flex; flex-wrap:wrap; align-items:center; gap:10px;">
  <span class="muted" style="font-size:12px;">均線：</span>
  <label><input type="checkbox" class="ma-toggle" value="5"> MA5</label>
  <label><input type="checkbox" class="ma-toggle" value="10"> MA10</label>
  <label><input type="checkbox" class="ma-toggle" value="20" checked> MA20</label>
  <label><input type="checkbox" class="ma-toggle" value="60"> MA60</label>
  <label><input type="checkbox" class="ma-toggle" value="120"> MA120</label>
  <label><input type="checkbox" class="ma-toggle" value="240"> MA240</label>
  <label><input type="checkbox" id="boll-toggle"> 布林通道</label>
  <span class="muted" style="font-size:12px;">副圖：</span>
  <select id="osc-select">
    <option value="rsi14" selected>RSI</option>
    <option value="macd">MACD</option>
    <option value="kd">KD</option>
    <option value="dmi">DMI/ADX</option>
    <option value="bias20">乖離率 BIAS</option>
    <option value="obv">OBV</option>
    <option value="none">不顯示</option>
  </select>
</div>
<div id="indicator-unavailable" class="muted" style="font-size:11px; margin-bottom:6px; display:none;">
  技術指標僅適用於「近月／三月／六月／一年」分頁（需要日線資料），當日/五日/五年不提供疊圖。
</div>
<div id="detail-chart" style="height:400px;"></div>
</section>

<section><h2>三大法人買賣超</h2>{institutional_html}</section>

<section><h2>估值指標</h2>{valuation_html_}</section>

<section><h2>融資融券餘額</h2>{margin_html_}</section>

<section><h2>月營收</h2>{revenue_html_}</section>

<section><h2>公司基本資料</h2>{company_html_}</section>

<section><h2>重大訊息公告</h2>
<div class="role" style="margin-bottom:8px;">從偵測到這檔個股那天起累積記錄，剛開始追蹤的個股歷史會比較少</div>
{material_info_html_}</section>

<section><h2>產業/基本面深度分析</h2>{pplx_html}</section>

<section><h2>相關貼文</h2>
<table><tr><th>標題</th><th class="num">互動</th><th>情緒</th></tr>
{posts_rows}</table></section>

<section><h2>方法論</h2><div class="muted" style="font-size:12px; line-height:1.7;">
即時行情來自 TWSE 官方公開資料，依規定有揭露延遲，非逐筆真即時；走勢圖來自
Yahoo Finance；技術指標（MA/RSI/MACD/KD/DMI/布林通道/BIAS/OBV）皆為本站依公開
價格資料計算，公式為業界常見版本，非官方揭露數據，僅供參考；三大法人買賣超、
融資融券、本益比殖利率、月營收、公司基本資料、重大訊息公告皆來自 TWSE 官方
公開資料。重大訊息公告的官方端點只提供最新一個交易日的資料、無法查詢歷史，
本站每日排程抓取後累積寫入資料庫，因此個股的公告歷史長度取決於這檔個股從
哪一天開始被本站追蹤，並非該公司完整的歷史公告紀錄。僅供研究參考，非投資建議。
</div></section>
</div>
{chart_script}
</body></html>"""


def run_stock_detail() -> list[Path]:
    market_data = common.read_json(common.RAW_DIR / "market_data.json", default=None)
    detail_data = common.read_json(common.RAW_DIR / "stock_detail.json", default=None)
    if not market_data or not market_data.get("stocks"):
        log.warning("找不到 market_data.json，略過個股詳細頁產出")
        return []

    details = (detail_data or {}).get("stocks", {})
    pages_dir = common.BASE_DIR / "docs" / "stocks"
    pages_dir.mkdir(parents=True, exist_ok=True)

    current_codes = {stock["code"] for stock in market_data["stocks"]}
    out_paths = []
    for stock in market_data["stocks"]:
        code = stock["code"]
        html_text = render_stock_detail_page(code, stock, details.get(code))
        out_path = pages_dir / f"{code}.html"
        out_path.write_text(html_text, encoding="utf-8")
        out_paths.append(out_path)

    # 清掉不再進 Top N 的舊個股頁，避免 docs/stocks/ 一直長出沒人連得到的孤兒頁面
    removed = 0
    for existing in pages_dir.glob("*.html"):
        if existing.stem not in current_codes:
            existing.unlink()
            removed += 1

    log.info("個股詳細頁完成：%d 頁（docs/stocks/），清除 %d 個過期頁面", len(out_paths), removed)
    return out_paths


def run_market() -> Path:
    market_path = common.RAW_DIR / "market_data.json"
    market_data = common.read_json(market_path, default=None)
    if market_data is None:
        raise FileNotFoundError(f"找不到盤勢資料：{market_path}（請先執行 pipeline/stock_detect.py）")

    html_text = render_market_page(market_data)
    pages_dir = common.BASE_DIR / "docs"
    pages_dir.mkdir(parents=True, exist_ok=True)
    out_path = pages_dir / "market.html"
    out_path.write_text(html_text, encoding="utf-8")

    # 給全站導覽列的個股搜尋用：只收目前有產出詳細頁的個股（跟 docs/stocks/ 的檔案一一對應）
    stock_index = [{"code": s["code"], "name": s["name"]} for s in market_data.get("stocks", [])]
    common.write_json(pages_dir / "stock_index.json", stock_index)

    log.info("盤勢頁完成：%s（同步更新個股搜尋索引，%d 檔）", out_path, len(stock_index))
    print(str(out_path))
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="產出社群輿情報告 HTML")
    parser.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD，預設今天")
    parser.add_argument("--weekly", action="store_true", help="產出週報而非日報")
    parser.add_argument("--keywords", action="store_true", help="產出關鍵字總覽頁")
    parser.add_argument("--market", action="store_true", help="產出盤勢總覽頁")
    parser.add_argument("--stock-detail", action="store_true", help="產出個股詳細頁（docs/stocks/*.html）")
    args = parser.parse_args()
    if args.weekly:
        run_weekly(end_date=args.date)
    elif args.keywords:
        run_keywords()
    elif args.market:
        run_market()
    elif args.stock_detail:
        run_stock_detail()
    else:
        run(day=args.date)
