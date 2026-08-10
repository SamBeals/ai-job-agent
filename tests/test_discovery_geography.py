"""Discovery geography + role-family prefilter regression tests."""

from __future__ import annotations

import pytest

from app.agents.discovery.filters import prefilter_candidate
from app.agents.discovery.geography import assess_geography, requires_us_employment
from app.agents.scout.profile_loader import load_candidate_profile
from app.schemas.discovery import RawDiscoveryResult
from app.schemas.preferences import JobPreferences


PROFILE = "data/fixtures/profiles/test_office_backend_prefs.json"


@pytest.fixture()
def profile():
    return load_candidate_profile(PROFILE)


def _raw(**kwargs) -> RawDiscoveryResult:
    base = dict(
        provider="t",
        source_name="t",
        external_id="x",
        title="Backend Software Engineer",
        company="Co",
        salary_min=120000,
        salary_max=150000,
        salary_period="year",
        job_url="https://example.com/j",
        canonical_url="https://example.com/j",
    )
    base.update(kwargs)
    return RawDiscoveryResult(**base)


@pytest.mark.parametrize(
    "location,arrangement,eligible,country",
    [
        ("Canada", None, False, "CA"),
        ("Remote, Canada", "remote", False, "CA"),
        ("Toronto, Canada", None, False, "CA"),
        ("United Kingdom", None, False, "GB"),
        ("Remote - UK", "remote", False, "GB"),
        ("London, England", None, False, "GB"),
        ("Europe", None, False, "EU"),
        ("Berlin, Germany", None, False, "EU"),
        ("India", None, False, "IN"),
        ("Bangalore, India", None, False, "IN"),
        ("Remote - India", "remote", False, "IN"),
        ("Australia", None, False, "AU"),
        ("Sydney, Australia", None, False, "AU"),
        ("Dublin, Ireland", None, False, "IE"),
        ("Dublin", None, False, "IE"),
        ("Remote", "remote", None, "UNKNOWN"),
        ("Fully Remote", "remote", None, "UNKNOWN"),
        ("Remote - US", "remote", True, "US"),
        ("US-Remote", "remote", True, "US"),
        ("Remote (US)", "remote", True, "US"),
        ("US", None, True, "US"),
        (
            "Remote, Canada; Remote, United States",
            "remote",
            True,
            "MULTI",
        ),
        (
            "Remote, Canada; Remote, India; Remote, United Kingdom; Remote, United States",
            "remote",
            True,
            "MULTI",
        ),
        (
            "New York, San Francisco, Seattle, or Remote (US/Canada)",
            "remote",
            True,
            "US",
        ),
        ("Chandler, AZ", "hybrid", True, "US"),
        ("Phoenix, Arizona", "onsite", True, "US"),
        ("Chicago, IL", None, True, "US"),
        ("N/A", None, None, "UNKNOWN"),
        ("", None, None, "UNKNOWN"),
    ],
)
def test_assess_geography_cases(location, arrangement, eligible, country):
    geo = assess_geography(location or None, work_arrangement=arrangement)
    assert geo.us_work_eligible is eligible
    assert geo.normalized_country == country


def test_requires_us_from_preference_and_home(profile):
    assert profile.preferences.us_employment_required is True
    assert requires_us_employment(profile.preferences) is True
    inferred = JobPreferences(home_location="Phoenix Metro, Arizona")
    assert requires_us_employment(inferred) is True
    off = JobPreferences(us_employment_required=False, home_location="Phoenix, Arizona")
    assert requires_us_employment(off) is False
    unknown = JobPreferences(home_location=None, us_employment_required=None)
    assert requires_us_employment(unknown) is False


def test_foreign_hard_rejected_before_ranking(profile):
    for loc in (
        "Canada",
        "Remote, Canada",
        "Bangalore, India",
        "Dublin, Ireland",
        "Remote - India",
        "Toronto, Canada",
        "London, UK",
        "Sydney, Australia",
        "Europe",
    ):
        cand = prefilter_candidate(
            profile,
            _raw(location_text=loc, work_arrangement="remote"),
        )
        assert cand.filtered is True, loc
        assert cand.filter_reason == "FOREIGN_LOCATION", loc
        assert cand.us_work_eligible is False


