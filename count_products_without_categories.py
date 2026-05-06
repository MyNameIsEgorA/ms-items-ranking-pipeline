from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx

from app.config import get_settings


PAGE_SIZE = 1000
NO_CATEGORY_NAME = "Без категории"


def has_no_categories(document: dict[str, Any]) -> bool:
    categories = document.get("categories")
    category_name = str(document.get("category_name") or "").strip()

    return (not categories) or category_name == NO_CATEGORY_NAME


def fetch_documents(client: httpx.Client, url: str, headers: dict[str, str], offset: int) -> dict[str, Any]:
    response = client.get(
        url,
        headers=headers,
        params={
            "limit": PAGE_SIZE,
            "offset": offset,
            "fields": "product_id,title,category_name,categories",
        },
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Count products without categories in the configured Meilisearch index."
    )
    parser.add_argument(
        "--show-examples",
        type=int,
        default=0,
        help="Print the first N products without categories.",
    )
    args = parser.parse_args()

    settings = get_settings()
    documents_url = f"{settings.meili_url}/indexes/{settings.meili_index}/documents"
    headers = {"Authorization": f"Bearer {settings.meili_master_key}"}

    total_seen = 0
    without_categories = 0
    examples: list[dict[str, Any]] = []

    try:
        with httpx.Client(timeout=30) as client:
            offset = 0
            while True:
                payload = fetch_documents(client, documents_url, headers, offset)
                documents = payload.get("results", [])
                if not documents:
                    break

                total_seen += len(documents)
                for document in documents:
                    if has_no_categories(document):
                        without_categories += 1
                        if len(examples) < args.show_examples:
                            examples.append(document)

                offset += len(documents)

                total = payload.get("total")
                if isinstance(total, int) and offset >= total:
                    break
    except httpx.HTTPStatusError as exc:
        print(f"Meilisearch returned HTTP {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"Could not read documents from Meilisearch: {exc}", file=sys.stderr)
        return 1

    print(f"Meilisearch URL: {settings.meili_url}")
    print(f"Index: {settings.meili_index}")
    print(f"Products checked: {total_seen}")
    print(f"Products without categories: {without_categories}")

    if examples:
        print()
        print("Examples:")
        for document in examples:
            print(
                f"- {document.get('product_id')}: "
                f"{document.get('title')} "
                f"(category_name={document.get('category_name')!r}, categories={document.get('categories')!r})"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
