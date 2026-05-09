from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import Settings
from app.schemas import ProductDocument, SortField


logger = logging.getLogger(__name__)


class MeiliClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.headers = {"Authorization": f"Bearer {settings.meili_master_key}"}

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.settings.meili_url}/health", headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def configure_index(self) -> None:
        payload = {
            "filterableAttributes": [
                "category_id",
                "category_name",
                "categories",
                "brand",
                "in_stock",
                "is_new",
                "is_sale",
                "page_keys",
                "gender",
                "product_type",
            ],
            "sortableAttributes": [
                "final_score",
                "final_score_1",
                "final_score_2",
                "final_score_3",
                "popularity",
                "novelty",
                "age_days",
                "price",
                "discount",
                "stock",
            ],
            "searchableAttributes": ["title", "brand", "category_name", "categories", "product_type"],
            "displayedAttributes": ["*"],
        }
        async with httpx.AsyncClient(timeout=30) as client:
            await self.ensure_index(client)
            response = await client.patch(
                f"{self.settings.meili_url}/indexes/{self.settings.meili_index}/settings",
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()

    async def ensure_index(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            f"{self.settings.meili_url}/indexes/{self.settings.meili_index}",
            headers=self.headers,
        )
        if response.status_code == 200:
            return
        if response.status_code != 404:
            response.raise_for_status()

        create_response = await client.post(
            f"{self.settings.meili_url}/indexes",
            headers=self.headers,
            json={"uid": self.settings.meili_index, "primaryKey": "product_id"},
        )
        create_response.raise_for_status()
        await self.wait_for_task(client, create_response.json()["taskUid"])

    async def replace_documents(self, documents: list[ProductDocument], wait: bool = True) -> int:
        await self.configure_index()
        payload = [document.model_dump(mode="json") for document in documents]
        logger.info("Uploading documents to Meilisearch: index=%s documents=%s", self.settings.meili_index, len(payload))
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.put(
                f"{self.settings.meili_url}/indexes/{self.settings.meili_index}/documents",
                params={"primaryKey": "product_id"},
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()
            task_uid = response.json()["taskUid"]
            if wait:
                await self.wait_for_task(client, task_uid)
            logger.info("Meilisearch document upload accepted: task_uid=%s", task_uid)
            return task_uid

    async def wait_for_task(self, client: httpx.AsyncClient, task_uid: int) -> None:
        for _ in range(120):
            response = await client.get(
                f"{self.settings.meili_url}/tasks/{task_uid}",
                headers=self.headers,
            )
            response.raise_for_status()
            task = response.json()
            if task["status"] in {"succeeded", "failed", "canceled"}:
                if task["status"] != "succeeded":
                    raise RuntimeError(f"Meilisearch task {task_uid} ended with {task['status']}: {task}")
                return
            await asyncio.sleep(1)
        raise TimeoutError(f"Meilisearch task {task_uid} did not finish in time")

    async def search(
        self,
        query: str = "",
        filters: str | None = None,
        sort: SortField = "final_score",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "q": query,
            "limit": limit,
            "offset": offset,
            "sort": [f"{sort}:desc"],
        }
        if filters:
            payload["filter"] = filters

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.settings.meili_url}/indexes/{self.settings.meili_index}/search",
                headers=self.headers,
                json=payload,
            )
            if response.status_code == 404:
                return {"hits": [], "estimatedTotalHits": 0}
            response.raise_for_status()
            return response.json()
