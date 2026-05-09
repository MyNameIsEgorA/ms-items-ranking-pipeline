from __future__ import annotations

import csv
import json
import logging
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.normalization import (
    clean_text,
    normalize_id,
    open_text_with_fallback,
    parse_bool,
    parse_datetime,
    parse_float,
    slugify,
    sniff_csv_dialect,
)
from app.schemas import ProductDocument


logger = logging.getLogger(__name__)

VIEW_ACTIONS = {
    "ProsmotrProdukta",
    "ProsmotrKategoriiProduktovVOperacii",
}
PURCHASE_ACTIONS = {
    "SoxranenieZakazaVOperaciiWebsiteCreateOrder",
    "SoxranenieZakazaVOperaciiNewOfflineCreateAuthorizedOrder",
}


@dataclass
class Variant:
    variant_id: str = ""
    barcode: str = ""
    sku: str = ""
    size: str = ""
    color: str = ""
    price: float = 0.0
    old_price: float = 0.0
    stock: float = 0.0
    image_url: str = ""


@dataclass
class ProductAggregate:
    product_id: str
    title: str = ""
    url: str = ""
    brand: str = ""
    gender: str = ""
    product_type: str = ""
    season: str = ""
    is_new_raw: bool = False
    categories: set[str] = field(default_factory=set)
    variants: list[Variant] = field(default_factory=list)
    image_url: str = ""

    def add_variant(self, row: dict[str, str]) -> None:
        self.title = self.title or clean_text(row.get("Название товара или услуги"))
        self.url = self.url or clean_text(row.get("URL"))
        self.brand = self.brand or clean_text(row.get("Параметр: Бренд"))
        self.gender = self.gender or normalize_gender(row.get("Параметр: Пол"))
        self.product_type = self.product_type or clean_text(
            row.get("Параметр: Тип") or row.get("Параметр: Тип2") or row.get("Параметр: Тип3")
        )
        self.season = self.season or clean_text(row.get("Параметр: Сезон"))
        self.is_new_raw = self.is_new_raw or parse_bool(row.get("Параметр: новинка"))
        self.image_url = self.image_url or first_image(row.get("Изображения")) or first_image(
            row.get("Изображения варианта")
        )
        self.categories.update(parse_categories(row.get("Размещение на сайте")))
        self.variants.append(
            Variant(
                variant_id=normalize_id(row.get("ID варианта")),
                barcode=normalize_id(row.get("Штрих-код") or row.get("Внешний ID")),
                sku=clean_text(row.get("Артикул")),
                size=clean_text(row.get("Свойство: Размер")),
                color=clean_text(row.get("Свойство: Цвет")),
                price=parse_float(row.get("Цена продажи")),
                old_price=parse_float(row.get("Старая цена")),
                stock=parse_float(row.get("Остаток")),
                image_url=first_image(row.get("Изображения варианта")) or first_image(row.get("Изображения")),
            )
        )


@dataclass
class ProductEventAggregate:
    views: int = 0
    purchases: int = 0
    first_seen_at: datetime | None = None

    def register_seen(self, event_time: datetime, age_days: float) -> None:
        if self.first_seen_at is None or event_time < self.first_seen_at:
            self.first_seen_at = event_time


def normalize_gender(value: Any) -> str:
    text = clean_text(value).lower()
    if not text:
        return ""
    if "муж" in text or text in {"men", "male"}:
        return "men"
    if "жен" in text or text in {"women", "female"}:
        return "women"
    if "уни" in text or "unisex" in text:
        return "unisex"
    return text