def test_us_remote_and_multi_us_foreign_accepted(profile):
    for loc in (
        "Remote - US",
        "US-Remote, Chicago, Seattle, San Francisco",
        "Remote, Canada; Remote, United States",
        "New York, San Francisco, Seattle, or Remote (US/Canada)",
        "Chandler, AZ",
        "Phoenix, Arizona",
    ):
        cand = prefilter_candidate(
            profile, _raw(location_text=loc, work_arrangement="remote")
        )
        assert cand.filtered is False, loc
        assert cand.us_work_eligible is True, loc


def test_ambiguous_remote_and_na_not_rejected(profile):
    for loc, arr in (("Remote", "remote"), ("N/A", None), (None, None)):
        cand = prefilter_candidate(
            profile, _raw(location_text=loc, work_arrangement=arr)
        )
        assert cand.filtered is False, loc
        assert cand.us_work_eligible is None, loc
        assert cand.normalized_country == "UNKNOWN"


def test_management_role_filtered(profile):
    cand = prefilter_candidate(
        profile,
        _raw(
            title="Manager, Software Engineering (L4)",
            location_text="Remote - US",
            work_arrangement="remote",
        ),
    )
    assert cand.filtered is True
    assert cand.filter_reason == "MANAGEMENT_ROLE"


def test_ic_software_roles_still_pass(profile):
    for title in (
        "Backend Software Engineer",
        "Principal Software Engineer",
        "Senior Software Engineer",
        "Java Developer",
        "Platform Engineer",
    ):
        cand = prefilter_candidate(
            profile,
            _raw(title=title, location_text="Remote - US", work_arrangement="remote"),
        )
        assert cand.filtered is False, title


def test_live_surfaced_replay_filter_counts(profile):
    """How many of the first live surfaced rows would now be filtered."""
    live = [
        ("Backend Engineer, AI Security", "New York, San Francisco, Seattle, or Remote (US/Canada)", "remote"),
        ("Backend Engineer, Core Technology", "US-Remote, Chicago, Seattle, San Francisco", "remote"),
        ("Backend Engineer (Ruby), AI Engineering: Agent Observability", "Remote, Canada", "remote"),
        ("Intermediate Backend Engineer, Platform Readiness", "Remote, Canada; Remote, United States", "remote"),
        (
            "Intermediate/Senior/Staff Backend Engineer (C), Tenant Scale: Git",
            "Remote, Canada; Remote, India; Remote, United Kingdom; Remote, United States",
            "remote",
        ),
        ("Backend Engineer/API, Payments and Risk", "Dublin, Ireland", None),
        ("Backend Engineer, Billing/Tax", "N/A", None),
        ("Backend Engineer, Core Technology", "Dublin", None),
        ("Backend Engineer, Credit Decisions", "Chicago, IL ", None),
        ("Backend Engineer, Data", "Canada", None),
        ("Backend Engineer, Developer SDKs (Golang)", "N/A", None),
        ("Backend Engineer, Payments and Risk", "US", None),
        ("Backend Engineer, Privy", "NYC-Privy", None),
        ("Intermediate Backend Engineer - Analytics Instrumentation", "Bangalore, India", None),
        ("Intermediate Backend Engineer - Platform Integrations (Monetization)", "Bangalore, India", None),
        ("Manager, Software Engineering (L4)", "Remote - India", "remote"),
        ("Principal Software Engineer", "Remote - US", "remote"),
        ("Principal Software Engineer", "Remote - India", "remote"),
        ("Principal Software Engineer - Identity Graph", "Remote - US", "remote"),
        ("Backend / API Engineer, Metronome (Billing)", "Toronto, Canada", None),
    ]
    filtered = []
    kept = []
    for title, loc, arr in live:
        cand = prefilter_candidate(
            profile,
            _raw(
                title=title,
                location_text=loc,
                work_arrangement=arr,
                external_id=title[:40],
            ),
        )
        if cand.filtered:
            filtered.append((title, loc, cand.filter_reason))
        else:
            kept.append((title, loc, cand.us_work_eligible))
    assert len(live) == 20
    assert len(filtered) + len(kept) == 20
    assert len(filtered) >= 9
    reasons = {r for _t, _l, r in filtered}
    assert "FOREIGN_LOCATION" in reasons
    # Exact expected count for the Phase 3.2 live surfaced set (20 rows)
    assert len(filtered) == 9
    assert len(kept) == 11
    # Management-with-US is covered separately; live Manager row was India → FOREIGN
