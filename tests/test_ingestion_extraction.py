"""Extraction / salary / text ingestion tests — no network, no paid LLM."""

from __future__ import annotations

from app.agents.scout.ingestion.html_extract import extract_from_html
from app.agents.scout.ingestion.salary import parse_salary
from app.agents.scout.ingestion.service import JobIngestionService
from app.agents.scout.ingestion.text_parser import extract_from_text


JSON_LD_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Senior Backend Engineer",
  "description": "Build Java APIs. <b>Spring Boot</b>",
  "datePosted": "2026-01-15",
  "employmentType": "FULL_TIME",
  "hiringOrganization": {"@type": "Organization", "name": "Acme Corp"},
  "jobLocation": {
    "@type": "Place",
    "address": {
      "@type": "PostalAddress",
      "addressLocality": "Chandler",
      "addressRegion": "AZ",
      "addressCountry": "US"
    }
  },
  "baseSalary": {
    "@type": "MonetaryAmount",
    "currency": "USD",
    "value": {"@type": "QuantitativeValue", "minValue": 140000, "maxValue": 165000, "unitText": "YEAR"}
  }
}
</script>
</head><body><nav>Menu</nav><p>Ignored</p></body></html>
"""


def test_json_ld_extracts_title_company_location() -> None:
    raw = extract_from_html(JSON_LD_HTML)
    assert raw.method == "JSON_LD"
    assert raw.title == "Senior Backend Engineer"
    assert raw.company == "Acme Corp"
    assert raw.location and "Chandler" in raw.location


def test_json_ld_salary_range() -> None:
    raw = extract_from_html(JSON_LD_HTML)
    assert raw.salary_min == 140000
    assert raw.salary_max == 165000
    assert raw.salary_currency == "USD"


def test_generic_html_extraction_returns_text() -> None:
    html = """
    <html><body>
    <h1>Software Engineer</h1>
    <p>Location: Tempe, AZ</p>
    <p>Hybrid role building backend services.</p>
    <p>Salary: $120,000 - $150,000</p>
    </body></html>
    """
    raw = extract_from_html(html)
    assert raw.title
    assert raw.description and "backend" in raw.description.lower()
    assert raw.salary_min == 120000
    assert raw.salary_max == 150000


def test_malformed_html_fails_gracefully() -> None:
    raw = extract_from_html("<html><boogers><<<>>>>")
    # Should not raise
    assert raw is not None


def test_empty_page_warns() -> None:
    raw = extract_from_html("<html><body></body></html>")
    assert raw.warnings


def test_pasted_text_produces_fields() -> None:
    text = """
Senior Software Engineer
Example Corp
Chandler, AZ
Hybrid

Salary: $135,000-$165,000

Requirements:
- Java
- Spring Boot
- REST APIs

Preferred skills:
- Kafka

Responsibilities:
- Design and implement backend services
"""
    raw = extract_from_text(text)
    assert raw.title and "Software Engineer" in raw.title
    assert raw.company and "Example" in raw.company
    assert raw.remote_status == "hybrid"
    assert raw.salary_min == 135000
    assert raw.salary_max == 165000
    assert "Java" in raw.required_skills
    assert any("Kafka" in s for s in raw.preferred_skills)


def test_unknown_salary_remains_unknown() -> None:
    raw = extract_from_text("Software Engineer\nAcme\nBuild things with Java.")
    assert raw.salary_min is None
    assert raw.salary_max is None


def test_unknown_remote_remains_unknown() -> None:
    raw = extract_from_text("Software Engineer\nAcme\nBuild backend services in Java.")
    assert raw.remote_status is None


def test_salary_120k_150k() -> None:
    parsed = parse_salary("Compensation: $120k-$150k")
    assert parsed.annual_min == 120000
    assert parsed.annual_max == 150000


def test_salary_comma_range() -> None:
    parsed = parse_salary("Base salary range: $120,000 - $150,000")
    assert parsed.annual_min == 120000
    assert parsed.annual_max == 150000


def test_hourly_not_treated_as_annual() -> None:
    parsed = parse_salary("Pay: $65/hour")
    assert parsed.is_hourly is True
    assert parsed.annual_min is None
    assert parsed.annual_max is None


def test_ingest_text_service_normalized_job() -> None:
    result = JobIngestionService().ingest_text(
        "Backend Software Engineer\nNorthwind\nRemote\nSalary: $150,000-$170,000\n"
        "Requirements:\n- Java\n- Spring Boot\n"
    )
    job = result.normalized_job
    assert job.title
    assert job.company
    assert job.salary_min == 150000
    assert job.remote_status == "remote"