def first_image(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    return text.split()[0]


def parse_categories(value: Any) -> set[str]:
    text = clean_text(value)
    if not text:
        return set()
    categories: set[str] = set()
    for raw_path in text.split("##"):
        parts = [clean_text(part) for part in raw_path.split("/") if clean_text(part)]
        for part in parts:
            if part.lower() != "каталог":
                categories.add(part)
    return categories


def choose_category(categories: set[str], product_type: str) -> str:
    if product_type:
        return product_type
    if not categories:
        return ""
    priority_words = ("куртк", "обув", "футбол", "брюк", "аксессуар", "толстов")
    for word in priority_words:
        for category in sorted(categories):
            if word in category.lower():
                return category
    return sorted(categories)[0]


def parse_discount(price: float, old_price: float) -> float:
    if price <= 0 or old_price <= price:
        return 0.0
    return round((old_price - price) / old_price, 4)


def catalog_created_at_from_season(season_code: str, fallback_tz: timezone | None) -> datetime | None:
    match = re.fullmatch(r"(SS|AW|HO|FW|SU)-(\d{2})", clean_text(season_code).upper())
    if not match:
        return None
    season, year_suffix = match.groups()
    month_map = {"SS": 3, "SU": 6, "AW": 8, "FW": 9, "HO": 10}
    return datetime(2000 + int(year_suffix), month_map.get(season, 6), 1, tzinfo=fallback_tz)


def calculate_catalog_age_days(product: ProductAggregate, period_end: datetime) -> tuple[float, datetime]:
    created_at = catalog_created_at_from_season(product.season, period_end.tzinfo)
    if created_at is None:
        return 180.0, period_end
    return max((period_end - created_at).total_seconds() / 86400, 0.0), created_at


def find_files(data_dir: Path, pattern: str) -> list[Path]:
    return sorted(data_dir.glob(pattern))


def normalize_period(start_date: datetime, end_date: datetime) -> tuple[datetime, datetime]:
    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    else:
        start_date = start_date.astimezone(timezone.utc)

    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    else:
        end_date = end_date.astimezone(timezone.utc)

    if start_date > end_date:
        raise ValueError("start_date must be less than or equal to end_date")
    return start_date, end_date


class ProductRankingPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings

    def build_documents(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[list[ProductDocument], dict[str, Any]]:
        stats: Counter = Counter()
        action_files = self._find_user_event_files()
        logger.info("Index build started: user event files=%s", len(action_files))
        period_start, period_end, period_stats = self._resolve_event_period(action_files, start_date, end_date)
        stats.update(period_stats)
        logger.info("Event period resolved: start=%s end=%s auto=%s", period_start, period_end, bool(period_stats["period_auto_detected"]))
        products = self._load_shop_products(stats)
        logger.info("Shop products loaded: products=%s rows=%s", len(products), stats["shop_rows"])
        categories = self._load_categories(stats)
        logger.info("Categories loaded: ids=%s rows=%s", len(categories), stats["category_rows"])
        self._enrich_from_website(products, stats)
        logger.info("Website enrichment finished: rows=%s matched=%s", stats["website_rows"], stats["website_rows_matched"])
        self._enrich_from_mindbox_catalog(products, categories, stats)
        logger.info(
            "Mindbox catalog enrichment finished: rows=%s matched=%s",
            stats["mindbox_catalog_rows"],
            stats["mindbox_catalog_rows_matched"],
        )

        barcode_to_product_id = {
            variant.barcode: product.product_id
            for product in products.values()
            for variant in product.variants
            if variant.barcode
        }
        product_events, event_stats = self._aggregate_events(
            action_files,
            period_start,
            period_end,
            barcode_to_product_id,
        )
        stats.update(event_stats)

        total_purchases = sum(metrics.purchases for metrics in product_events.values())
        documents = [
            self._build_document(product, product_events[product.product_id], total_purchases, period_end)
            for product in products.values()
            if product.product_id in product_events
        ]
        documents.sort(key=lambda item: item.final_score, reverse=True)
        stats["documents"] = len(documents)
        stats["products_loaded"] = len(products)
        stats["products_with_events_in_period"] = len(product_events)
        plot_paths = self._build_score_plots(documents)
        stats["score_plot_files"] = plot_paths
        logger.info("Index build finished: documents=%s score_plots=%s", len(documents), len(plot_paths))
        return documents, {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            **dict(stats),
        }

    def _load_shop_products(self, stats: Counter) -> dict[str, ProductAggregate]:
        candidates = find_files(self.settings.data_dir, "shop_data*.csv")
        if not candidates:
            raise FileNotFoundError(f"No shop_data*.csv files found in {self.settings.data_dir}")
        shop_path = candidates[0]
        products: dict[str, ProductAggregate] = {}

        with open_text_with_fallback(shop_path) as handle:
            dialect = sniff_csv_dialect(handle)
            reader = csv.DictReader(handle, dialect=dialect)
            for row in reader:
                stats["shop_rows"] += 1
                product_id = normalize_id(row.get("ID товара"))
                if not product_id:
                    stats["shop_rows_without_product_id"] += 1
                    continue
                products.setdefault(product_id, ProductAggregate(product_id=product_id)).add_variant(row)

        stats["shop_files"] = 1
        return products

    def _load_categories(self, stats: Counter) -> dict[str, str]:
        candidates = find_files(self.settings.data_dir, "categories.csv")
        if not candidates:
            return {}

        category_map: dict[str, str] = {}
        with candidates[0].open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            for row in reader:
                stats["category_rows"] += 1
                name = clean_text(row.get("CategoryName"))
                if not name:
                    continue
                for field_name, value in row.items():
                    if field_name.startswith("CategoryIds"):
                        category_id = normalize_id(value)
                        if category_id:
                            category_map[category_id] = name
        stats["category_ids_loaded"] = len(category_map)
        return category_map

    def _enrich_from_website(self, products: dict[str, ProductAggregate], stats: Counter) -> None:
        candidates = find_files(self.settings.data_dir, "products_website.csv")
        if not candidates:
            return

        barcode_to_product = {
            variant.barcode: product
            for product in products.values()
            for variant in product.variants
            if variant.barcode
        }

        with candidates[0].open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                stats["website_rows"] += 1
                product = barcode_to_product.get(normalize_id(row.get("Variant Barcode")))
                if product is None:
                    continue
                product.brand = product.brand or clean_text(row.get("Vendor"))
                product.gender = product.gender or normalize_gender(
                    row.get("gender (product.metafields.custom.gender)")
                    or row.get("gender_list (product.metafields.custom.gender_list)")
                )
                product.product_type = product.product_type or clean_text(
                    row.get("Type") or row.get("type1 (product.metafields.custom.type1)")
                )
                product.image_url = product.image_url or clean_text(row.get("Image Src"))
                product.is_new_raw = product.is_new_raw or ("new" in clean_text(row.get("Tags")).lower())
                stats["website_rows_matched"] += 1

    def _enrich_from_mindbox_catalog(
        self,
        products: dict[str, ProductAggregate],
        categories: dict[str, str],
        stats: Counter,
    ) -> None:
        candidates = find_files(self.settings.data_dir, "products-mindbox.csv")
        if not candidates:
            return

        barcode_to_product = {
            variant.barcode: product
            for product in products.values()
            for variant in product.variants
            if variant.barcode
        }

        with candidates[0].open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            for row in reader:
                stats["mindbox_catalog_rows"] += 1
                ids = [
                    normalize_id(row.get("ProductIdsInsalesId")),
                    normalize_id(row.get("ProductIdsKixboxShopifyId")),
                    normalize_id(row.get("ProductIdsKixboxWebsiteId")),
                ]
                product = next((barcode_to_product[item] for item in ids if item in barcode_to_product), None)
                if product is None:
                    continue
                product.title = product.title or clean_text(row.get("ProductName"))
                product.url = product.url or clean_text(row.get("ProductUrl"))
                product.brand = product.brand or clean_text(row.get("ProductVendorName"))
                product.gender = product.gender or normalize_gender(row.get("ProductCustomFieldsSex"))
                product.product_type = product.product_type or clean_text(row.get("ProductCustomFieldsProductType"))
                product.season = product.season or clean_text(row.get("ProductCustomFieldsSeason"))
                product.image_url = product.image_url or clean_text(row.get("ProductPictureUrl"))
                for field_name, value in row.items():
                    if field_name.startswith("ProductCategoriesIds"):
                        category_name = categories.get(normalize_id(value))
                        if category_name:
                            product.categories.add(category_name)
                stats["mindbox_catalog_rows_matched"] += 1

    def _aggregate_events(
        self,
        action_files: list[Path],
        period_start: datetime,
        period_end: datetime,
        barcode_to_product_id: dict[str, str],
    ) -> tuple[dict[str, ProductEventAggregate], Counter]:
        stats: Counter = Counter()
        metrics: dict[str, ProductEventAggregate] = defaultdict(ProductEventAggregate)
        total_files = len(action_files)

        for index, path in enumerate(action_files, start=1):
            logger.info("Processing user event file %s/%s: %s", index, total_files, path.name)
            with path.open("r", encoding="utf-8") as handle:
                actions = json.load(handle).get("customerActions", [])
            stats["action_files"] += 1
            stats["actions_total"] += len(actions)

            for action in actions:
                action_time = parse_datetime(action.get("dateTimeUtc"))
                if action_time is None:
                    stats["actions_without_datetime"] += 1
                    continue

                if action_time < period_start:
                    stats["actions_before_period"] += 1
                    continue
                if action_time > period_end:
                    stats["actions_after_period"] += 1
                    continue

                template = ((action.get("actionTemplate") or {}).get("ids") or {}).get("systemName", "")
                if template not in PURCHASE_ACTIONS and template not in VIEW_ACTIONS:
                    stats["actions_with_unsupported_template"] += 1
                    continue

                product_ids = iter_action_product_ids(action)
                if not product_ids:
                    stats["actions_without_products"] += 1
                    continue

                age_days = max((period_end - action_time).total_seconds() / 86400, 0.0)
                if age_days > self.settings.popularity_window_days:
                    stats["actions_before_popularity_window"] += 1
                    continue

                for event_product_id in product_ids:
                    stats["event_products_total"] += 1
                    product_id = barcode_to_product_id.get(event_product_id)
                    if product_id is None:
                        stats["event_products_not_in_shop_data"] += 1
                        continue
                    stats["event_products_matched"] += 1
                    target = metrics[product_id]
                    target.register_seen(action_time, age_days)
                    if template in PURCHASE_ACTIONS:
                        target.purchases += 1
                    else:
                        target.views += 1

            logger.info(
                "Processed user event file %s/%s: actions_total=%s matched_event_products=%s indexed_product_candidates=%s files_left=%s",
                index,
                total_files,
                stats["actions_total"],
                stats["event_products_matched"],
                len(metrics),
                total_files - index,
            )

        stats["event_product_ids"] = len(metrics)
        for key in (
            "actions_before_period",
            "actions_after_period",
            "actions_without_datetime",
            "actions_without_products",
            "actions_before_popularity_window",
            "actions_with_unsupported_template",
            "event_products_total",
            "event_products_matched",
            "event_products_not_in_shop_data",
        ):
            stats.setdefault(key, 0)
        return metrics, stats

    def _resolve_event_period(
        self,
        action_files: list[Path],
        start_date: datetime | None,
        end_date: datetime | None,
    ) -> tuple[datetime, datetime, Counter]:
        if start_date is not None and end_date is not None:
            period_start, period_end = normalize_period(start_date, end_date)
            return period_start, period_end, Counter({"period_auto_detected": 0})

        if start_date is not None or end_date is not None:
            raise ValueError("start_date and end_date must be provided together")

        cached_period = self._load_cached_event_period(action_files)
        if cached_period is not None:
            period_start, period_end = cached_period
            logger.info("Event period loaded from cache: start=%s end=%s", period_start, period_end)
            return period_start, period_end, Counter({"period_auto_detected": 1, "period_cache_hit": 1})

        stats: Counter = Counter({"period_auto_detected": 1})
        period_start: datetime | None = None
        period_end: datetime | None = None
        total_files = len(action_files)
        for index, path in enumerate(action_files, start=1):
            logger.info("Scanning event period from file %s/%s: %s", index, total_files, path.name)
            with path.open("r", encoding="utf-8") as handle:
                actions = json.load(handle).get("customerActions", [])
            for action in actions:
                action_time = parse_datetime(action.get("dateTimeUtc"))
                if action_time is None:
                    stats["period_actions_without_datetime"] += 1
                    continue
                period_start = min(period_start or action_time, action_time)
                period_end = max(period_end or action_time, action_time)
            logger.info(
                "Scanned event period file %s/%s: current_start=%s current_end=%s files_left=%s",
                index,
                total_files,
                period_start,
                period_end,
                total_files - index,
            )

        if period_start is None or period_end is None:
            raise ValueError(f"No dated events found in {self.settings.user_events_dir}")

        stats["period_cache_hit"] = 0
        self._save_event_period_cache(action_files, period_start, period_end)
        return period_start, period_end, stats

    def _load_cached_event_period(self, action_files: list[Path]) -> tuple[datetime, datetime] | None:
        cache_path = self.settings.event_period_cache_path
        if not cache_path.exists():
            return None
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if cache.get("files") != self._event_file_signatures(action_files):
            return None

        period_start = parse_datetime(cache.get("period_start"))
        period_end = parse_datetime(cache.get("period_end"))
        if period_start is None or period_end is None:
            return None
        return period_start, period_end

    def _save_event_period_cache(
        self,
        action_files: list[Path],
        period_start: datetime,
        period_end: datetime,
    ) -> None:
        cache_path = self.settings.event_period_cache_path
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "files": self._event_file_signatures(action_files),
        }
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _event_file_signatures(self, action_files: list[Path]) -> list[dict[str, int | str]]:
        return [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            }
            for path in action_files
        ]

    def _find_user_event_files(self) -> list[Path]:
        action_files = find_files(self.settings.user_events_dir, "mindbox_filtered_actions_part_*.json")
        if not action_files:
            raise FileNotFoundError(
                f"No mindbox_filtered_actions_part_*.json files found in {self.settings.user_events_dir}"
            )

        expected_total: int | None = None
        present_parts: set[int] = set()
        pattern = re.compile(r"mindbox_filtered_actions_part_(\d+)_of_(\d+)\.json$")
        for path in action_files:
            match = pattern.match(path.name)
            if match is None:
                continue
            part = int(match.group(1))
            total = int(match.group(2))
            present_parts.add(part)
            if expected_total is None:
                expected_total = total
            elif expected_total != total:
                raise FileNotFoundError(
                    f"Inconsistent user event file totals in {self.settings.user_events_dir}: "
                    f"expected {expected_total}, got {total} in {path.name}"
                )

        if expected_total is not None:
            missing_parts = sorted(set(range(1, expected_total + 1)) - present_parts)
            if missing_parts:
                missing = ", ".join(f"{part:02d}" for part in missing_parts)
                raise FileNotFoundError(
                    f"Missing user event files in {self.settings.user_events_dir}: parts {missing} of {expected_total}"
                )

        logger.info("User event files verified: found=%s expected=%s", len(action_files), expected_total or "unknown")
        return action_files

    def _build_score_plots(
        self,
        documents: list[ProductDocument],
    ) -> list[str]:
        if not documents:
            logger.info("Score plots skipped: no documents")
            return []

        os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        output_dir = self.settings.score_plots_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        score_values = [document.final_score for document in documents]
        popularity_values = [document.popularity for document in documents]
        novelty_values = [document.novelty for document in documents]

        saved_paths = [
            self._save_histogram(
                plt,
                score_values,
                "Final score distribution",
                "final_score",
                output_dir / "final_score_distribution.png",
            ),
            self._save_histogram(
                plt,
                popularity_values,
                "Popularity distribution",
                "popularity",
                output_dir / "popularity_distribution.png",
            ),
            self._save_histogram(
                plt,
                novelty_values,
                "Novelty distribution",
                "novelty",
                output_dir / "novelty_distribution.png",
            ),
        ]

        logger.info("Score plots saved: %s", ", ".join(str(path) for path in saved_paths))
        return [str(path) for path in saved_paths]

    def _save_histogram(self, plt: Any, values: list[float], title: str, xlabel: str, path: Path) -> str:
        plt.figure(figsize=(10, 6))
        plt.hist(values, bins=50, color="#3B82F6", edgecolor="#1F2937", alpha=0.85)
        plt.title(title)
        plt.xlabel(xlabel)
        plt.ylabel("products")
        plt.grid(axis="y", alpha=0.25)
        plt.tight_layout()
        plt.savefig(path, dpi=140)
        plt.close()
        return str(path)

    def _decay(self, age_days: float) -> float:
        decay_lambda = math.log(2) / max(self.settings.popularity_half_life_days, 1)
        return math.exp(-decay_lambda * age_days)

    def _category_boost(self, category_name: str) -> float:
        return 1.0

    def _build_document(
        self,
        product: ProductAggregate,
        metrics: ProductEventAggregate,
        total_purchases: int,
        period_end: datetime,
    ) -> ProductDocument:
        barcodes = sorted({variant.barcode for variant in product.variants if variant.barcode})
        variant_ids = sorted({variant.variant_id for variant in product.variants if variant.variant_id})
        prices = [variant.price for variant in product.variants if variant.price > 0]
        old_prices = [variant.old_price for variant in product.variants if variant.old_price > 0]
        price = min(prices) if prices else 0.0
        old_price = max(old_prices) if old_prices else 0.0
        stock = sum(max(variant.stock, 0.0) for variant in product.variants)
        in_stock = stock > 0
        category_name = choose_category(product.categories, product.product_type)

        if metrics.first_seen_at is None:
            raise ValueError(f"Product {product.product_id} has no event mention date")

        age_days, created_at = calculate_catalog_age_days(product, period_end)
        purchases = int(metrics.purchases)
        popularity = (
            metrics.views * self.settings.popularity_view_weight
            + purchases * self.settings.popularity_purchase_weight
        ) * self._decay(age_days)
        novelty = -math.log2((purchases + 1) / (total_purchases + 1)) if total_purchases > 0 else 0.0
        discount = parse_discount(price, old_price)
        is_sale = discount > 0 or any("распрод" in item.lower() or "sale" in item.lower() for item in product.categories)
        is_new = product.is_new_raw or any("новин" in item.lower() for item in product.categories)
        page_keys = resolve_page_keys(is_new, is_sale, product.gender, category_name, product.categories, product.product_type)

        stock_boost = self.settings.boost_in_stock if in_stock else self.settings.boost_out_of_stock
        featured_boost = 1.0
        sale_boost = self.settings.boost_sale if is_sale else 1.0
        category_boost = self._category_boost(category_name)
        boost = stock_boost * featured_boost * sale_boost * category_boost

        final_score = math.log1p(popularity) * (novelty / 14) * boost
        final_score_1 = final_score
        final_score_2 = final_score
        final_score_3 = final_score

        return ProductDocument(
            product_id=product.product_id,
            title=product.title,
            category_id=slugify(category_name) if category_name else "",
            category_name=category_name,
            categories=sorted(product.categories),
            brand=product.brand,
            price=round(price, 2),
            old_price=round(old_price, 2),
            discount=discount,
            in_stock=in_stock,
            stock=stock,
            is_new=is_new,
            is_sale=is_sale,
            page_keys=page_keys,
            gender=product.gender,
            product_type=product.product_type,
            season=product.season,
            popularity=round(popularity, 6),
            novelty=round(novelty, 6),
            final_score=round(final_score, 6),
            final_score_1=round(final_score_1, 6),
            final_score_2=round(final_score_2, 6),
            final_score_3=round(final_score_3, 6),
            age_days=round(age_days, 6),
            created_at=created_at,
            url=product.url,
            image_url=product.image_url,
            barcodes=barcodes,
            variant_ids=variant_ids,
        )


def iter_action_product_ids(action: dict[str, Any]) -> list[str]:
    product_ids: list[str] = []
    for product in action.get("products") or []:
        product_id = normalize_id((product.get("ids") or {}).get("insalesId"))
        if product_id:
            product_ids.append(product_id)
    for line in ((action.get("order") or {}).get("lines") or []):
        product_id = normalize_id((((line.get("product") or {}).get("ids") or {}).get("insalesId")))
        if product_id:
            product_ids.append(product_id)
    return product_ids


def resolve_page_keys(
    is_new: bool,
    is_sale: bool,
    gender: str,
    category_name: str,
    categories: set[str],
    product_type: str,
) -> list[str]:
    keys: set[str] = set()
    if is_new:
        keys.add("new")
    if is_sale:
        keys.add("sale")
    if gender in {"men", "unisex"} or any("муж" in item.lower() for item in categories):
        keys.add("men")

    primary_text = " ".join([category_name, product_type]).lower()
    explicit_jacket_category = any(
        item.lower() == "куртки" or item.lower().endswith(" куртки") for item in categories
    )
    if "куртк" in primary_text or "jacket" in primary_text or explicit_jacket_category:
        keys.add("jackets")
    return sorted(keys)
