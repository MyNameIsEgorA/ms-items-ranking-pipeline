from __future__ import annotations

import csv
import math
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


TRUE_VALUES = {"1", "true", "yes", "y", "да", "истина", "новинка", "new"}


def normalize_id(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().replace("\ufeff", "").replace("\xa0", "").replace(" ", "")
    if not text:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    if "e" in text.lower():
        try:
            return str(int(Decimal(text.replace(",", "."))))
        except (InvalidOperation, ValueError):
            return text
    return text


def parse_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    text = str(value).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not text:
        return default
    try:
        parsed = float(text)
    except ValueError:
        return default
    if math.isnan(parsed) or math.isinf(parsed):
        return default
    return parsed


def parse_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^0-9a-zа-яё]+", "-", text, flags=re.IGNORECASE)
    return text.strip("-") or "unknown"


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def open_text_with_fallback(path: Path):
    for encoding in ("utf-16", "utf-8-sig", "utf-8", "cp1251"):
        try:
            handle = path.open("r", encoding=encoding, newline="")
            handle.read(4096)
            handle.seek(0)
            return handle
        except UnicodeError:
            continue
    raise UnicodeError(f"Cannot detect text encoding for {path}")


def sniff_csv_dialect(handle) -> csv.Dialect:
    sample = handle.read(32768)
    handle.seek(0)
    try:
        return csv.Sniffer().sniff(sample, delimiters="\t;,|")
    except csv.Error:
        return csv.excel_tab


def is_sale_period(moment: datetime) -> bool:
    month = moment.month
    return month in {1, 2, 3, 5, 7, 8, 10}

