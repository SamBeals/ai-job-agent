"""Discovery calibration regressions from live Cloudflare weak-score run."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.agents.discovery.agent import DiscoveryAgent
from app.agents.discovery.filters import prefilter_candidate
from app.agents.discovery.ranking import score_candidate
from app.agents.scout.profile_loader import load_candidate_profile
from app.config import Settings
from app.discord.agent_activity import discovery_completed_embeds
from app.models.discovery import DiscoveryRun
from app.schemas.discovery import DiscoveryRunStatus, RawDiscoveryResult


PROFILE = "data/fixtures/profiles/test_office_backend_prefs.json"
FIXTURE = Path("data/fixtures/discovery/cloudflare_calibration.json")


@pytest.fixture()
def profile():
    return load_candidate_profile(PROFILE)


@pytest.fixture()
def calibration():
    return json.loads(FIXTURE.read_text())


def _settings(**overrides) -> Settings:
    base = dict(
        candidate_profile_path=PROFILE,
        discovery_provider="fake",
        discovery_max_raw_results=100,
        discovery_max_surfaced_results=10,
        discovery_min_surface_score=45,
        llm_provider="mock",
        discord_agent_webhook_url="",
    )
    base.update(overrides)
    return Settings(**base)


def _raw_from_job(job: dict, *, external_id: str | None = None) -> RawDiscoveryResult:
    now = datetime.now(timezone.utc)
    return RawDiscoveryResult(
        provider="calibration",
        source_name="cloudflare_calibration",
        external_id=external_id or job["id"],
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
        published_at=now,
    )


def _evaluate(profile, job: dict):
    raw = _raw_from_job(job)
    cand = prefilter_candidate(profile, raw)
    if cand.filtered:
        return cand
    return score_candidate(profile, cand)


def test_calibration_fixture_expectations(profile, calibration):
    min_score = _settings().discovery_min_surface_score
    live_cloudflare_ids = {
        "cf-director-gtm-finance",
        "cf-dse-analytical-db",
        "cf-dse-analytics-alerts",
        "cf-dse-delivery-db",
        "cf-dse-logs-audit",
        "cf-fde-generic",
        "cf-fde-hebrew",
    }
    surfaced_live = 0

    for job in calibration["jobs"]:
        expect = job["expect"]
        cand = _evaluate(profile, job)

        if expect.get("not_target_software_role"):
            assert cand.filtered or "TARGET_SOFTWARE_ROLE" not in (cand.reason_codes or [])

        if expect.get("filter_reason_any_of"):
            assert cand.filtered is True
            assert cand.filter_reason in expect["filter_reason_any_of"], (
                f"{job['id']}: expected filter in {expect['filter_reason_any_of']}, "
                f"got {cand.filter_reason}"
            )

        if cand.filtered:
            would_surface = False
        else:
            if expect.get("no_local_hybrid_bonus"):
                assert "LOCAL_HYBRID" not in (cand.reason_codes or [])
                assert "HYBRID" not in (cand.reason_codes or []) or (
                    "HYBRID_LOCATION_UNKNOWN" in (cand.reason_codes or [])
                )
            if expect.get("reason_codes_any_of"):
                codes = set(cand.reason_codes or [])
                assert codes.intersection(expect["reason_codes_any_of"]), (
                    f"{job['id']}: missing any of {expect['reason_codes_any_of']} in {codes}"
                )
            if expect.get("max_score_below_threshold"):
                assert cand.discovery_score < min_score, (
                    f"{job['id']}: score {cand.discovery_score} should be < {min_score}"
                )
            would_surface = cand.discovery_score >= min_score

        assert would_surface is expect["surfaces"], (
            f"{job['id']}: surfaces={would_surface} score="
            f"{getattr(cand, 'discovery_score', None)} filtered={cand.filtered} "
            f"reason={cand.filter_reason} codes={cand.reason_codes}"
        )
        if job["id"] in live_cloudflare_ids and would_surface:
            surfaced_live += 1

    assert surfaced_live == 0


def test_director_developer_gtm_not_target_software(profile, calibration):
    job = next(j for j in calibration["jobs"] if j["id"] == "cf-director-gtm-finance")
    cand = _evaluate(profile, job)
    assert cand.filtered is True
    assert cand.filter_reason in {
        "NON_SOFTWARE_DEVELOPER_CONTEXT",
        "MANAGEMENT_ROLE",
        "NON_TARGET_ROLE_FAMILY",
    }
    assert "TARGET_SOFTWARE_ROLE" not in (cand.reason_codes or [])


def test_unknown_location_hybrid_no_local_bonus(profile, calibration):
    job = next(j for j in calibration["jobs"] if j["id"] == "cf-dse-analytical-db")
    cand = _evaluate(profile, job)
    assert cand.filtered is False
    assert "LOCAL_HYBRID" not in cand.reason_codes
    assert "HYBRID_LOCATION_UNKNOWN" in cand.reason_codes or "LOCATION_UNKNOWN" in cand.reason_codes
    assert cand.discovery_score < _settings().discovery_min_surface_score


def test_fde_hebrew_language_constraint(profile, calibration):
    job = next(j for j in calibration["jobs"] if j["id"] == "cf-fde-hebrew")
    cand = _evaluate(profile, job)
    assert cand.filtered is True
    assert cand.filter_reason == "MANDATORY_LANGUAGE_UNMET"
    assert "MANDATORY_LANGUAGE_SIGNAL" in (cand.reason_codes or [])


def test_fde_generic_not_blanket_rejected(profile, calibration):
    job = next(j for j in calibration["jobs"] if j["id"] == "cf-fde-generic")
    cand = _evaluate(profile, job)
    assert cand.filtered is False
    assert "SPECIALIZED_ROLE" in cand.reason_codes
    assert cand.discovery_score < _settings().discovery_min_surface_score


def test_score_discrimination_ladder(profile):
    """Scores must meaningfully separate strong local roles from weak unknowns."""
    cases = [
        (
            "chandler",
            dict(
                title="Backend Software Engineer",
                location_text="Chandler, AZ",
                work_arrangement="hybrid",
                salary_min=125000,
                salary_max=155000,
                salary_period="year",
                description_snippet="Java backend APIs",
            ),
        ),
        (
            "phoenix_java",
            dict(
                title="Java Software Engineer",
                location_text="Phoenix, AZ",
                work_arrangement="hybrid",
                salary_min=120000,
                salary_max=140000,
                salary_period="year",
                description_snippet="Java services",
            ),
        ),
        (
            "us_remote",
            dict(
                title="Backend Engineer",
                location_text="Remote - US",
                work_arrangement="remote",
                salary_min=130000,
                salary_max=160000,
                salary_period="year",
                description_snippet="Java backend",
            ),
        ),
        (
            "unknown_software",
            dict(
                title="Software Engineer",
                location_text="N/A",
                work_arrangement="hybrid",
                description_snippet="General software engineering.",
            ),
        ),
        (
            "specialized_unknown",
            dict(
                title="Distributed Systems Engineer, Analytical Database Platform",
                location_text="N/A",
                work_arrangement="hybrid",
                description_snippet="Analytical database platform.",
            ),
        ),
    ]
    scores: dict[str, int] = {}
    for key, fields in cases:
        job = {
            "id": key,
            "title": fields["title"],
            "company": "Co",
            **fields,
        }
        cand = _evaluate(profile, job)
        assert cand.filtered is False, key
        scores[key] = cand.discovery_score

    assert scores["chandler"] > scores["phoenix_java"] > scores["us_remote"]
    assert scores["us_remote"] >= _settings().discovery_min_surface_score
    assert scores["unknown_software"] < scores["us_remote"]
    assert scores["specialized_unknown"] < scores["unknown_software"]
    assert scores["specialized_unknown"] < _settings().discovery_min_surface_score


def test_max_is_ceiling_not_fill_target(session: Session, profile, calibration):
    """Weak Cloudflare pile must not pad up to MAX when none meet the threshold."""
    settings = _settings(discovery_max_surfaced_results=10, discovery_min_surface_score=45)
    weak = [
        j
        for j in calibration["jobs"]
        if j["id"].startswith("cf-")
    ]

    class WeakOnly:
        name = "weak"

        def search(self, query):  # noqa: ANN001
            return [_raw_from_job(j) for j in weak]

    agent = DiscoveryAgent(session, settings=settings, providers=[WeakOnly()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    session.commit()

    assert result.raw_result_count == len(weak)
    assert result.quality_result_count == 0
    assert result.surfaced_result_count == 0
    assert result.status == DiscoveryRunStatus.COMPLETED.value


def test_controls_still_surface_under_ceiling(session: Session, calibration):
    settings = _settings(discovery_max_surfaced_results=2, discovery_min_surface_score=45)
    controls = [
        j
        for j in calibration["jobs"]
        if j["id"].startswith("control-") and j["expect"]["surfaces"]
    ]
    assert len(controls) >= 3

    class Controls:
        name = "controls"

        def search(self, query):  # noqa: ANN001
            return [_raw_from_job(j) for j in controls]

    agent = DiscoveryAgent(session, settings=settings, providers=[Controls()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    session.commit()

    assert result.quality_result_count >= 3
    # Max is a ceiling: only 2 surfaced even though 3+ qualify
    assert result.surfaced_result_count == 2


def test_foreign_still_rejected(profile, calibration):
    job = next(j for j in calibration["jobs"] if j["id"] == "control-foreign-hybrid")
    cand = _evaluate(profile, job)
    assert cand.filtered is True
    assert cand.filter_reason == "FOREIGN_LOCATION"


def test_completion_copy_zero_and_nonzero():
    zero = discovery_completed_embeds(
        run_id=1,
        work_item_id=2,
        sources_searched=2,
        raw_result_count=500,
        filtered_result_count=40,
        surfaced_result_count=0,
        quality_result_count=0,
        previously_seen_count=0,
    )[0]["description"]
    assert "quality threshold" in zero.lower()
    assert "quality beat volume" not in zero.lower()

    seen = discovery_completed_embeds(
        run_id=1,
        work_item_id=2,
        sources_searched=2,
        raw_result_count=500,
        filtered_result_count=40,
        surfaced_result_count=0,
        quality_result_count=9,
        previously_seen_count=9,
    )[0]["description"]
    assert "already seen" in seen.lower()
    assert "Previously seen:** 9" in seen

    three = discovery_completed_embeds(
        run_id=1,
        work_item_id=2,
        sources_searched=2,
        raw_result_count=500,
        filtered_result_count=40,
        surfaced_result_count=3,
        quality_result_count=3,
        previously_seen_count=0,
    )[0]["description"]
    assert "3** strong new opportunities found" in three
    assert "Passed quality threshold:** 3" in three
