from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import docs_ingestion
from app.db import Base
from app.models import Submission


FEED = "https://cloud.google.com/feeds/bigquery-release-notes.xml"


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _atom(updated: datetime, entry_id: str = "release-1") -> str:
    return f"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>{entry_id}</id>
        <title>BigQuery release</title>
        <updated>{updated.isoformat()}</updated>
        <link href="https://cloud.google.com/bigquery/release" />
        <summary>A BigQuery release adds a new reporting capability.</summary>
      </entry>
    </feed>"""


def test_official_docs_use_policy_feeds_and_skip_old_or_known_items(monkeypatch):
    now = datetime.now(timezone.utc)
    recent_xml = _atom(now - timedelta(days=1))
    old_xml = _atom(now - timedelta(days=90), "release-old")
    payloads = [recent_xml, recent_xml, old_xml]
    calls = []

    class Response:
        def __init__(self, text):
            self.text = text
            self.status_code = 200

        def raise_for_status(self):
            return None

    def get(url, **kwargs):
        calls.append(url)
        return Response(payloads.pop(0))

    monkeypatch.setattr(docs_ingestion, "policy_docs_feeds", lambda: [FEED])
    monkeypatch.setattr(docs_ingestion.httpx, "get", get)
    db = _session()
    try:
        first = docs_ingestion.collect_official_docs(5, db)
        duplicate = docs_ingestion.collect_official_docs(5, db)
        old = docs_ingestion.collect_official_docs(5, db)
        assert calls == [FEED, FEED, FEED]
        assert first["created"] == 1
        assert duplicate["created"] == 0
        assert duplicate["duplicates_skipped"] == 1
        assert old["created"] == 0
        assert old["outside_budget"] == 1
        saved = db.scalars(select(Submission)).one()
        assert saved.source_name == "official_docs"
        assert saved.source_url == "https://cloud.google.com/bigquery/release"
    finally:
        db.close()


def test_official_docs_does_not_use_catalogue_feeds_without_policy(monkeypatch):
    monkeypatch.setattr(docs_ingestion, "policy_docs_feeds", lambda: [])
    db = _session()
    try:
        result = docs_ingestion.collect_official_docs(5, db)
        assert result["created"] == 0
        assert result["submission_ids"] == []
    finally:
        db.close()