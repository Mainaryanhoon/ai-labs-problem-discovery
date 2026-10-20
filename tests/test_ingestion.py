import json
from datetime import datetime, timezone
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app, get_cluster
from app.models import (
    ClusterEvidence,
    ProblemCluster,
    SourceObservation,
    Submission,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://provider.example")
            response = httpx.Response(
                self.status_code,
                request=request,
            )
            raise httpx.HTTPStatusError(
                "provider error",
                request=request,
                response=response,
            )

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.fixture
def ingestion_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr("app.main.engine", engine)
    monkeypatch.setattr(
        "app.security.get_settings",
        lambda: SimpleNamespace(
            service_api_key="",
            submission_rate_limit_per_minute=0,
        ),
    )
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, TestingSession
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _hn_item(object_id="12345"):
    return {
        "objectID": object_id,
        "author": "hn_author",
        "title": "Ask HN: <b>Manual reporting is frustrating</b>",
        "story_text": "I struggle to reconcile several weekly reports.",
        "created_at_i": 1_700_000_000,
    }


def _stack_item(question_id=8123):
    return {
        "question_id": question_id,
        "title": "Manual reconciliation is frustrating",
        "body": "<p>I struggle to reconcile monthly orders.</p>",
        "link": (
            "https://stackoverflow.com/questions/"
            f"{question_id}/manual-reconciliation"
        ),
        "creation_date": 1_700_000_000,
        "tags": ["workflow", None],
        "score": 2,
        "answer_count": 1,
        "view_count": 20,
        "owner": {"display_name": "se_author"},
    }


def _reddit_item(post_id="t3_abc"):
    return {
        "itemType": "post",
        "postId": post_id,
        "title": "Manual reporting is frustrating",
        "selftext": "I struggle to reconcile the weekly orders.",
        "link": f"https://www.reddit.com/r/test/comments/{post_id}/",
        "author": "shop_owner",
        "publishingDate": "2024-01-02T03:04:05.000Z",
    }


def _x_item(tweet_id="123456"):
    return {
        "type": "tweet",
        "id": tweet_id,
        "url": f"https://x.com/example/status/{tweet_id}",
        "text": "I struggle with this frustrating manual workflow.",
        "lang": "en",
        "createdAt": "Tue Jan 02 03:04:05 +0000 2024",
        "author": {"userName": "example"},
    }


def _install_settings(monkeypatch):
    settings = SimpleNamespace(
        apify_api_token="test-apify-token",
        apify_reddit_actor_id="anyxsolutions~reddit-scraper",
        apify_x_actor_id="apidojo~tweet-scraper",
    )
    monkeypatch.setattr("app.social_ingestion.get_settings", lambda: settings)


def test_hacker_news_is_cleaned_deduplicated_and_repeatable(
    ingestion_client, monkeypatch
):
    client, session_factory = ingestion_client
    monkeypatch.setattr(
        "app.ingestion.httpx.get",
        Mock(return_value=FakeResponse({"hits": [_hn_item(), _hn_item("54321")]})),
    )

    first = client.post("/api/ingestion/hacker-news")
    second = client.post("/api/ingestion/hacker-news")

    assert first.status_code == 200
    assert first.json()["created"] == 1
    assert first.json()["duplicates_skipped"] == 1
    assert second.json()["created"] == 0
    assert second.json()["duplicates_skipped"] == 2

    with session_factory() as db:
        saved = db.scalars(select(Submission)).all()
    assert len(saved) == 1
    assert saved[0].text == (
        "Ask HN: Manual reporting is frustrating\n\n"
        "I struggle to reconcile several weekly reports."
    )
    assert saved[0].source_url == (
        "https://news.ycombinator.com/item?id=12345"
    )
    assert saved[0].submitted_by == "hn_author"
    assert saved[0].language == "und"
    assert saved[0].created_at.replace(tzinfo=timezone.utc) == (
        datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    )


def test_hacker_news_skips_malformed_hits_and_handles_empty_payload(
    ingestion_client, monkeypatch
):
    client, _ = ingestion_client
    get = Mock(side_effect=[
        FakeResponse({"hits": [None, {"objectID": "invalid"}, _hn_item()]}),
        FakeResponse({"hits": []}),
    ])
    monkeypatch.setattr("app.ingestion.httpx.get", get)

    malformed_batch = client.post("/api/ingestion/hacker-news")
    empty_batch = client.post("/api/ingestion/hacker-news")

    assert malformed_batch.status_code == 200
    assert malformed_batch.json()["created"] == 1
    assert malformed_batch.json()["irrelevant_skipped"] == 2
    assert empty_batch.json()["fetched"] == 0
    assert empty_batch.json()["created"] == 0


def test_stack_exchange_preserves_url_id_and_source_timestamp(
    ingestion_client, monkeypatch
):
    client, session_factory = ingestion_client
    monkeypatch.setattr(
        "app.ingestion.httpx.get",
        Mock(return_value=FakeResponse({
            "items": [_stack_item(), _stack_item()],
            "quota_remaining": 9000,
        })),
    )

    first = client.post("/api/ingestion/stack-exchange")
    second = client.post("/api/ingestion/stack-exchange")

    assert first.status_code == 200
    assert first.json()["created"] == 1
    assert first.json()["duplicates_skipped"] == 1
    assert second.json()["created"] == 0
    assert second.json()["duplicates_skipped"] == 2

    with session_factory() as db:
        saved = db.scalars(select(Submission)).one()
    assert saved.source_name == "Stack Exchange:stackoverflow"
    assert saved.source_url == (
        "https://stackoverflow.com/questions/"
        "8123/manual-reconciliation"
    )
    assert "source_id=8123" in saved.text
    assert saved.submitted_by == "se_author"
    assert saved.language == "und"
    assert saved.created_at.replace(tzinfo=timezone.utc) == (
        datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    )


def test_stack_exchange_skips_malformed_questions_and_reports_rate_limit(
    ingestion_client, monkeypatch
):
    client, _ = ingestion_client
    get = Mock(side_effect=[
        FakeResponse({"items": [[], _stack_item()]}),
        FakeResponse({"backoff": 10, "items": []}),
    ])
    monkeypatch.setattr("app.ingestion.httpx.get", get)

    batch = client.post("/api/ingestion/stack-exchange")
    limited = client.post("/api/ingestion/stack-exchange")

    assert batch.status_code == 200
    assert batch.json()["created"] == 1
    assert batch.json()["irrelevant_skipped"] == 1
    assert limited.status_code == 503
    assert "backoff" in limited.json()["detail"]


@pytest.mark.parametrize(
    ("path", "provider_module", "payload"),
    [
        (
            "/api/ingestion/stack-exchange",
            "app.ingestion.httpx.get",
            {"items": []},
        ),
        (
            "/api/ingestion/reddit",
            "app.social_ingestion.httpx.post",
            [],
        ),
        (
            "/api/ingestion/x",
            "app.social_ingestion.httpx.post",
            [],
        ),
    ],
)
def test_empty_provider_results_are_successful_noop(
    ingestion_client,
    monkeypatch,
    path,
    provider_module,
    payload,
):
    client, _ = ingestion_client
    if "social_ingestion" in provider_module:
        _install_settings(monkeypatch)
    monkeypatch.setattr(
        provider_module,
        Mock(return_value=FakeResponse(payload)),
    )

    response = client.post(path)

    assert response.status_code == 200
    assert response.json()["fetched"] == 0
    assert response.json()["created"] == 0


@pytest.mark.parametrize(
    ("path", "source", "item"),
    [
        ("/api/ingestion/reddit", "Reddit", _reddit_item()),
        ("/api/ingestion/x", "X", _x_item()),
    ],
)
def test_apify_sources_map_documented_actor_outputs_and_deduplicate(
    ingestion_client,
    monkeypatch,
    path,
    source,
    item,
):
    client, session_factory = ingestion_client
    _install_settings(monkeypatch)
    post = Mock(return_value=FakeResponse([item, item]))
    monkeypatch.setattr("app.social_ingestion.httpx.post", post)

    response = client.post(path)
    saved_response = client.post(path)

    assert response.status_code == 200
    assert response.json()["source"] == source
    assert response.json()["created"] == 1
    assert response.json()["duplicates_skipped"] == 1
    assert saved_response.json()["created"] == 0
    authorization = post.call_args.kwargs["headers"]["Authorization"]
    assert authorization == "Bearer test-apify-token"
    assert authorization != "Bearer ******"
    if source == "Reddit":
        actor_input = post.call_args.kwargs["json"]
        assert actor_input["maxItems"] == 10
        assert actor_input["maxComments"] == 0
        assert actor_input["searchQueries"]
        assert "startUrls" not in actor_input
    else:
        actor_input = post.call_args.kwargs["json"]
        assert actor_input["maxItems"] == 50
        assert len(actor_input["searchTerms"]) == 1
        assert actor_input["sort"] == "Latest"
        assert post.call_args.kwargs["params"]["limit"] == 50

    with session_factory() as db:
        saved = db.scalars(select(Submission)).one()
    assert saved.source_name == source
    assert saved.source_url == item["link" if source == "Reddit" else "url"]
    assert saved.submitted_by == (
        "shop_owner" if source == "Reddit" else "example"
    )
    assert saved.analysis_status == "pending"
    assert saved.language == ("und" if source == "Reddit" else "en")
    assert saved.created_at.year == 2024


@pytest.mark.parametrize(
    ("path", "items"),
    [
        (
            "/api/ingestion/reddit",
            [
                None,
                {"itemType": "comment"},
                {"itemType": "subreddit"},
                _reddit_item() | {"link": "//malformed.example/post"},
            ],
        ),
        (
            "/api/ingestion/x",
            [
                None,
                {"type": "user"},
                _x_item() | {"url": "javascript:invalid"},
            ],
        ),
    ],
)
def test_apify_skips_malformed_and_non_post_records(
    ingestion_client,
    monkeypatch,
    path,
    items,
):
    client, _ = ingestion_client
    _install_settings(monkeypatch)
    monkeypatch.setattr(
        "app.social_ingestion.httpx.post",
        Mock(return_value=FakeResponse(items)),
    )

    response = client.post(path)

    assert response.status_code == 200
    expected_fetched = 4 if path.endswith("/reddit") else 3
    assert response.json()["fetched"] == expected_fetched
    assert response.json()["created"] == 0
    assert response.json()["irrelevant_skipped"] == expected_fetched


@pytest.mark.parametrize(
    ("path", "source", "item"),
    [
        ("/api/ingestion/reddit", "Reddit", _reddit_item()),
        ("/api/ingestion/x", "X", _x_item()),
    ],
)
def test_apify_optional_metadata_may_be_absent(
    ingestion_client,
    monkeypatch,
    path,
    source,
    item,
):
    client, session_factory = ingestion_client
    _install_settings(monkeypatch)
    item = item.copy()
    if source == "Reddit":
        item.pop("author")
        item.pop("publishingDate")
    else:
        item.pop("author")
        item.pop("createdAt")
        item.pop("lang")
    monkeypatch.setattr(
        "app.social_ingestion.httpx.post",
        Mock(return_value=FakeResponse([item])),
    )

    response = client.post(path)

    assert response.status_code == 200
    assert response.json()["created"] == 1
    with session_factory() as db:
        saved = db.scalars(select(Submission)).one()
    assert saved.submitted_by is None
    assert saved.language == "und"
    assert saved.source_url == item["link" if source == "Reddit" else "url"]


def test_x_contract_accepts_documented_records_without_type_and_preserves_quotes(
    ingestion_client,
    monkeypatch,
):
    client, session_factory = ingestion_client
    _install_settings(monkeypatch)
    item = _x_item()
    item.pop("type")
    item["text"] = (
        "I struggle with a manual workflow. "
        "Quoted post: a small business owner describes the same pain."
    )
    monkeypatch.setattr(
        "app.social_ingestion.httpx.post",
        Mock(return_value=FakeResponse([item, item | {"type": "user"}])),
    )

    response = client.post("/api/ingestion/x")

    assert response.status_code == 200
    assert response.json()["created"] == 1
    assert response.json()["irrelevant_skipped"] == 1
    with session_factory() as db:
        saved = db.scalars(select(Submission)).one()
    assert saved.text == item["text"]
    assert saved.source_url == item["url"]


def test_cross_source_duplicate_preserves_each_observation_and_is_idempotent(
    ingestion_client,
    monkeypatch,
):
    _client, session_factory = ingestion_client
    _install_settings(monkeypatch)
    from app.social_ingestion import _save_items

    reddit_item = _reddit_item("t3_cross123")
    original_text = (
        f"{reddit_item['title']}\n\n{reddit_item['selftext']}"
    )
    x_item = _x_item("87654321")
    x_item["text"] = original_text

    with session_factory() as db:
        reddit_result = _save_items(db, [reddit_item], "Reddit")
        submission_id = reddit_result["submission_ids"][0]

        first_x = _save_items(db, [x_item], "X")
        repeated_x = _save_items(db, [x_item], "X")

        submission_count = db.query(Submission).count()
        observations = db.scalars(
            select(SourceObservation)
            .where(SourceObservation.submission_id == submission_id)
            .order_by(SourceObservation.source_name)
        ).all()
        cluster = ProblemCluster(
            title="Cross-source problem",
            problem_statement="A repeated workflow problem.",
        )
        db.add(cluster)
        db.flush()
        db.add(ClusterEvidence(
            cluster_id=cluster.id,
            submission_id=submission_id,
        ))
        db.commit()
        detail = get_cluster(cluster.id, db)

    assert first_x["created"] == 0
    assert first_x["duplicates_skipped"] == 1
    assert first_x["observations_linked"] == 1
    assert first_x["submission_ids"] == []
    assert repeated_x["duplicates_skipped"] == 1
    assert submission_count == 1
    assert len(observations) == 2
    assert {
        (item.source_name, item.source_url, item.native_id)
        for item in observations
    } == {
        ("Reddit", reddit_item["link"], "cross123"),
        ("X", x_item["url"], "87654321"),
    }
    evidence_observations = detail["evidence_provenance"][0][
        "source_observations"
    ]
    assert {item["source_name"] for item in evidence_observations} == {
        "Reddit",
        "X",
    }


def test_x_provider_metadata_is_preserved_for_pipeline_classification(
    ingestion_client,
):
    _client, session_factory = ingestion_client
    from app.social_ingestion import _save_items
    from app.models import SubmissionProviderMetadata

    item = _x_item("987654")
    item["author"]["isBusinessAccount"] = True
    item.update({
        "isRetweet": True,
        "isReply": True,
        "inReplyToStatusId": "987650",
        "isQuote": True,
        "quotedTweet": {"id": "987649"},
    })

    with session_factory() as db:
        result = _save_items(db, [item], "X")
        saved_metadata = db.get(
            SubmissionProviderMetadata,
            result["submission_ids"][0],
        )

    assert result["created"] == 1
    metadata = result["submission_metadata"][str(result["submission_ids"][0])]
    assert metadata == {
        "is_repost": True,
        "is_reply": True,
        "is_quote": True,
        "author": "example",
        "author_is_business_account": True,
    }
    assert json.loads(saved_metadata.metadata_json) == metadata


def test_reddit_subreddit_input_and_local_per_community_caps(
    ingestion_client,
    monkeypatch,
):
    client, session_factory = ingestion_client
    _install_settings(monkeypatch)
    items = []
    items.append({"itemType": "comment", "subreddit": "smallbusiness"})
    for community, ids in (
        ("smallbusiness", ("a1", "a2", "a3", "a4", "a5")),
        ("saas", ("b1", "b2")),
    ):
        for post_id in ids:
            items.append({
                "itemType": "post",
                "postId": f"t3_{post_id}",
                "title": "Manual reporting is frustrating",
                "selftext": f"I struggle with repetitive reports for {post_id}.",
                "link": (
                    f"https://www.reddit.com/r/{community}/comments/"
                    f"{post_id}/title/"
                ),
                "subreddit": community,
            })
    items.append({"itemType": "subreddit", "subreddit": "smallbusiness"})
    post = Mock(return_value=FakeResponse(items))
    monkeypatch.setattr("app.social_ingestion.httpx.post", post)

    response = client.post(
        "/api/ingestion/reddit",
        params=[
            ("limit", "8"),
            ("subreddits", "r/smallbusiness"),
            ("subreddits", "SaaS"),
        ],
    )

    assert response.status_code == 200
    result = response.json()
    actor_input = post.call_args.kwargs["json"]
    assert actor_input["startUrls"] == [
        {"url": "https://www.reddit.com/r/smallbusiness/new/"},
        {"url": "https://www.reddit.com/r/saas/new/"},
    ]
    assert actor_input["maxItems"] == 8
    assert actor_input["maxComments"] == 0
    assert result["per_community_limits"] == {
        "smallbusiness": 4,
        "saas": 4,
    }
    assert result["created"] == 6
    assert result["bounded_skipped"] == 1
    assert result["irrelevant_skipped"] == 1
    with session_factory() as db:
        saved = db.scalars(select(Submission)).all()
    assert len(saved) == 6
    assert all(record.source_name == "Reddit" for record in saved)
    assert all(record.source_url.startswith("https://www.reddit.com/r/") for record in saved)


def test_x_collection_rejects_below_actor_minimum_without_request(
    ingestion_client,
    monkeypatch,
):
    client, _ = ingestion_client
    _install_settings(monkeypatch)
    post = Mock()
    monkeypatch.setattr("app.social_ingestion.httpx.post", post)

    response = client.post("/api/ingestion/x?limit=49")

    assert response.status_code == 422
    assert post.call_count == 0


def test_x_actor_run_is_not_started_concurrently_in_one_worker(
    monkeypatch,
):
    import app.social_ingestion as social_ingestion

    started = threading.Event()
    finish = threading.Event()
    monkeypatch.setattr(
        social_ingestion,
        "get_settings",
        lambda: SimpleNamespace(
            apify_x_actor_id="apidojo~tweet-scraper",
        ),
    )

    def blocking_collect(*_args):
        started.set()
        assert finish.wait(timeout=5)
        return []

    monkeypatch.setattr(social_ingestion, "_collect_apify", blocking_collect)
    monkeypatch.setattr(
        social_ingestion,
        "_save_items",
        lambda *_args, **_kwargs: {"created": 0},
    )
    errors = []
    thread = threading.Thread(
        target=lambda: social_ingestion.collect_x(50, None),
    )
    thread.start()
    try:
        assert started.wait(timeout=5)
        with pytest.raises(HTTPException) as caught:
            social_ingestion.collect_x(50, None)
        assert caught.value.status_code == 409
    finally:
        finish.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_apify_rejects_bad_dataset_and_sanitizes_provider_errors(
    ingestion_client,
    monkeypatch,
):
    client, _ = ingestion_client
    _install_settings(monkeypatch)
    monkeypatch.setattr(
        "app.social_ingestion.httpx.post",
        Mock(side_effect=[
            FakeResponse({"items": [_x_item()]}),
            FakeResponse(ValueError("malformed json")),
        ]),
    )
    bad_shape = client.post(
        "/api/ingestion/x",
    )
    assert bad_shape.status_code == 502
    malformed = client.post("/api/ingestion/x")
    assert malformed.status_code == 502
    assert "malformed JSON" in malformed.json()["detail"]
    assert "test-apify-token" not in malformed.text


@pytest.mark.parametrize("token", ["", "   "])
def test_apify_missing_token_fails_safely_without_request(
    ingestion_client,
    monkeypatch,
    token,
):
    client, _ = ingestion_client
    settings = SimpleNamespace(
        apify_api_token=token,
        apify_reddit_actor_id="anyxsolutions~reddit-scraper",
        apify_x_actor_id="apidojo~tweet-scraper",
    )
    monkeypatch.setattr("app.social_ingestion.get_settings", lambda: settings)
    post = Mock()
    monkeypatch.setattr("app.social_ingestion.httpx.post", post)

    response = client.post("/api/ingestion/reddit")

    assert response.status_code == 503
    assert response.json()["detail"] == "Apify API token is not configured."
    assert post.call_count == 0


def test_provider_timeouts_rate_limits_and_request_bounds(
    ingestion_client,
    monkeypatch,
):
    client, _ = ingestion_client
    get = Mock(side_effect=[
        httpx.TimeoutException("timeout"),
        FakeResponse({"hits": []}),
    ])
    monkeypatch.setattr("app.ingestion.httpx.get", get)
    retried = client.post("/api/ingestion/hacker-news")
    assert retried.status_code == 200
    assert get.call_count == 2

    monkeypatch.setattr(
        "app.ingestion.httpx.get",
        Mock(return_value=FakeResponse({}, status_code=429)),
    )
    rate_limited = client.post("/api/ingestion/hacker-news")
    assert rate_limited.status_code == 503

    assert client.post("/api/ingestion/hacker-news?limit=0").status_code == 422
    assert client.post(
        "/api/ingestion/stack-exchange?site=bad%2Fsite"
    ).status_code == 422
    assert client.post("/api/ingestion/reddit?limit=26").status_code == 422


def test_apify_timeout_and_rate_limit_are_controlled(
    ingestion_client,
    monkeypatch,
):
    client, _ = ingestion_client
    _install_settings(monkeypatch)
    monkeypatch.setattr(
        "app.social_ingestion.httpx.post",
        Mock(side_effect=httpx.TimeoutException("timeout")),
    )
    timeout = client.post("/api/ingestion/reddit")
    assert timeout.status_code == 504
    assert "test-apify-token" not in timeout.text

    request = httpx.Request("POST", "https://provider.example")
    response = httpx.Response(429, request=request)
    rate_error = httpx.HTTPStatusError(
        "provider error",
        request=request,
        response=response,
    )
    monkeypatch.setattr(
        "app.social_ingestion.httpx.post",
        Mock(side_effect=rate_error),
    )
    rate_limited = client.post("/api/ingestion/reddit")
    assert rate_limited.status_code == 503
    assert "test-apify-token" not in rate_limited.text
