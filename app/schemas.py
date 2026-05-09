from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


SortField = Literal["final_score", "final_score_1", "final_score_2", "final_score_3", "popularity", "novelty"]


class BuildRequest(BaseModel):
    start_date: datetime | None = Field(default=None, description="Inclusive start of the event processing period.")
    end_date: datetime | None = Field(default=None, description="Inclusive end of the event processing period.")
    dry_run: bool = Field(default=False, description="Build documents but do not upload to Meilisearch.")
    wait_for_meili: bool = Field(default=True, description="Wait until Meilisearch indexing task completes.")

    @model_validator(mode="after")
    def validate_period(self) -> "BuildRequest":
        if self.start_date is None and self.end_date is None:
            return self
        if self.start_date is None or self.end_date is None:
            raise ValueError("start_date and end_date must be provided together")
        start_date = self.start_date if self.start_date.tzinfo else self.start_date.replace(tzinfo=timezone.utc)
        end_date = self.end_date if self.end_date.tzinfo else self.end_date.replace(tzinfo=timezone.utc)
        if start_date > end_date:
            raise ValueError("start_date must be less than or equal to end_date")
        return self


class BuildResponse(BaseModel):
    indexed: bool
    documents: int
    period_start: datetime
    period_end: datetime
    meili_task_uid: int | None = None
    stats: dict[str, Any]


class ProductDocument(BaseModel):
    product_id: str
    title: str
    category_id: str
    category_name: str
    categories: list[str] = Field(default_factory=list)
    brand: str = ""
    price: float = 0.0
    old_price: float = 0.0
    discount: float = 0.0
    in_stock: bool = False
    stock: float = 0.0
    is_new: bool = False
    is_sale: bool = False
    page_keys: list[str] = Field(default_factory=list)
    gender: str = ""
    product_type: str = ""
    season: str = ""
    popularity: float = 0.0
    novelty: float = 0.0
    final_score: float = 0.0
    final_score_1: float = 0.0
    final_score_2: float = 0.0
    final_score_3: float = 0.0
    age_days: float = 0.0
    created_at: datetime
    url: str = ""
    image_url: str = ""
    barcodes: list[str] = Field(default_factory=list)
    variant_ids: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    hits: list[dict[str, Any]]
    estimated_total_hits: int | None = None
    limit: int
    offset: int
    sort: SortField
