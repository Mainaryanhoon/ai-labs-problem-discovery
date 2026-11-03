from app.tatvic_scoring import (
    COMPONENT_WEIGHTS,
    compute_demand_strength_1_to_5,
    is_product_ready,
    score_tatvic_opportunity,
)


def test_worked_example_uses_commercial_first_weights():
    assert COMPONENT_WEIGHTS["commercial_signal"] == 35
    assert COMPONENT_WEIGHTS["capability_readiness"] == 10
    assert COMPONENT_WEIGHTS["india_relevance"] == 15
    assert sum(COMPONENT_WEIGHTS.values()) == 100
    # RUBRIC §6.2: weight × (component − 1) ÷ 4
    components = {
        "commercial_signal": 2,
        "demand_strength": 4,
        "india_relevance": 1,
        "section_client_fit": 4,
        "capability_readiness": 4,
        "effort_to_first_version": 3,
        "defensibility": 1,
    }
    expected = round(
        sum(
            COMPONENT_WEIGHTS[key] * (components[key] - 1) / 4
            for key in COMPONENT_WEIGHTS
        ),
        1,
    )

    row = {
        "section": "Analytics",
        "filter_reason_code": "KEEP_DEMAND",
        "problem_statement": "GA4 purchases do not match store orders.",
        "capability_readiness_1_to_5": 4,
        "section_client_fit_1_to_5": 4,
        "leverage_on_offerings_1_to_5": 4,
        "commercial_signal_1_to_5": 2,
        "effort_to_first_version_1_to_5": 3,
        "defensibility_1_to_5": 1,
        "affected_user": "eCommerce analytics lead",
        "smallest_first_version": "BQ Agent add-on that flags GA4 vs order drift",
        "first_client_type": "eCommerce analytics lead",
        "failure_risk_line": "Native GA4 fix may shrink demand",
    }
    analyses = [row] * 25

    class Sub:
        def __init__(self, sid: int, platform: str):
            self.id = sid
            self.submitted_by = f"author{sid}"
            self.source_name = platform
            self.created_at = None

    platforms = ("reddit", "stack_exchange", "hacker_news")
    submissions = [
        Sub(index, platforms[index % 3]) for index in range(25)
    ]
    result = score_tatvic_opportunity(
        eligible_analyses=analyses,
        submissions=submissions,
        source_platforms=set(platforms),
        item_count=25,
        distinct_authors=25,
        records_last_30d=1,
        records_prior_30d=2,
        primary_section="Analytics",
        cluster_title="GA4 purchase reconciliation drift",
    )
    assert result.components["demand_strength"] == 4
    assert result.components["india_relevance"] == 1
    assert result.components["commercial_signal"] == 2
    assert result.score == expected
    assert result.component_contributions["commercial_signal"] > result.component_contributions["capability_readiness"] or (
        result.components["commercial_signal"] >= result.components["capability_readiness"]
    )
    assert result.top10_eligible is True
    assert result.product_ready is True
    assert result.evidence_level == "strong"
    assert result.weight_version == "v0.1"


def test_single_item_generic_etl_is_not_product_ready():
    analyses = [
        {
            "section": "Cloud and Data Engineering",
            "filter_reason_code": "KEEP_DEMAND",
            "problem_statement": "Nested JSON deconstruction in BigQuery",
            "capability_readiness_1_to_5": 4,
            "section_client_fit_1_to_5": 4,
            "leverage_on_offerings_1_to_5": 4,
            "commercial_signal_1_to_5": 2,
            "effort_to_first_version_1_to_5": 3,
            "defensibility_1_to_5": 1,
            "smallest_first_version": "Script to flatten JSON",
            "first_client_type": "data engineer",
            "failure_risk_line": "May already have dbt macros",
        }
    ]
    result = score_tatvic_opportunity(
        eligible_analyses=analyses,
        submissions=[],
        source_platforms={"official_docs"},
        item_count=1,
        distinct_authors=1,
        records_last_30d=0,
        records_prior_30d=1,
        primary_section="Cloud and Data Engineering",
        cluster_title=(
            "Automating the systematic deconstruction of highly nested "
            "JSON data in cloud data warehouses"
        ),
    )
    assert result.product_ready is False
    assert result.score is None


