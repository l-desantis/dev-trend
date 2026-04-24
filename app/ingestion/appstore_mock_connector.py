import json
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.ingestion.base import BaseConnector, NormalizedItem


class AppStoreMockConnector(BaseConnector):
    source_type = "appstore"

    def __init__(self, *args, mock_dir: Path = Path("data/mock"), **kwargs):
        super().__init__(*args, **kwargs)
        self.mock_dir = mock_dir

    async def fetch(self) -> list[dict]:
        settings = get_settings()
        if not settings.enable_mock_appstore:
            return []
        records = []
        for path in sorted(self.mock_dir.glob("appstore_*.json")):
            records.extend(json.loads(path.read_text()))
        return records

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        items = []
        for r in raw:
            items.append(NormalizedItem(
                source_type=self.source_type,
                external_id=r["external_id"],
                title=r["title"],
                body=r["description"],
                url=r.get("url"),
                created_at=datetime.fromisoformat(r["updated_at"].rstrip("Z")).replace(tzinfo=UTC),
                metadata={
                    k: r[k] for k in
                    ["category", "growth_index", "install_proxy", "rating", "review_sentiment", "competitor_density", "updated_at"]
                    if k in r
                },
            ))
        return items
