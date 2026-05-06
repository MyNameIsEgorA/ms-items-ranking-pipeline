from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query

from app.config import Settings, get_settings
from app.data_pipeline import ProductRankingPipeline
from app.meili import MeiliClient
from app.schemas import BuildRequest, BuildResponse, SearchResponse, SortField


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    settings = get_settings()
    app = FastAPI(
        title=settings.title,
        version="1.0.0",
        description=(
            "FastAPI prototype that cleans KixBox dumps, calculates ranking features, "
            "indexes product documents in Meilisearch and serves ranked category pages."
        ),
    )

    @app.post("/index/rebuild", response_model=BuildResponse)
    async def rebuild_index(
        request: BuildRequest,
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> BuildResponse:
        pipeline = ProductRankingPipeline(settings)
        try:
            documents, stats = await asyncio.to_thread(
                pipeline.build_documents,
                request.start_date,
                request.end_date,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        task_uid = None
        if not request.dry_run:
            try:
                task_uid = await MeiliClient(settings).replace_documents(documents, wait=request.wait_for_meili)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Meilisearch indexing failed: {exc}") from exc

        return BuildResponse(
            indexed=not request.dry_run,
            documents=len(documents),
            period_start=stats["period_start"],
            period_end=stats["period_end"],
            meili_task_uid=task_uid,
            stats=stats,
        )

    @app.get("/search", response_model=SearchResponse)
    async def search(
        settings: Annotated[Settings, Depends(get_settings)],
        q: str = "",
        filter: str | None = None,
        sort: SortField = "final_score",
        limit: Annotated[int, Query(ge=1, le=50)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> SearchResponse:
        result = await MeiliClient(settings).search(
            query=q,
            filters=filter,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        return SearchResponse(
            hits=result.get("hits", []),
            estimated_total_hits=result.get("estimatedTotalHits"),
            limit=limit,
            offset=offset,
            sort=sort,
        )

    return app


app = create_app()