def test_excel_protect_is_non_product():
    from app.tatvic_scoring import is_non_product_problem

    assert is_non_product_problem(
        "User cannot sort/filter protected Excel sheets because ActiveSheet.Protect"
    )


def test_single_source_platform_caps_at_60():
    analyses = [
        {
            "section": "Analytics",
            "filter_reason_code": "KEEP_DEMAND",
            "problem_statement": "GA4 attribution lag for paid media",
            "capability_readiness_1_to_5": 4,
            "section_client_fit_1_to_5": 4,
            "leverage_on_offerings_1_to_5": 4,
            "commercial_signal_1_to_5": 3,
            "effort_to_first_version_1_to_5": 4,
            "defensibility_1_to_5": 3,
            "smallest_first_version": "GA Tag Auditor check for attribution windows",
            "first_client_type": "performance marketer",
            "failure_risk_line": "Native GA4 reports may suffice",
        }
    ] * 5
    result = score_tatvic_opportunity(
        eligible_analyses=analyses,
        submissions=[],
        source_platforms={"reddit"},
        item_count=5,
        distinct_authors=5,
        records_last_30d=5,
        records_prior_30d=1,
        primary_section="Analytics",
        cluster_title="GA4 attribution lag",
    )
    # Still not product_ready without multi-author Top10 bar unless top10...
    # 5 items 5 authors 1 platform → top10_eligible True, single source cap
    assert result.top10_eligible is True
    if result.score is not None:
        assert result.score <= 60
    assert result.single_source_capped is True


def test_demand_strength_anchor_five():
    assert (
        compute_demand_strength_1_to_5(
            item_count=30,
            distinct_source_platforms=3,
            month_over_month_rising=True,
        )
        == 5
    )


def _complete_row(**overrides):
    row = {
        "section": "Analytics",
        "filter_reason_code": "KEEP_DEMAND",
        "problem_statement": (
            "GA4 purchases do not match store orders for India eCommerce "
            "teams in Bengaluru."
        ),
        "capability_readiness_1_to_5": 4,
        "section_client_fit_1_to_5": 4,
        "leverage_on_offerings_1_to_5": 4,
        "commercial_signal_1_to_5": 2,
        "effort_to_first_version_1_to_5": 3,
        "defensibility_1_to_5": 1,
        "affected_user": "eCommerce analytics lead",
        "smallest_first_version": "BQ Agent add-on that flags GA4 vs order drift",
        "first_client_type": "eCommerce analytics lead at an India brand",
        "failure_risk_line": "Native GA4 fix may shrink demand",
    }
    row.update(overrides)
    return row


def _score_rows(rows, *, platforms=("reddit", "stack_exchange", "hacker_news")):
    class Sub:
        def __init__(self, sid: int, platform: str):
            self.id = sid
            self.submitted_by = f"author{sid}"
            self.source_name = platform
            self.created_at = None

    n = max(len(rows), 3)
    submissions = [
        Sub(index, platforms[index % len(platforms)]) for index in range(n)
    ]
    return score_tatvic_opportunity(
        eligible_analyses=rows,
        submissions=submissions,
        source_platforms=set(platforms),
        item_count=n,
        distinct_authors=n,
        records_last_30d=n,
        records_prior_30d=1,
        primary_section="Analytics",
        cluster_title="GA4 purchase reconciliation drift",
    )


def test_missing_qualitative_components_score_one_with_no_evidence():
    """RUBRIC §6.5 / MASTER: absent model components → 1 + 'no evidence'."""
    row = _complete_row()
    for key in (
        "capability_readiness_1_to_5",
        "section_client_fit_1_to_5",
        "leverage_on_offerings_1_to_5",
        "commercial_signal_1_to_5",
        "effort_to_first_version_1_to_5",
        "defensibility_1_to_5",
    ):
        row.pop(key)
    # Keep offering fields so angle logic can still run; missing leverage
    # forces undetermined / not product_ready without loosening gates.
    result = _score_rows([row] * 3)
    assert result.components["capability_readiness"] == 1
    assert result.components["section_client_fit"] == 1
    assert result.components["leverage_on_offerings"] == 1
    assert result.components["commercial_signal"] == 1
    assert result.components["effort_to_first_version"] == 1
    assert result.components["defensibility"] == 1
    assert any("capability_readiness: no evidence" in line for line in result.limitations)
    assert any("effort_to_first_version: no evidence" in line for line in result.limitations)
    assert any("no evidence" in line for line in result.reasons)


