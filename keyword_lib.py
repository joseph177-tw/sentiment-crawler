# -*- coding: utf-8 -*-
"""
共用的關鍵字斷詞邏輯。report/render.py（當日關鍵字雲）與 pipeline/aggregate.py
（累積關鍵字索引頁）都要用同一套斷詞/停用詞規則，拆成單一來源避免兩邊各自維護、
結果對不起來（例如雲上顯示的字跟 keywords.html 裡的錨點對不到同一個 id）。
"""
from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache

import jieba
import jieba.posseg as pseg

import common

jieba.setLogLevel(20)  # 抑制 jieba 初始化時的 INFO log


def _load_finance_dict() -> None:
    """把 keywords.yaml 的公司/產業關鍵字餵給 jieba，避免「永豐金證券」「台積電」
    這類專有名詞被預設字典拆散（例如拆成「永豐」+「金證券」）。明確指定
    tag='nz'（其他專名），確保這些詞一律被詞性過濾判定為名詞，不受
    EXCLUDED_POS_PREFIXES 影響。"""
    keywords = common.load_keywords()
    terms = (
        keywords.get("company", [])
        + keywords.get("competitors", [])
        + keywords.get("industry", [])
    )
    for term in terms:
        jieba.add_word(term, freq=100000, tag="nz")


_load_finance_dict()

STOPWORDS = {
    "的", "是", "在", "了", "與", "及", "和", "也", "就", "都", "而", "或", "被",
    "這", "那", "有", "為", "對", "中", "上", "下", "不", "台股", "新聞",
    "多數", "用戶", "調查", "心得", "分享",
    # PTT/Dcard 常見標題分類標籤或回文前綴，本身不是有意義的關鍵字
    "情報", "閒聊", "討論", "問題", "請益", "公告", "Re",
    # 通用轉述/意見動詞：任何主題的新聞都可能出現「XX認為／表示」，本身
    # 不帶主題資訊，只是敘述句型的一部分
    "認為", "表示", "指出", "強調", "透露", "說明", "呼籲", "提到", "坦言",
    "直言", "提出", "反映", "顯示", "補充", "澄清", "回應", "證實", "否認",
    "提醒", "建議", "詢問",
    # 通用第三方泛稱，不指涉任何具體主題
    "網友", "網民", "鄉民", "民眾",
    # 通用幅度/動作動詞：本身沒有指出「什麼」增加/累積，只是修飾詞，
    # 跟「認為」類問題同性質（例：使用者反映的累積、推出、增加）
    "增加", "減少", "累積", "累計", "推出", "可能", "期間", "上調", "下修",
    "提升", "下滑",
    # 斷詞把「N月合併營收」公告樣板文字裡的「月」跟「合併」黏在一起產生
    # 的假詞（跟「日至」同一類問題），這類月營收公告幾乎每檔上市公司
    # 每月都會發一則，不處理的話這個假詞會一直冒出來
    "月合",
}
# 一律排除的詞性：代詞(r)/副詞(d)/連接詞(c)/介詞(p)/助詞與語氣詞(u*/y)/
# 嘆詞(e)/擬聲詞(o)。這些詞性不管哪個領域都不可能是有意義的主題詞，用
# 詞性過濾一次處理掉，不用每個字個別加進停用詞表。名詞(n*)/動詞(v*)/
# 形容詞(a*)/數量詞(m/q)/地名(ns)/英文(eng) 等一律保留，因為財經用語常見
# 的「成長」「需求」「升息」「強勁」等詞在 jieba 詞性標註裡也會被標成
# 動詞/形容詞，過度排除會連帶砍掉真正有意義的字。
# 用「精確比對」而非字首比對：英文標籤是 'eng'，若用字首 'e' 去比對會
# 誤刪所有英文關鍵字（例如「AI」「ETF」），這裡曾經因為這個字首碰撞
# 把它們整批濾掉，改成明列每個詞性代碼就不會再誤傷。
EXCLUDED_POS_TAGS = {
    "r", "rr", "rg", "ry", "rz",              # 代詞
    "d", "dg",                                 # 副詞
    "c",                                        # 連接詞
    "p",                                        # 介詞
    "u", "ud", "ug", "uj", "ul", "uv", "uz",   # 助詞
    "y",                                        # 語氣詞
    "e",                                        # 嘆詞
    "o",                                        # 擬聲詞
}

# TWSE 重大訊息公告的制式用語（例如面額變更/減資公告的「公告期間：115年
# 08月06日至115年11月05日」）在不同個股間逐字重複出現，會把「期間」「日至」
# 這類無意義片段的次數灌高（「日至」甚至是斷詞把日期尾端的「日」跟連接詞
# 「至」黏在一起的產物，不是真的詞）。斷詞前先把這類日期區間樣板文字整段
# 拿掉，從源頭避免產生這些假詞，比之後再一個個加停用詞更徹底。
_ROC_DATE_RANGE_RE = re.compile(r"\d{2,3}年\d{1,2}月\d{1,2}日至\d{2,3}年\d{1,2}月\d{1,2}日")
_ROC_DATE_RE = re.compile(r"\d{2,3}年\d{1,2}月\d{1,2}日")

# 同樣道理：公司更名公告（「公告本公司名稱由『OO股份有限公司』更名為
# 『XX股份有限公司』」）會在每一檔更名個股的公告文字裡逐字重複出現
# 「股份有限公司」這個純法律組織型態後綴，跟公司實際業務、主題完全無關，
# 卻會被斷詞拆成「股份」「有限」兩個無意義片段、次數隨更名公告數量增加。
_COMPANY_SUFFIX_RE = re.compile(r"股份有限公司|有限公司")

VALID_WORD_RE = re.compile(r"^[一-鿿A-Za-z]{2,}$")


@lru_cache(maxsize=4096)
def _pos_tag_or_none(word: str) -> str | None:
    """回傳 word 在 jieba 詞性標註下的詞性；若 jieba 會把它再拆成多個子詞
    （代表這個詞的詞性不明確、或跟 cut_for_search 判斷的詞界不一致，例如
    「投資人」在 posseg 裡會被拆成「投資」+「人」），保守起見回傳 None、
    不套用詞性過濾，只靠既有的停用詞表跟正規表示式把關，避免誤刪掉
    cut_for_search 原本正確辨識出來的複合詞。

    詞性標註結果只跟 jieba 目前載入的字典有關（程式執行期間不會變），
    同一個詞在累積上千則貼文裡會重複出現非常多次，加 cache 避免對同一個
    詞重複呼叫 pseg.cut()（實測沒 cache 時 1179 則貼文要跑 65 秒，這是
    主要瓶頸）。"""
    parts = list(pseg.cut(word))
    return parts[0].flag if len(parts) == 1 else None


def tokenize(text: str) -> list[str]:
    """回傳文字中通過過濾的關鍵字（可重複，呼叫端視需求自行去重/計數）。"""
    text = _ROC_DATE_RANGE_RE.sub(" ", text)
    text = _ROC_DATE_RE.sub(" ", text)
    text = _COMPANY_SUFFIX_RE.sub(" ", text)
    words = []
    for word in jieba.cut_for_search(text):
        word = word.strip()
        if word in STOPWORDS or not VALID_WORD_RE.match(word):
            continue
        flag = _pos_tag_or_none(word)
        if flag in EXCLUDED_POS_TAGS:
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
