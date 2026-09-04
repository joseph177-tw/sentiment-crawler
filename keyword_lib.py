# -*- coding: utf-8 -*-
"""
共用的關鍵字斷詞邏輯。report/render.py（當日關鍵字雲）與 pipeline/aggregate.py
（累積關鍵字索引頁）都要用同一套斷詞/停用詞規則，拆成單一來源避免兩邊各自維護、
結果對不起來（例如雲上顯示的字跟 keywords.html 裡的錨點對不到同一個 id）。
"""
from __future__ import annotations

import re
from collections import Counter

import jieba

import common

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

STOPWORDS = {
    "的", "是", "在", "了", "與", "及", "和", "也", "就", "都", "而", "或", "被",
    "這", "那", "有", "為", "對", "中", "上", "下", "不", "台股", "新聞",
    "多數", "用戶", "調查", "心得", "分享",
    # PTT/Dcard 常見標題分類標籤或回文前綴，本身不是有意義的關鍵字
    "情報", "閒聊", "討論", "問題", "請益", "公告", "Re",
}
VALID_WORD_RE = re.compile(r"^[一-鿿A-Za-z]{2,}$")


def tokenize(text: str) -> list[str]:
    """回傳文字中通過過濾的關鍵字（可重複，呼叫端視需求自行去重/計數）。"""
    words = []
    for word in jieba.cut_for_search(text):
        word = word.strip()
        if word in STOPWORDS or not VALID_WORD_RE.match(word):
            continue
        words.append(word)
    return words


def keyword_cloud(records: list[dict], top_n: int = 20) -> list[tuple[str, int]]:
    """給一批 {title, summary} 記錄，回傳出現次數最多的關鍵字（用於單日關鍵字雲）。"""
    counter: Counter[str] = Counter()
    for rec in records:
        text = f"{rec.get('title', '')} {rec.get('summary', '')}"
        counter.update(tokenize(text))
    return counter.most_common(top_n)
