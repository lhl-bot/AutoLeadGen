import os
import pytest

from services.outbound_engine import _build_leadcontact_query_plan

# Real WF#15 parameters (padel clubs in Spain) — the case that returned 0 before.
WF15 = dict(
    keywords="padel club, padel academy, padel court operator",
    target_role="Owner, Founder, CEO, General Manager, Club Manager, Purchasing Manager",
    target_region="Spain",
    product_focus="padel",
)


def test_first_tier_is_specific_and_second_drops_industry():
    plan = _build_leadcontact_query_plan(**WF15)
    assert len(plan) >= 2
    # Tier 1: industry present (specific)
    assert plan[0]["industries"] == ["Sporting Goods"]
    assert plan[0]["locations"] == ["Spain"]
    # Tier 2: industry dropped — the proven recall-killer is no longer mandatory
    assert plan[1]["industries"] is None
    assert any(p["industries"] is None for p in plan)


def test_primary_keyword_is_single_token_not_concatenated():
    plan = _build_leadcontact_query_plan(**WF15)
    # Was previously "padel club  padel academy" (too specific). Now just the first token.
    assert plan[0]["keyword"] == "padel club"
    for p in plan:
        assert p["keyword"] != "padel club  padel academy"


def test_ladder_loosens_titles_then_keyword():
    plan = _build_leadcontact_query_plan(**WF15)
    # Some later tier drops job_titles entirely (region + keyword only).
    assert any(p["job_titles"] is None for p in plan)
    # Broad last resort uses the single distinctive word.
    assert any(p["keyword"] == "padel" for p in plan)


def test_use_industry_env_disables_industry_tier(monkeypatch):
    monkeypatch.setenv("LEADCONTACT_USE_INDUSTRY", "false")
    plan = _build_leadcontact_query_plan(**WF15)
    assert all(p["industries"] is None for p in plan)


def test_custom_industry_map_env(monkeypatch):
    monkeypatch.setenv("LEADCONTACT_INDUSTRY_MAP", '{"padel": "Recreational Facilities"}')
    plan = _build_leadcontact_query_plan(**WF15)
    assert plan[0]["industries"] == ["Recreational Facilities"]


def test_empty_inputs_do_not_crash():
    plan = _build_leadcontact_query_plan("", "", "", "")
    assert isinstance(plan, list) and len(plan) >= 1
    # No location, no keyword — still a valid (empty) attempt rather than an exception.
    assert plan[0]["locations"] is None


def test_plan_has_no_duplicate_attempts():
    plan = _build_leadcontact_query_plan(**WF15)
    seen = []
    for p in plan:
        assert p not in seen
        seen.append(p)


def test_company_size_applied_to_tiers_and_dropped_in_broad_fallback():
    plan = _build_leadcontact_query_plan(**WF15, company_size=["11_50", "51_200"])
    # Filtered tiers carry the size constraint...
    assert plan[0]["company_size"] == ["11_50", "51_200"]
    # ...and the broad single-word fallback drops it (so we still get results).
    assert plan[-1]["company_size"] is None


def test_no_company_size_means_none_everywhere():
    plan = _build_leadcontact_query_plan(**WF15)
    assert all(p["company_size"] is None for p in plan)
