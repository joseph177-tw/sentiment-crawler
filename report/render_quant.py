# -*- coding: utf-8 -*-
"""
量化選股頁：把 docs/quant/picks.json 轉成 docs/quant/index.html。

picks.json 由本機的 quant-factor-ic/daily_picks.py 產生（TWSE 三大法人、外資持股等端點
從 GitHub Actions 的海外 IP 連不上，所以量化資料在本機抓取與計算，只把結果推上來）。
這支腳本只負責排版，沿用網站共用的 BASE_CSS 與導覽列。

用法：
    python report/render_quant.py
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common
from report.render import BASE_CSS, _nav_html

log = common.setup_logging("report.render_quant")

QUANT_DIR = common.BASE_DIR / "docs" / "quant"
STOCK_PAGES = common.BASE_DIR / "docs" / "stocks"

EXTRA_CSS = """
.disclaimer { background:#fff8e1; border:1px solid #f3d27a; border-radius:10px; padding:12px 16px; margin-bottom:18px;
              font-size:13px; line-height:1.7; }
.tabbar { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
.tabbar button { border:1px solid var(--line); background:var(--card); color:var(--ink); border-radius:20px;
                 padding:5px 14px; font-size:13px; cursor:pointer; }
.tabbar button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
.fchips span { display:inline-block; margin:2px 6px 2px 0; padding:2px 10px; border-radius:12px; font-size:12px; }
.fchips .on { background:#e3edf7; color:var(--accent); }
.fchips .off { background:#f0f0f0; color:#999; text-decoration:line-through; }
details.pick { border-bottom:1px solid var(--line); }
details.pick > summary { list-style:none; cursor:pointer; display:grid; align-items:center; gap:10px; padding:10px 4px;
                         grid-template-columns: 54px minmax(130px,1.1fr) 90px 92px 62px minmax(200px,2.4fr) 18px; }
details.pick > summary::-webkit-details-marker { display:none; }
details.pick > summary:hover { background:#f7f9fc; }
details.pick[open] > summary { background:#eef3f9; }
details.pick > summary .arrow { color:var(--sub); transition:transform .15s; }
details.pick[open] > summary .arrow { transform:rotate(90deg); }
.pick-head { display:grid; gap:10px; padding:6px 4px; font-size:12px; color:var(--sub); border-bottom:2px solid var(--line);
             grid-template-columns: 54px minmax(130px,1.1fr) 90px 92px 62px minmax(200px,2.4fr) 18px; }
.rk { font-weight:700; font-size:15px; }
.rk small { display:block; font-weight:400; font-size:11px; }
.nm small { display:block; color:var(--sub); font-size:11px; }
.why { font-size:12px; color:var(--sub); line-height:1.5; }
.pill { display:inline-block; font-size:10px; padding:1px 7px; border-radius:10px; margin-left:4px; vertical-align:middle; }
.pill.hold { background:#e3edf7; color:var(--accent); } .pill.new { background:#fdecea; color:var(--up); }
.detail { display:grid; grid-template-columns: 1.4fr 1fr; gap:18px; padding:12px 6px 18px; }
.detail h3 { font-size:13px; color:var(--accent); margin:10px 0 6px; }
.detail table td, .detail table th { padding:5px 6px; font-size:12px; }
.pbar { display:inline-block; width:70px; height:7px; background:#e9edf2; border-radius:4px; vertical-align:middle; margin-right:6px; position:relative; }
.pbar i { position:absolute; left:0; top:0; height:7px; border-radius:4px; background:var(--accent); }
.unused td { color:#aaa; }
ul.reasons { margin:4px 0; padding-left:18px; font-size:13px; line-height:1.7; }
ul.reasons.caution li { color:#8a5a00; }
.ok { color:var(--down); font-weight:700; } .ng { color:var(--up); font-weight:700; }
@media (max-width:820px) {
  details.pick > summary, .pick-head { grid-template-columns: 44px 1fr 70px 18px; }
  details.pick > summary .c-ind, details.pick > summary .c-score, details.pick > summary .why,
  .pick-head .c-ind, .pick-head .c-score, .pick-head .c-why { display:none; }
  .detail { grid-template-columns: 1fr; }
}
"""


def _e(s) -> str:
    return html.escape(str(s))


def _pct(v, d=1, sign=False) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.{d}f}%" if sign else f"{v * 100:.{d}f}%"


def _cls(v) -> str:
    return "" if v is None else ("up" if v > 0 else "down" if v < 0 else "")


def _stock_link(code: str, name: str) -> str:
    """有社群輿情個股頁的股票，名稱連過去；沒有就純文字。"""
    if (STOCK_PAGES / f"{code}.html").exists():
        return f'<a href="../stocks/{_e(code)}.html" title="看社群輿情與個股頁">{_e(name)}</a>'
    return _e(name)


def _pick_row(p: dict, factors_used: list[str]) -> str:
    ex = p["extra"]
    move = ""
    if p["rank_prev"] is None:
        move = "<small class=up>新進</small>"
    elif p["rank_prev"] != p["rank"]:
        d = p["rank_prev"] - p["rank"]
        move = f'<small class="{"up" if d > 0 else "down"}">{"▲" if d > 0 else "▼"}{abs(d)}</small>'
    pills = ('<span class="pill hold">持股中</span>' if p["in_holdings"] else "") + \
            ('<span class="pill new">今日新進前30</span>' if p["is_new"] else "")
    why = "；".join(p["strengths"][:2]) or "各項因子均衡，沒有特別突出的單項"

    comp_rows = []
    for c in p["components"]:
        pct = c["pct"]
        bar = f'<span class="pbar"><i style="width:{(pct or 0) * 100:.0f}%"></i></span>{_pct(pct, 0)}' if pct is not None else "—"
        used = "納入" if c["used"] else "今日不納入"
        comp_rows.append(f'<tr class="{"" if c["used"] else "unused"}"><td>{_e(c["label"])}'
                         f'<div class="role">{(_e(c["group"]) + "｜") if c["group"] != c["label"] else ""}{_e(c["direction"])}</div></td>'
                         f'<td class="num">{_e(c["value_text"])}</td><td>{bar}</td><td>{used}</td></tr>')
    names = {"foreign_hold": "外資持股比率", "low": "低風險子分數", "foreign_20": "外資 20 日買超", "high_120": "接近 120 日高點"}
    contrib = "".join(f'<tr><td>{names[k]}</td><td class="num {_cls(v)}">{"—" if v is None else f"{v:+.3f}"}</td></tr>'
                      for k, v in p["contrib"].items())
    conds = "".join(f'<tr><td>{_e(c["label"])}</td><td class="num">{_e(c["value"])}</td>'
                    f'<td>{"<span class=ok>✓</span>" if c["ok"] else "<span class=ng>✗</span>"}</td></tr>' for c in p["conditions"])
    strengths = "".join(f"<li>{_e(s)}</li>" for s in p["strengths"]) or "<li class=muted>沒有單項因子進入前 20%，靠各項均衡得分入選</li>"
    cautions = "".join(f"<li>{_e(s)}</li>" for s in p["cautions"]) or "<li class=muted>無特別警示</li>"
    lots = lambda v: "—" if v is None else f"{v:+,.0f} 張"
    extra = (f'<tr><td>外資近 5 日買賣超</td><td class="num {_cls(ex["foreign_5d"])}">{lots(ex["foreign_5d"])}</td></tr>'
             f'<tr><td>投信近 5 日買賣超</td><td class="num {_cls(ex["trust_5d"])}">{lots(ex["trust_5d"])}</td></tr>'
             f'<tr><td>融資 5 日增減</td><td class="num">{lots(ex["margin_chg_5d"])}</td></tr>'
             f'<tr><td>近 20 日報酬（還原）</td><td class="num {_cls(ex["ret_20d"])}">{_pct(ex["ret_20d"], 1, True)}</td></tr>'
             f'<tr><td>20 日均成交金額</td><td class="num">{(ex["avg_value"] or 0) / 1e8:.2f} 億</td></tr>')

    return f"""<details class="pick">
<summary>
  <div class="rk">{p["rank"]}{move}</div>
  <div class="nm">{_stock_link(p["code"], p["name"])}{pills}<small>{_e(p["code"])}</small></div>
  <div class="c-ind muted">{_e(p["industry"])}</div>
  <div class="num">{ex["close"]:.2f}<br><small class="{_cls(ex["ret_1d"])}">{_pct(ex["ret_1d"], 2, True)}</small></div>
  <div class="num c-score">{p["score"]:+.3f}</div>
  <div class="why">{_e(why)}</div>
  <div class="arrow">›</div>
</summary>
<div class="detail">
  <div>
    <h3>因子明細（百分位越高越好；方向已換算）</h3>
    <table><thead><tr><th>因子</th><th class="num">數值</th><th>股票池百分位</th><th>今日</th></tr></thead><tbody>{"".join(comp_rows)}</tbody></table>
    <h3>對綜合分數的貢獻（各項 = 百分位 − 0.5，綜合分數為其平均）</h3>
    <table><tbody>{contrib}<tr><td><b>綜合分數</b>（第 {p["rank"]} 名／{p["of"]} 檔）</td><td class="num"><b>{p["score"]:+.3f}</b></td></tr></tbody></table>
  </div>
  <div>
    <h3>入選理由</h3><ul class="reasons">{strengths}</ul>
    <h3>需注意</h3><ul class="reasons caution">{cautions}</ul>
    <h3>篩選條件</h3><table><tbody>{conds}</tbody></table>
    <h3>補充資訊（不列入分數）</h3><table><tbody>{extra}</tbody></table>
  </div>
</div>
</details>"""


def _model_block(key: str, m: dict, rules: dict, regime: dict, show: bool) -> str:
    all_f = [("foreign_hold", "外資持股比率"), ("low", "低風險（週轉率、波動度、融資餘額）"),
             ("foreign_20", "外資 20 日買超"), ("high_120", "接近 120 日高點")]
    if key == "B3":
        all_f = [f for f in all_f if f[0] != "foreign_20"]
    chips = "".join(f'<span class="{"on" if k in m["factors_used"] else "off"}">{_e(n)}</span>' for k, n in all_f)
    note = ""
    if key == "C60":
        note = (f'今日行情為「{_e(regime["name"])}」（市值加權指數近 60 日 {_pct(regime["trend_60d"], 1, True)}），'
                + ("超過 +5%，多頭因子納入。" if regime["key"] == "up" else "未超過 +5%，只用全天候因子。"))
    rows = "".join(_pick_row(p, m["factors_used"]) for p in m["picks"])
    dropped = "、".join(f'{_e(d["name"])}（{_e(d["code"])}，{d["rank_prev"]} → {d["rank"] or "出榜"}）' for d in m["dropped"]) or "無"
    h = m["holdings"]
    hold_rows = "".join(
        f'<tr><td>{_e(s["code"])}</td><td>{_stock_link(s["code"], s["name"])}</td><td class="num">{s["rank"] or "—"}</td>'
        f'<td>{"續抱" if s["keep"] else "<span class=up>下次換股會換掉</span>"}</td></tr>' for s in h["stocks"])
    return f"""<div class="model" data-model="{key}" {"" if show else "hidden"}>
<section>
  <h2>{_e(m["name"])}</h2>
  <div class="muted" style="margin-bottom:8px">{_e(m["desc"])}</div>
  <div class="fchips">今日使用的因子：{chips}</div>
  <div class="muted" style="margin-top:6px; font-size:12px">{note}</div>
</section>
<section>
  <h2>今日排名前 {len(m["picks"])} 名</h2>
  <div class="muted" style="font-size:12px; margin-bottom:8px">點任一列展開：因子數值與百分位、對分數的貢獻、篩選條件、入選理由與注意事項。股名有連結的可以看社群輿情個股頁。</div>
  <div class="pick-head"><div>排名</div><div>股票</div><div class="c-ind">產業</div><div class="num">收盤／日漲跌</div><div class="num c-score">分數</div><div class="c-why">主要理由</div><div></div></div>
  {rows}
  <div class="muted" style="font-size:12px; margin-top:10px">今日掉出前 {len(m["picks"])} 名：{dropped}</div>
</section>
<section>
  <h2>模型持股（每 {rules["rebal"]} 個交易日換股）</h2>
  <div class="muted" style="font-size:12px; margin-bottom:8px">最近一次換股訊號：{_e(h["signal_date"])}（{h["days_since"]} 個交易日前），
  下次換股約在 <b>{h["next_in"]}</b> 個交易日後。換股時，今日排名仍在前 {rules["buffer_n"]} 名的持股會續抱（緩衝帶），其餘換成最高分的股票。</div>
  <table><thead><tr><th>代號</th><th>名稱</th><th class="num">今日排名</th><th>依緩衝帶規則</th></tr></thead><tbody>{hold_rows}</tbody></table>
</section>
</div>"""


def _backtest_section(bt: dict, live: list) -> str:
    if not bt:
        return ""
    keys = [("AB_C", "主模型 C60"), ("AB_B_nof20", "三因子 B3"), ("EW", "股票池等權"), ("CAP", "市值加權指數（≈0050）")]
    head = "".join(f'<th class="num">{_e(bt[p]["label"])}</th>' for p in ["full", "oos", "is"])
    rows = "".join(
        f'<tr><td>{n}</td>' + "".join(f'<td class="num">{_pct(bt[p][k]["cagr"])}<div class="role">回撤 {_pct(bt[p][k]["mdd"], 0)}</div></td>'
                                      for p in ["full", "oos", "is"]) + "</tr>" for k, n in keys)
    names = {"C60": "主模型 C60", "B3": "三因子 B3", "S": "簡化版 S（已淘汰，對照用）"}
    live_rows = "".join(
        f'<tr><td>{_e(names.get(r["strategy"], r["strategy"]))}</td><td>{_e(r["signal_date"])}</td><td>{_e(r["entry"])} → {_e(r["to"])}</td>'
        f'<td class="num">{r["days"]}</td><td class="num {_cls(r["ret"])}">{"尚未開始" if not r["days"] else _pct(r["ret"], 1, True)}</td>'
        f'<td class="num">{"—" if not r["days"] else _pct(r["ew"], 1, True)}</td><td class="num">{"—" if not r["days"] else _pct(r["cap"], 1, True)}</td></tr>'
        for r in live)
    return f"""<section>
  <h2>回測績效（扣交易成本，年化）</h2>
  <table><thead><tr><th>組合</th>{head}</tr></thead><tbody>{rows}</tbody></table>
  <div class="muted" style="font-size:12px; margin-top:8px">2022/01–2023/06 是當初的樣本外期間；但兩個模型後來是看過全部結果才定案的，<b>所有回測數字都已不算樣本外</b>。</div>
</section>
<section>
  <h2>前瞻追蹤（唯一真正沒看過的資料）</h2>
  <table><thead><tr><th>模型</th><th>名單訊號日</th><th>追蹤區間</th><th class="num">交易日數</th><th class="num">報酬</th><th class="num">同期等權</th><th class="num">同期指數</th></tr></thead>
  <tbody>{live_rows or '<tr><td colspan=7 class=muted>尚無資料</td></tr>'}</tbody></table>
  <div class="muted" style="font-size:12px; margin-top:8px">每週六更新；未扣交易成本。建議累積 2～3 個換股週期（約半年）再判斷模型是否有效。</div>
</section>"""


def render_quant_page(data: dict) -> str:
    regime, rules = data["regime"], data["rules"]
    tabs = "".join(f'<button type="button" data-model="{k}" class="{"on" if i == 0 else ""}">{_e(m["name"])}</button>'
                   for i, (k, m) in enumerate(data["models"].items()))
    blocks = "".join(_model_block(k, m, rules, regime, i == 0) for i, (k, m) in enumerate(data["models"].items()))
    c60 = data["backtest"].get("full", {}).get("AB_C", {})
    reports = [("research/simple_report.html", "簡化版策略與逐步拆解"), ("research/backtest_report.html", "多因子回測（樣本內）"),
               ("research/oos_backtest_report.html", "多因子回測（樣本外）"), ("research/ic_report.html", "因子 IC 分析（樣本內）"),
               ("research/oos_ic_report.html", "因子 IC 分析（樣本外）")]
    links = "".join(f'<a class="tag" href="{h}">{_e(n)}</a>' for h, n in reports if (QUANT_DIR / h).exists())
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>量化選股｜每日模型排名</title>
<style>{BASE_CSS}{EXTRA_CSS}</style></head><body>{_nav_html('quant', prefix='../')}<div class="wrap">
<header><h1>量化選股｜每日模型排名</h1>
<div class="sub">資料日 {_e(data["data_date"])}（TWSE 盤後）｜產生時間 {_e(data["generated_at"])}｜股票池 {data["universe_size"]} 檔上市普通股</div></header>
<div class="disclaimer"><b>這不是投資建議。</b>本頁是依固定規則對歷史資料計算的模型排名，供研究參考，不考慮任何人的財務狀況與風險承受度，
也不保證未來報酬。回測顯示這套模型屬於<b>防禦型</b>：空頭、盤整時較抗跌，但在 2024–2026 權值股主導的多頭中，報酬<b>明顯落後市值加權指數（≈0050）</b>。
所有回測數字都經過反覆比較才定案，存在資料探勘偏誤，請以下方「前瞻追蹤」為準。</div>
<section><div class="summary-cards">
  <div class="card"><div class="n">{_e(regime["name"])}</div><div class="l">今日行情（指數近 60 日 {_pct(regime["trend_60d"], 1, True)}）</div></div>
  <div class="card"><div class="n">{data["models"]["C60"]["holdings"]["next_in"]}</div><div class="l">距下次換股（交易日）</div></div>
  <div class="card"><div class="n">{_pct(c60.get("cagr"))}</div><div class="l">主模型全期回測年化（扣成本）</div></div>
  <div class="card"><div class="n">{_pct(c60.get("mdd"), 0)}</div><div class="l">主模型全期最大回撤</div></div>
</div></section>
<div class="tabbar">{tabs}</div>
{blocks}
{_backtest_section(data["backtest"], data["live"])}
<section><h2>方法與研究報告</h2>
<div style="font-size:13px; line-height:1.8">
每日以 TWSE 盤後資料（收盤行情、三大法人、融資融券、外資持股、除權息）計算因子。每個因子在股票池中排名後換算成 −0.5～+0.5，綜合分數為各因子的平均；
低風險子分數 = 20 日週轉率、20 日波動度、融資餘額／股本三者（越低越好）平均後再排名。股票池：上市普通股，股價 ≥ 10 元、20 日均成交金額 ≥ 2,000 萬、上市滿 60 個交易日，排除創新板。
回測假設 t 日收盤後計算、t+1 日收盤成交，每 {rules["rebal"]} 個交易日換股，持有前 {rules["top_n"]} 名等權，緩衝帶 {rules["buffer_n"]} 名，
交易成本來回約 {_pct(rules["cost_round_trip"], 2)}（手續費 6 折、證交稅、單邊 0.1% 滑價）。
</div>
<div style="margin-top:10px">{links}</div></section>
</div>
<script>
document.querySelectorAll('.tabbar button').forEach(b => b.addEventListener('click', () => {{
  document.querySelectorAll('.tabbar button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.model').forEach(m => m.hidden = m.dataset.model !== b.dataset.model);
}}));
</script>
</body></html>"""


def run() -> Path:
    data = json.loads((QUANT_DIR / "picks.json").read_text(encoding="utf-8"))
    out = QUANT_DIR / "index.html"
    out.write_text(render_quant_page(data), encoding="utf-8")
    log.info("量化選股頁完成：%s（資料日 %s）", out, data["data_date"])
    return out


if __name__ == "__main__":
    run()