def test_explicit_valid_component_scores_are_preserved():
    result = _score_rows([_complete_row()] * 3)
    assert result.components["capability_readiness"] == 4
    assert result.components["section_client_fit"] == 4
    assert result.components["leverage_on_offerings"] == 4
    assert result.components["commercial_signal"] == 2
    assert result.components["effort_to_first_version"] == 3
    assert result.components["defensibility"] == 1
    assert not any("no evidence" in line for line in result.limitations)


def test_invalid_or_malformed_component_scores_treated_as_missing():
    row = _complete_row(
        capability_readiness_1_to_5="high",  # type: ignore[arg-type]
        section_client_fit_1_to_5=0,
        leverage_on_offerings_1_to_5=True,  # type: ignore[arg-type]
        commercial_signal_1_to_5=9,
        effort_to_first_version_1_to_5=None,
        defensibility_1_to_5=3.5,  # type: ignore[arg-type]
    )
    result = _score_rows([row] * 3)
    assert result.components["capability_readiness"] == 1
    assert result.components["section_client_fit"] == 1
    assert result.components["leverage_on_offerings"] == 1
    assert result.components["commercial_signal"] == 1
    assert result.components["effort_to_first_version"] == 1
    assert result.components["defensibility"] == 1
    assert any("effort_to_first_version: no evidence" in line for line in result.limitations)


def test_missing_effort_uses_same_no_evidence_rule_as_other_components():
    row = _complete_row()
    row.pop("effort_to_first_version_1_to_5")
    result = _score_rows([row] * 3)
    assert result.components["effort_to_first_version"] == 1
    assert result.components["capability_readiness"] == 4
    assert any("effort_to_first_version: no evidence" in line for line in result.limitations)
    # Contribution for effort at 1 is 0; other components unchanged vs complete row.
    complete = _score_rows([_complete_row()] * 3)
    assert result.component_contributions["effort_to_first_version"] == 0.0
    assert (
        result.component_contributions["capability_readiness"]
        == complete.component_contributions["capability_readiness"]
    )


def test_complete_inputs_score_and_cap_unchanged():
    """Regression: complete inputs keep commercial-first arithmetic and single-source cap."""
    # Demand 4 (25 items, 3 platforms, not rising).
    class Sub:
        def __init__(self, sid: int, platform: str):
            self.id = sid
            self.submitted_by = f"author{sid}"
            self.source_name = platform
            self.created_at = None

    platforms = ("reddit", "stack_exchange", "hacker_news")
    multi = score_tatvic_opportunity(
        eligible_analyses=[_complete_row()] * 25,
        submissions=[
            Sub(index, platforms[index % 3]) for index in range(25)
        ],
        source_platforms=set(platforms),
        item_count=25,
        distinct_authors=25,
        records_last_30d=1,
        records_prior_30d=2,
        primary_section="Analytics",
        cluster_title="GA4 purchase reconciliation drift",
    )
    assert multi.components["demand_strength"] == 4
    expected = round(
        sum(
            COMPONENT_WEIGHTS[key] * (multi.components[key] - 1) / 4
            for key in COMPONENT_WEIGHTS
        ),
        1,
    )
    assert multi.score == expected
    assert multi.single_source_capped is False

    single = score_tatvic_opportunity(
        eligible_analyses=[_complete_row(
            commercial_signal_1_to_5=3,
            effort_to_first_version_1_to_5=4,
            defensibility_1_to_5=3,
        )] * 5,
        submissions=[],
        source_platforms={"reddit"},
        item_count=5,
        distinct_authors=5,
        records_last_30d=5,
        records_prior_30d=1,
        primary_section="Analytics",
        cluster_title="GA4 attribution lag",
    )
    assert single.single_source_capped is True
    assert single.score is None or single.score <= 60


