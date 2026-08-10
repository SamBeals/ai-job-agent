"""Deterministic fake DiscoveryProvider for tests — never hits the network."""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.discovery import DiscoveryQuery, RawDiscoveryResult


class FakeDiscoveryProvider:
    """Returns a fixed set of opportunities covering ranking/filter cases."""

    name = "fake"

    def search(self, query: DiscoveryQuery) -> list[RawDiscoveryResult]:
        now = datetime.now(timezone.utc)
        return [
            RawDiscoveryResult(
                provider=self.name,
                source_name="fake",
                external_id="fake-chandler-backend",
                title="Backend Software Engineer",
                company="Desert Systems",
                location_text="Chandler, AZ",
                work_arrangement="hybrid",
                salary_min=125000,
                salary_max=155000,
                salary_currency="USD",
                salary_period="year",
                description_snippet="Build Java/Spring Boot services and REST APIs.",
                description_full=(
                    "Backend Software Engineer — build Java services, REST APIs, "
                    "SQL, AWS. Hybrid in Chandler, AZ. Own services end-to-end, "
                    "collaborate with product, write tests, and ship reliably. "
                    "Required: Java, Spring Boot, SQL, cloud experience. "
                    "This posting includes enough structured content for Scout "
                    "evaluation without fetching the employer URL."
                ),
                job_url="https://example.com/jobs/fake-chandler-backend",
                canonical_url="https://example.com/jobs/fake-chandler-backend",
                published_at=now,
            ),
            RawDiscoveryResult(
                provider=self.name,
                source_name="fake",
                external_id="fake-phoenix-hybrid",
                title="Software Engineer",
                company="Valley Platform",
                location_text="Phoenix, AZ",
                work_arrangement="hybrid",
                salary_min=120000,
                salary_max=140000,
                salary_currency="USD",
                salary_period="year",
                description_snippet="Develop backend services for product teams.",
                job_url="https://example.com/jobs/fake-phoenix-hybrid",
                canonical_url="https://example.com/jobs/fake-phoenix-hybrid",
                published_at=now,
            ),
            RawDiscoveryResult(
                provider=self.name,
                source_name="fake",
                external_id="fake-remote-backend",
                title="Backend Engineer",
                company="Cloud Harbor",
                location_text="Remote - US",
                work_arrangement="remote",
                salary_min=130000,
                salary_max=160000,
                salary_currency="USD",
                salary_period="year",
                description_snippet="Remote backend Java/Kotlin services.",
                job_url="https://example.com/jobs/fake-remote-backend",
                canonical_url="https://example.com/jobs/fake-remote-backend",
                published_at=now,
            ),
            RawDiscoveryResult(
                provider=self.name,
                source_name="fake",
                external_id="fake-low-salary",
                title="Junior Backend Developer",
                company="Budget Soft",
                location_text="Tempe, AZ",
                work_arrangement="onsite",
                salary_min=70000,
                salary_max=90000,
                salary_currency="USD",
                salary_period="year",
                description_snippet="Entry backend role.",
                job_url="https://example.com/jobs/fake-low-salary",
                canonical_url="https://example.com/jobs/fake-low-salary",
                published_at=now,
            ),
            RawDiscoveryResult(
                provider=self.name,
                source_name="fake",
                external_id="fake-frontend-only",
                title="Frontend Engineer",
                company="Pixel Labs",
                location_text="Scottsdale, AZ",
                work_arrangement="hybrid",
                salary_min=130000,
                salary_max=150000,
                salary_currency="USD",
                salary_period="year",
                description_snippet="React/CSS UI only. No backend.",
                job_url="https://example.com/jobs/fake-frontend-only",
                canonical_url="https://example.com/jobs/fake-frontend-only",
                published_at=now,
            ),
            RawDiscoveryResult(
                provider=self.name,
                source_name="fake",
                external_id="fake-duplicate-url",
                title="Backend Software Engineer",
                company="Desert Systems",
                location_text="Chandler, AZ",
                work_arrangement="hybrid",
                salary_min=125000,
                salary_max=155000,
                salary_currency="USD",
                salary_period="year",
                description_snippet="Duplicate of Chandler backend.",
                # Same canonical URL as first — should dedupe
                job_url="https://example.com/jobs/fake-chandler-backend",
                canonical_url="https://example.com/jobs/fake-chandler-backend",
                published_at=now,
            ),
            RawDiscoveryResult(
                provider=self.name,
                source_name="fake",
                external_id="fake-missing-salary",
                title="Software Engineer",
                company="Opaque Co",
                location_text="Mesa, AZ",
                work_arrangement="onsite",
                salary_min=None,
                salary_max=None,
                description_snippet="Full-stack leaning backend. Salary not listed.",
                description_full="Software Engineer building APIs and services. Salary TBD.",
                job_url="https://example.com/jobs/fake-missing-salary",
                canonical_url="https://example.com/jobs/fake-missing-salary",
                published_at=now,
            ),
            RawDiscoveryResult(
                provider=self.name,
                source_name="fake",
                external_id="fake-helpdesk",
                title="Help Desk Technician",
                company="Support Desk Inc",
                location_text="Phoenix, AZ",
                work_arrangement="onsite",
                salary_min=55000,
                salary_max=65000,
                salary_currency="USD",
                salary_period="year",
                description_snippet="Password resets and ticket triage.",
                job_url="https://example.com/jobs/fake-helpdesk",
                canonical_url="https://example.com/jobs/fake-helpdesk",
                published_at=now,
            ),
        ]
