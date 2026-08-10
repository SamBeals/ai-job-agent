"""Geographic viability calibration — Run #5 regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.discovery.filters import prefilter_candidate
from app.agents.discovery.geography import assess_geography
from app.agents.discovery.ranking import score_candidate
from app.agents.discovery.viability import assess_viability
from app.agents.scout.profile_loader import load_candidate_profile
from app.config import Settings
from app.schemas.discovery import RankedDiscoveryCandidate, RawDiscoveryResult


PROFILE = "data/fixtures/profiles/test_office_backend_prefs.json"
FIXTURE = Path("data/fixtures/discovery/run5_geo_calibration.json")


@pytest.fixture()
def profile():
    return load_candidate_profile(PROFILE)


@pytest.fixture()
def calibration():
    return json.loads(FIXTURE.read_text())


def _settings() -> Settings:
    return Settings(
        candidate_profile_path=PROFILE,
        discovery_min_surface_score=45,
        llm_provider="mock",
    )


def _raw_from_job(job: dict) -> RawDiscoveryResult:
    return RawDiscoveryResult(
        provider="calibration",
        source_name="run5",
        external_id=job["id"],
        title=job["title"],
        company=job["company"],
        location_text=job.get("location_text"),
        work_arrangement=job.get("work_arrangement"),
        salary_min=job.get("salary_min"),
        salary_max=job.get("salary_max"),
        salary_period=job.get("salary_period"),
        description_snippet=job.get("description_snippet"),
        job_url=f"https://example.com/jobs/{job['id']}",
        canonical_url=f"https://example.com/jobs/{job['id']}",
    )


def _evaluate(profile, job: dict):
    raw = _raw_from_job(job)
    cand = prefilter_candidate(profile, raw)
    if cand.filtered:
        return cand
    return score_candidate(profile, cand)


def test_run5_calibration_fixture(profile, calibration):
    min_score = _settings().discovery_min_surface_score
    for job in calibration["jobs"]:
        expect = job["expect"]
        cand = _evaluate(profile, job)
        if expect.get("filter_reason_any_of"):
            assert cand.filtered is True
            assert cand.filter_reason in expect["filter_reason_any_of"], job["id"]
            would = False
        else:
            assert cand.filtered is False, job["id"]
            if expect.get("max_score_below_threshold"):
                assert cand.discovery_score < min_score, job["id"]
            if expect.get("reason_codes_any_of"):
                assert set(cand.reason_codes or {}).intersection(
                    expect["reason_codes_any_of"]
                ), job["id"]
            would = cand.discovery_score >= min_score
        assert would is expect["surfaces"], (
            f"{job['id']}: surfaces={would} score={getattr(cand,'discovery_score',None)} "
            f"filtered={cand.filtered} reason={cand.filter_reason}"
        )


def test_colombia_foreign_root_cause(profile):
    assert assess_geography("Colombia").us_work_eligible is False
    cand = _evaluate(
        profile,
        {
            "id": "co",
            "title": "Senior Backend Software Engineer",
            "company": "GoDaddy",
            "location_text": "Colombia",
        },
    )
    assert cand.filtered and cand.filter_reason == "FOREIGN_LOCATION"


def test_nonlocal_onsite_and_hybrid_rejected(profile):
    for loc, arr, reason in [
        ("New York, NY", "onsite", "NONLOCAL_ONSITE"),
        ("New York, NY", "hybrid", "NONLOCAL_HYBRID"),
        ("Palo Alto, CA", "onsite", "NONLOCAL_ONSITE"),
        ("Washington DC", "onsite", "NONLOCAL_ONSITE"),
    ]:
        cand = _evaluate(
            profile,
            {
                "id": loc,
                "title": "Backend Software Engineer",
                "company": "Palantir",
                "location_text": loc,
                "work_arrangement": arr,
                "description_snippet": "Backend Java",
            },
        )
        assert cand.filtered is True
        assert cand.filter_reason == reason


def test_springdale_unknown_arrangement_rejected(profile):
    cand = _evaluate(
        profile,
        {
            "id": "ar",
            "title": "Staff Software Engineer",
            "company": "Walmart",
            "location_text": "Springdale, AR",
            "work_arrangement": None,
            "description_snippet": "Java backend",
        },
    )
    assert cand.filtered is True
    assert cand.filter_reason == "NONLOCAL_PHYSICAL_UNKNOWN"


def test_dallas_verified_us_remote_eligible(profile):
    cand = _evaluate(
        profile,
        {
            "id": "dal",
            "title": "Senior DevSecOps Platform Engineer, AI Automation",
            "company": "Equinix",
            "location_text": "Dallas, TX, Flexible / Remote",
            "work_arrangement": "remote",
            "description_snippet": "Platform",
        },
    )
    assert cand.filtered is False
    assert cand.discovery_score >= 45
    assert "US_REMOTE" in cand.reason_codes


def test_metro_cities_recognized(profile):
    for city in ("Chandler", "Phoenix", "Tempe", "Mesa", "Gilbert", "Scottsdale"):
        v = assess_viability(
            profile.preferences,
            location_text=f"{city}, AZ",
            work_arrangement="hybrid",
        )
        assert v.in_preferred_metro is True, city
        assert v.filter_reason is None


def test_score_hierarchy(profile):
    def score(**kw):
        raw = RawDiscoveryResult(
            provider="t",
            source_name="t",
            external_id="x",
            company="Co",
            job_url="https://e/x",
            canonical_url="https://e/x",
            **kw,
        )
        return score_candidate(profile, prefilter_candidate(profile, raw)).discovery_score

    chandler = score(
        title="Backend Software Engineer",
        location_text="Chandler, AZ",
        work_arrangement="hybrid",
        salary_min=130000,
        salary_max=160000,
        salary_period="year",
        description_snippet="Java backend",
    )
    phoenix = score(
        title="Backend Software Engineer",
        location_text="Phoenix, AZ",
        work_arrangement="onsite",
        salary_min=130000,
        salary_max=160000,
        salary_period="year",
        description_snippet="Java backend",
    )
    us_remote = score(
        title="Backend Engineer",
        location_text="Remote - US",
        work_arrangement="remote",
        salary_min=130000,
        salary_max=160000,
        salary_period="year",
        description_snippet="Java backend",
    )
    unknown_remote = score(
        title="Backend Engineer",
        location_text="Flexible / Remote",
        work_arrangement="remote",
        description_snippet="Java backend",
    )
    assert chandler > phoenix > us_remote > unknown_remote
    assert unknown_remote < 45
    assert us_remote >= 45


def test_foreign_remote_not_us_remote(profile):
    cand = prefilter_candidate(
        profile,
        RawDiscoveryResult(
            provider="t",
            source_name="t",
            external_id="x",
            title="Backend Software Engineer",
            company="Euro",
            location_text="Remote - Germany",
            work_arrangement="remote",
            job_url="https://e/x",
            canonical_url="https://e/x",
        ),
    )
    assert cand.filtered is True
    assert cand.filter_reason == "FOREIGN_LOCATION"


def test_unknown_remote_stays_unknown(profile):
    v = assess_viability(
        profile.preferences,
        location_text="Flexible / Remote",
        work_arrangement="remote",
    )
    assert v.us_work_eligible is None
    assert v.remote_eligibility_unknown is True
    assert v.remote_us_eligible is False


def test_relocation_true_allows_nonlocal(profile):
    moved = profile.model_copy(deep=True)
    moved.preferences = profile.preferences.model_copy(update={"relocation_allowed": True})
    cand = prefilter_candidate(
        moved,
        RawDiscoveryResult(
            provider="t",
            source_name="t",
            external_id="x",
            title="Backend Software Engineer",
            company="Palantir",
            location_text="New York, NY",
            work_arrangement="hybrid",
            description_snippet="Java backend",
            salary_min=140000,
            salary_max=180000,
            salary_period="year",
            job_url="https://e/x",
            canonical_url="https://e/x",
        ),
    )
    assert cand.filtered is False
    scored = score_candidate(moved, cand)
    assert scored.discovery_score >= 45


def test_salary_floor_and_unknown_salary_local(profile):
    low = prefilter_candidate(
        profile,
        RawDiscoveryResult(
            provider="t",
            source_name="t",
            external_id="low",
            title="Backend Software Engineer",
            company="Cheap",
            location_text="Chandler, AZ",
            work_arrangement="hybrid",
            salary_min=70000,
            salary_max=90000,
            salary_period="year",
            job_url="https://e/low",
            canonical_url="https://e/low",
        ),
    )
    assert low.filtered is True
    assert low.filter_reason == "SALARY_BELOW_MINIMUM"

    unknown = _evaluate(
        profile,
        {
            "id": "unk",
            "title": "Backend Software Engineer",
            "company": "Opaque",
            "location_text": "Tempe, AZ",
            "work_arrangement": "hybrid",
            "description_snippet": "Java backend",
        },
    )
    assert unknown.filtered is False
    assert unknown.discovery_score >= 45
    assert "SALARY_UNKNOWN" in unknown.reason_codes


def test_min_surface_score_unchanged():
    assert _settings().discovery_min_surface_score == 45


def test_ca_only_remote_rejected(profile):
    cand = prefilter_candidate(
        profile,
        RawDiscoveryResult(
            provider="t",
            source_name="t",
            external_id="ca",
            title="Backend Software Engineer",
            company="Bay",
            location_text="Remote - California only",
            work_arrangement="remote",
            job_url="https://e/ca",
            canonical_url="https://e/ca",
        ),
    )
    assert cand.filtered is True
    assert cand.filter_reason == "REMOTE_REGION_INCOMPATIBLE"
