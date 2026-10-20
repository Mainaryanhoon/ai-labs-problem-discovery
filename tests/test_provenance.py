import json

from app import ingestion, social_ingestion
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, initialize_database
from app.models import (
    ClusterEvidence,
    ProblemCluster,
    SourceObservation,
    SourceRecord,
    Submission,
)
from app.provenance import (
    clean_native_id,
    community_yield,
    normalize_subreddit,
    parse_source_reference,
    record_source_provenance,
)


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    initialize_database(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)()


def test_source_reference_parsing_and_validation():
    reddit = parse_source_reference(
        "Reddit",
        "https://old.reddit.com/r/SaaS/comments/1ab2cd/title/",
    )
    assert (reddit.native_id, reddit.community) == ("1ab2cd", "saas")
    assert parse_source_reference(
        "Reddit", "https://redd.it/1ab2cd"
    ).native_id == "1ab2cd"
    assert parse_source_reference(
        "X", "https://x.com/someone/status/998877"
    ).native_id == "998877"
    assert parse_source_reference(
        "Hacker News", "https://news.ycombinator.com/item?id=4242"
    ).native_id == "4242"
    stack = parse_source_reference(
        "Stack Exchange:superuser",
        "https://superuser.com/questions/777/title",
    )
    assert (stack.native_id, stack.community) == ("777", "superuser")
    assert clean_native_id(True) is None
    assert clean_native_id("t3_AbC123", source_name="Reddit") == "abc123"
    assert normalize_subreddit("/r/SaaS/") == "saas"
    assert normalize_subreddit("../private") is None
    assert parse_source_reference(
        "Reddit", "https://notreddit.com/r/saas/comments/1ab2cd/"
    ).native_id is None


def test_all_ingestion_adapters_persist_native_identity_and_timestamp_origin(
    monkeypatch,
):
    engine, db = _database()
    try:
        reddit_item = {
            "itemType": "post",
            "postId": "t3_abc123",
            "title": "Manual reporting is frustrating",
            "selftext": "I struggle to reconcile weekly reports.",
            "link": "https://www.reddit.com/r/SaaS/comments/abc123/title/",
            "author": "owner",
            "publishingDate": "2024-01-02T03:04:05.000Z",
        }
        reddit_result = social_ingestion._save_items(
            db, [reddit_item], "Reddit"
        )
        reddit_record = db.get(SourceRecord, reddit_result["submission_ids"][0])
        assert (reddit_record.native_id, reddit_record.community) == (
            "abc123",
            "saas",
        )
        assert reddit_record.timestamp_origin == "source"
        reddit_observation = db.scalars(
            select(SourceObservation).where(
                SourceObservation.submission_id
                == reddit_result["submission_ids"][0]
            )
        ).one()
        assert reddit_observation.source_url == reddit_item["link"]
        assert reddit_observation.native_id == "abc123"

        duplicate = {
            **reddit_item,
            "title": "Another title that describes a different frustrating task",
            "link": "https://old.reddit.com/r/saas/comments/abc123/",
        }
        assert social_ingestion._save_items(
            db, [duplicate], "Reddit"
        )["duplicates_skipped"] == 1

        x_result = social_ingestion._save_items(
            db,
            [{
                "type": "tweet",
                "id": "5550001",
                "url": "https://x.com/example/status/5550001",
                "text": "I struggle with this frustrating manual workflow.",
                "lang": "en",
                "createdAt": "Tue Jan 02 03:04:05 +0000 2024",
                "author": {"userName": "example"},
            }],
            "X",
        )
        x_record = db.get(SourceRecord, x_result["submission_ids"][0])
        assert x_record.native_id == "5550001"
        assert x_record.timestamp_origin == "source"

        def fake_get_json(url, *, params, source):
            if "hn.algolia.com" in url:
                return {"hits": [{
                    "objectID": "4242",
                    "title": "Ask HN: Manual invoice work is frustrating",
                    "story_text": "I struggle with repetitive invoices.",
                    "created_at_i": 1_704_164_645,
                }]}
            return {"items": [{
                "question_id": 777,
                "title": "Manual spreadsheet work is frustrating",
                "body": "<p>I struggle with repetitive copy-paste.</p>",
                "link": "https://superuser.com/questions/777/title",
                "creation_date": 1_704_164_645,
            }]}

        monkeypatch.setattr(ingestion, "_get_json", fake_get_json)
        ingestion.collect_hacker_news(5, db)
        ingestion.collect_stack_exchange(5, "superuser", db)
        source_records = {
            record.source_name: record
            for record in db.scalars(select(SourceRecord)).all()
        }
        assert source_records["Hacker News"].native_id == "4242"
        assert source_records["Stack Exchange:superuser"].native_id == "777"
        assert source_records["Stack Exchange:superuser"].community == "superuser"
    finally:
        db.close()
        engine.dispose()


def test_community_yield_excludes_malformed_analysis_and_counts_links():
    engine, db = _database()
    try:
        records = [
            Submission(
                text="A genuine problem that happens repeatedly.",
                source_name="Reddit",
                analysis_status="completed",
                analysis_json=json.dumps({
                    "is_genuine_problem": True,
                    "signal_type": "pain_point",
                }),
            ),
            Submission(
                text="No valid analysis available.",
                source_name="Reddit",
                analysis_status="completed",
                analysis_json="not json",
            ),
            Submission(
                text="A rejected promotion.",
                source_name="Reddit",
                analysis_status="completed",
                analysis_json=json.dumps({
                    "is_genuine_problem": False,
                    "signal_type": "noise",
                }),
            ),
        ]
        db.add_all(records)
        db.flush()
        for record in records:
            record_source_provenance(
                db,
                record,
                native_id=None,
                community="saas",
                source_url=f"https://example.test/{record.id}",
                canonical_url=f"https://example.test/{record.id}",
                source_created_at=None,
            )
        cluster = ProblemCluster(
            title="Manual process",
            problem_statement="Work is repetitive.",
        )
        db.add(cluster)
        db.flush()
        db.add(ClusterEvidence(
            cluster_id=cluster.id,
            submission_id=records[0].id,
            relationship_type="supports",
        ))
        db.commit()

        report = community_yield(db)

        assert report == [{
            "source_name": "Reddit",
            "community": "saas",
            "collected": 3,
            "analyzed": 2,
            "genuine": 1,
            "clustered": 1,
            "genuine_rate": 0.5,
        }]
    finally:
        db.close()
        engine.dispose()


def test_additive_initialization_adds_source_table_without_altering_submissions():
    engine = create_engine("sqlite://")
    try:
        Submission.__table__.create(engine)
        before = {
            column["name"]
            for column in inspect(engine).get_columns("submissions")
        }

        initialize_database(engine)

        after = {
            column["name"]
            for column in inspect(engine).get_columns("submissions")
        }
        assert after == before
        assert "source_records" in inspect(engine).get_table_names()
        assert "native_id" in {
            column["name"]
            for column in inspect(engine).get_columns("source_records")
        }
        assert "source_observations" in inspect(engine).get_table_names()
        assert "weekly_ranking_snapshots" in inspect(engine).get_table_names()
        assert "weekly_ranking_entries" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