def test_missing_components_do_not_loosen_product_readiness():
    """Missing scores must not invent leverage/readiness that unlock product_ready."""
    row = _complete_row()
    for key in (
        "capability_readiness_1_to_5",
        "section_client_fit_1_to_5",
        "leverage_on_offerings_1_to_5",
        "commercial_signal_1_to_5",
        "effort_to_first_version_1_to_5",
        "defensibility_1_to_5",
    ):
        row.pop(key)
    result = _score_rows([row] * 3)
    assert result.product_ready is False
    assert result.score is None
    assert result.offering_angle == "undetermined"

    complete = _score_rows([_complete_row()] * 3)
    assert complete.product_ready is True
    assert complete.score is not None


def test_commercial_bypass_skips_known_offering_and_non_product_blockers():
    excel_problem = "Retail founders still reconcile GST in Excel every week"
    common = dict(
        top10_eligible=True,
        offering_angle="new_agent_or_offering",
        smallest_first_version="A weekly GST exception report for D2C brands",
        failure_risk_line="CA firms may already sell a similar spreadsheet",
        keep_demand_count=3,
        title="GST reconciliation for D2C",
        problem_texts=[excel_problem],
        in_scope_section=False,
    )
    blocked, blockers = is_product_ready(
        **common,
        components={
            "leverage_on_offerings": 3,
            "demand_strength": 3,
            "capability_readiness": 3,
            "commercial_signal": 2,
        },
        first_client_type="store operations lead",
    )
    assert blocked is False
    joined = " ".join(blockers)
    assert "known Tatvic offering" in joined
    assert "non-product patterns" in joined

    by_signal, signal_blockers = is_product_ready(
        **common,
        components={
            "leverage_on_offerings": 3,
            "demand_strength": 3,
            "capability_readiness": 3,
            "commercial_signal": 4,
        },
        first_client_type="store operations lead",
    )
    assert by_signal is True
    assert not any("known Tatvic offering" in item for item in signal_blockers)
    assert not any("non-product patterns" in item for item in signal_blockers)

    by_buyer, buyer_blockers = is_product_ready(
        **common,
        components={
            "leverage_on_offerings": 3,
            "demand_strength": 3,
            "capability_readiness": 3,
            "commercial_signal": 2,
        },
        first_client_type="Founder / CMO at an India D2C brand",
    )
    assert by_buyer is True
    assert not any("known Tatvic offering" in item for item in buyer_blockers)
    assert not any("non-product patterns" in item for item in buyer_blockers)


def test_high_commercial_outweighs_low_readiness_and_scores_india():
    row = _complete_row(
        commercial_signal_1_to_5=5,
        capability_readiness_1_to_5=1,
        india_relevance_1_to_5=5,
        first_client_type="Founder at an India D2C brand in Bengaluru",
    )
    result = _score_rows([row] * 3)
    assert result.components["commercial_signal"] == 5
    assert result.components["capability_readiness"] == 1
    assert result.components["india_relevance"] >= 3
    assert result.component_contributions["commercial_signal"] == 35.0
    assert result.component_contributions["capability_readiness"] == 0.0
    assert result.component_contributions["commercial_signal"] > (
        result.component_contributions["capability_readiness"]
        + result.component_contributions["section_client_fit"]
    )
    assert any("Commercial demand outweighs buildability" in line for line in result.reasons)
    assert any("does not cancel a high commercial_signal" in line for line in result.limitations)
    # Model cannot claim India 5 without matching stored-text markers.
    assert result.components["india_relevance"] <= 4


def test_india_claim_cannot_exceed_text_cap():
    row = _complete_row(
        problem_statement="Global chip maker posts quarterly earnings",
        first_client_type="data analyst",
        affected_user="data analyst",
        india_relevance_1_to_5=5,
    )
    result = score_tatvic_opportunity(
        eligible_analyses=[row] * 3,
        submissions=[],
        source_platforms={"reddit", "stack_exchange", "hacker_news"},
        item_count=3,
        distinct_authors=3,
        records_last_30d=3,
        records_prior_30d=1,
        primary_section="Analytics",
        cluster_title="Global chip earnings",
    )
    assert result.components["india_relevance"] == 1
    assert any("india_relevance: no evidence" in line for line in result.limitations)
