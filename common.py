# -*- coding: utf-8 -*-
"""
專案共用工具：設定檔載入、標準化貼文 schema、id 雜湊、logging 設定。
所有 crawlers/pipeline 模組共用，避免重複造輪子（架構文件本身沒列這個檔案，
但 news/ptt/dcard/forum 四支爬蟲都需要同一套 schema 與設定載入邏輯，
拆成單一來源可避免四邊各自實作、格式跑掉）。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import yaml

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "db.sqlite"


def load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_keywords() -> dict:
    with open(CONFIG_DIR / "keywords.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_timezone() -> ZoneInfo:
    return ZoneInfo(load_settings().get("timezone", "Asia/Taipei"))


def setup_logging(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger(name)


def make_id(platform: str, title: str, timestamp: str) -> str:
    """標題 + 時間 hash，作為去重比對用的穩定 id。"""
    raw = f"{platform}|{title.strip()}|{timestamp}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def make_record(
    platform: str,
    board: str,
    title: str,
    content: str,
    author: str,
    timestamp: str,
    url: str,
    push: int = 0,
    boo: int = 0,
) -> dict:
    """標準化 schema，對應架構文件第五節的清理層輸出格式。"""
    rec_id = make_id(platform, title, timestamp)
    return {
        "id": rec_id,
        "platform": platform,
        "board": board,
        "title": title.strip(),
        "content": content.strip(),
        "author": author.strip() if author else "",
        "timestamp": timestamp,
        "url": url,
        "engagement": {"push": push, "boo": boo},
    }


def today_str(tz: Optional[ZoneInfo] = None) -> str:
    tz = tz or get_timezone()
    return datetime.now(tz).strftime("%Y-%m-%d")


def raw_output_path(source: str, day: Optional[str] = None) -> Path:
    """data/raw/<YYYY-MM-DD>/<source>.json"""
    day = day or today_str()
    out_dir = RAW_DIR / day
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{source}.json"


def write_json(path: Path, data: Any) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read_json(path: Path, default: Any = None) -> Any:
    import json
    if not path.exists():
        return default if default is not None else []
    return json.loads(path.read_text(encoding="utf-8"))
