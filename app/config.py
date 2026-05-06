from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    title: str = "Предобработка данных и ранжирование товаров по бизнес правилам"
    data_dir: Path = Path("data_dumps")
    user_events_dir: Path = Path("data_dumps/user-events")
    score_plots_dir: Path = Path("data_dumps/score-plots")
    event_period_cache_path: Path = Path("data_dumps/event-period-cache.json")

    meili_url: str = "http://localhost:7700"
    meili_master_key: str = "local-master-key"
    meili_index: str = "products"

    popularity_window_days: int = 30
    popularity_half_life_days: int = 30
    popularity_view_weight: float = 0.3
    popularity_purchase_weight: float = 0.7

    boost_in_stock: float = 1.5
    boost_sale: float = 1.2
    boost_out_of_stock: float = 0.05

    default_sort: Literal["final_score", "popularity", "novelty"] = "final_score"
    max_page_size: int = Field(default=50, ge=1, le=100)


@lru_cache
def get_settings() -> Settings:
    return Settings()
