#!/usr/bin/env python3
"""Match Mindbox customer actions to shop_data products.

Mindbox product ids in the provided dumps are stored as `ids.insalesId`, while
the shop_data export stores the same values in the `Штрих-код` column.

The script writes a flat UTF-8 TSV/CSV: one row per action-product relation.
Order events with several lines produce several rows.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


DEFAULT_ACTION_GLOB = "data_dumps/mindbox_filtered_actions_part_*.json"
DEFAULT_SHOP_GLOB = "data_dumps/shop_data*.csv"
DEFAULT_OUTPUT = "data_dumps/mindbox_actions_with_shop_data.csv"
DEFAULT_SHOP_OUTPUT_FIELDS = [
    "ID товара",
    "Название товара или услуги",
    "Название товара в URL",
    "URL",
    "Свойство: Размер",
    "Свойство: Цвет",
    "ID варианта",
    "Артикул",
    "Штрих-код",
    "Внешний ID",
    "Цена продажи",
    "Старая цена",
    "Остаток",
    "Параметр: Бренд",
    "Параметр: Пол",
    "Параметр: Тип",
    "Параметр: Сезон",
    "Параметр: Тип2",
    "Параметр: Тип3",
]


BASE_FIELDS = [
    "source_file",
    "event_mindbox_id",
    "event_datetime_utc",
    "event_creation_datetime_utc",
    "action_template_system_name",
    "action_template_name",
    "customer_mindbox_id",
    "channel_external_id",
    "channel_system_name",
    "channel_name",
    "relation_source",
    "mindbox_product_insales_id",
    "mindbox_product_name",
    "order_mindbox_id",
    "order_shopify_kixbox_id",
    "order_line_id",
    "order_line_number",
    "order_line_quantity",
    "order_line_base_price_per_item",
    "order_line_price",
    "match_status",
    "matched_shop_rows",
]


def normalize_id(value: Any) -> str:
    """Normalize ids exported as plain strings or Excel-like scientific values."""
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    text = text.replace("\ufeff", "").replace(" ", "").replace("\xa0", "")
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]

    if "e" in text.lower():
        try:
            return str(int(Decimal(text.replace(",", "."))))
        except (InvalidOperation, ValueError):
            return text

    return text


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


def sniff_dialect(handle) -> csv.Dialect:
    sample = handle.read(32768)
    handle.seek(0)
    try:
        return csv.Sniffer().sniff(sample, delimiters="\t;,|")
    except csv.Error:
        return csv.excel_tab


def find_single_file(pattern: str, label: str) -> Path:
    files = sorted(Path().glob(pattern))
    if not files:
        raise FileNotFoundError(f"No {label} files found by pattern: {pattern}")
    if len(files) > 1:
        names = "\n".join(f"  - {path}" for path in files)
        raise ValueError(
            f"Pattern for {label} matched multiple files. Pass the exact path.\n{names}"
        )
    return files[0]


def build_shop_index(shop_path: Path, join_column: str) -> tuple[dict[str, list[dict[str, str]]], list[str], Counter]:
    index: dict[str, list[dict[str, str]]] = {}
    stats: Counter = Counter()

    with open_text_with_fallback(shop_path) as handle:
        dialect = sniff_dialect(handle)
        reader = csv.DictReader(handle, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError(f"Shop file has no header: {shop_path}")
        if join_column not in reader.fieldnames:
            raise ValueError(
                f"Column {join_column!r} not found in {shop_path}. "
                f"Available columns: {', '.join(reader.fieldnames)}"
            )

        fieldnames = list(reader.fieldnames)
        for row in reader:
            stats["shop_rows"] += 1
            key = normalize_id(row.get(join_column))
            if not key:
                stats["shop_rows_without_join_id"] += 1
                continue
            index.setdefault(key, []).append(row)

    stats["shop_unique_join_ids"] = len(index)
    stats["shop_duplicate_join_ids"] = sum(1 for rows in index.values() if len(rows) > 1)
    return index, fieldnames, stats


def iter_action_products(action: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for product in action.get("products") or []:
        product_ids = product.get("ids") or {}
        yield {
            "relation_source": "products",
            "mindbox_product_insales_id": normalize_id(product_ids.get("insalesId")),
            "mindbox_product_name": product.get("name", ""),
        }

    order = action.get("order") or {}
    for line in order.get("lines") or []:
        product = line.get("product") or {}
        product_ids = product.get("ids") or {}
        yield {
            "relation_source": "order.lines",
            "mindbox_product_insales_id": normalize_id(product_ids.get("insalesId")),
            "mindbox_product_name": product.get("name", ""),
            "order_line_id": line.get("id", ""),
            "order_line_number": line.get("number", ""),
            "order_line_quantity": line.get("quantity", ""),
            "order_line_base_price_per_item": line.get("basePricePerItem", ""),
            "order_line_price": line.get("priceOfLine", ""),
        }


def base_action_row(action: dict[str, Any], source_file: Path) -> dict[str, Any]:
    action_template = action.get("actionTemplate") or {}
    action_template_ids = action_template.get("ids") or {}
    customer_ids = (action.get("customer") or {}).get("ids") or {}
    channel = action.get("channel") or {}
    channel_ids = channel.get("ids") or {}
    order = action.get("order") or {}
    order_ids = order.get("ids") or {}

    return {
        "source_file": str(source_file),
        "event_mindbox_id": ((action.get("ids") or {}).get("mindboxId", "")),
        "event_datetime_utc": action.get("dateTimeUtc", ""),
        "event_creation_datetime_utc": action.get("creationDateTimeUtc", ""),
        "action_template_system_name": action_template_ids.get("systemName", ""),
        "action_template_name": action_template.get("name", ""),
        "customer_mindbox_id": customer_ids.get("mindboxId", ""),
        "channel_external_id": channel_ids.get("externalId", ""),
        "channel_system_name": channel_ids.get("systemName", ""),
        "channel_name": channel.get("name", ""),
        "order_mindbox_id": order_ids.get("mindboxId", ""),
        "order_shopify_kixbox_id": order_ids.get("shopifyKixboxId", ""),
    }


def load_actions(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    actions = payload.get("customerActions")
    if not isinstance(actions, list):
        raise ValueError(f"File does not contain customerActions list: {path}")
    return actions


def write_matches(
    action_paths: list[Path],
    shop_index: dict[str, list[dict[str, str]]],
    output_shop_fields: list[str],
    output_path: Path,
    output_delimiter: str,
    include_events_without_products: bool,
) -> Counter:
    stats: Counter = Counter()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = BASE_FIELDS + [f"shop__{field}" for field in output_shop_fields]

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=output_delimiter)
        writer.writeheader()

        for action_path in action_paths:
            actions = load_actions(action_path)
            stats["action_files"] += 1
            stats["actions_total"] += len(actions)

            for action in actions:
                base_row = base_action_row(action, action_path)
                products = list(iter_action_products(action))

                if not products:
                    stats["actions_without_products"] += 1
                    if include_events_without_products:
                        row = {
                            **base_row,
                            "match_status": "no_product_in_event",
                            "matched_shop_rows": 0,
                        }
                        writer.writerow(row)
                        stats["rows_written"] += 1
                    continue

                stats["actions_with_products"] += 1
                for product_row in products:
                    stats["product_relations_total"] += 1
                    product_id = product_row.get("mindbox_product_insales_id", "")
                    matches = shop_index.get(product_id) if product_id else None

                    if not matches:
                        row = {
                            **base_row,
                            **product_row,
                            "match_status": "unmatched",
                            "matched_shop_rows": 0,
                        }
                        writer.writerow(row)
                        stats["product_relations_unmatched"] += 1
                        stats["rows_written"] += 1
                        continue

                    stats["product_relations_matched"] += 1
                    for shop_row in matches:
                        row = {
                            **base_row,
                            **product_row,
                            "match_status": "matched",
                            "matched_shop_rows": len(matches),
                        }
                        row.update(
                            {
                                f"shop__{field}": shop_row.get(field, "")
                                for field in output_shop_fields
                            }
                        )
                        writer.writerow(row)
                        stats["rows_written"] += 1

    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Match Mindbox action product ids to shop_data rows by barcode."
    )
    parser.add_argument(
        "--actions",
        nargs="+",
        type=Path,
        help=f"Mindbox JSON files. Default: all files matching {DEFAULT_ACTION_GLOB}",
    )
    parser.add_argument(
        "--shop-data",
        type=Path,
        help=f"shop_data CSV file. Default: the single file matching {DEFAULT_SHOP_GLOB}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(DEFAULT_OUTPUT),
        help=f"Output TSV/CSV path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--join-column",
        default="Штрих-код",
        help="shop_data column used for matching. Default: Штрих-код",
    )
    parser.add_argument(
        "--delimiter",
        default="\t",
        help=r"Output delimiter. Default: tab. Use ';' for semicolon CSV.",
    )
    parser.add_argument(
        "--include-events-without-products",
        action="store_true",
        help="Also write category/other events that do not contain product ids.",
    )
    parser.add_argument(
        "--all-shop-fields",
        action="store_true",
        help="Write every shop_data column. By default only compact product fields are written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    action_paths = args.actions or sorted(Path().glob(DEFAULT_ACTION_GLOB))
    if not action_paths:
        raise FileNotFoundError(f"No action files found by pattern: {DEFAULT_ACTION_GLOB}")

    shop_path = args.shop_data or find_single_file(DEFAULT_SHOP_GLOB, "shop_data")
    shop_index, shop_fields, shop_stats = build_shop_index(shop_path, args.join_column)
    output_shop_fields = (
        shop_fields
        if args.all_shop_fields
        else [field for field in DEFAULT_SHOP_OUTPUT_FIELDS if field in shop_fields]
    )
    match_stats = write_matches(
        action_paths=action_paths,
        shop_index=shop_index,
        output_shop_fields=output_shop_fields,
        output_path=args.output,
        output_delimiter=args.delimiter,
        include_events_without_products=args.include_events_without_products,
    )

    print(f"shop_data: {shop_path}")
    print(f"action files: {len(action_paths)}")
    print(f"output: {args.output}")
    for key, value in (shop_stats + match_stats).most_common():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
