"""DynamoDB access. One table per customer env:

    pk="title", sk=<id>   a catalog title
    pk="recs",  sk=<id>   last known recommendations (the fallback when the vendor is
                          slow or down)
"""

from __future__ import annotations

from typing import Any

from boto3.dynamodb.conditions import Key


def _title(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["sk"],
        "name": item.get("name", ""),
        "genre": item.get("genre", ""),
        "year": int(item.get("year", 0)),
    }


class Store:
    def __init__(self, table: Any) -> None:
        self.table = table

    def list_titles(self) -> list[dict[str, Any]]:
        resp = self.table.query(KeyConditionExpression=Key("pk").eq("title"))
        return [_title(i) for i in resp.get("Items", [])]

    def get_title(self, title_id: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": "title", "sk": title_id}).get("Item")
        return _title(item) if item else None

    def get_cached_recs(self, title_id: str) -> list[str]:
        item = self.table.get_item(Key={"pk": "recs", "sk": title_id}).get("Item")
        return list(item.get("recs", [])) if item else []

    def put_cached_recs(self, title_id: str, recs: list[str]) -> None:
        self.table.put_item(Item={"pk": "recs", "sk": title_id, "recs": recs})
