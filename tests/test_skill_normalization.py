"""Skill normalization tests — conservative aliases only."""

from __future__ import annotations

from app.agents.scout.skills import normalize_skill, skills_equivalent


def test_aws_alias() -> None:
    assert skills_equivalent("AWS", "Amazon Web Services")
    assert normalize_skill("Amazon Web Services") == "aws"


def test_kubernetes_k8s_alias() -> None:
    assert skills_equivalent("Kubernetes", "K8s")


def test_postgres_alias() -> None:
    assert skills_equivalent("PostgreSQL", "Postgres")


def test_javascript_js_alias() -> None:
    assert skills_equivalent("JavaScript", "JS")


def test_rest_aliases() -> None:
    assert skills_equivalent("REST", "RESTful APIs")
    assert skills_equivalent("REST API Design", "REST APIs")


def test_java_does_not_match_javascript() -> None:
    assert not skills_equivalent("Java", "JavaScript")
    assert normalize_skill("Java") != normalize_skill("JavaScript")


def test_aws_does_not_match_azure() -> None:
    assert not skills_equivalent("AWS", "Azure")


def test_react_does_not_auto_match_react_native() -> None:
    assert not skills_equivalent("React", "React Native")


def test_mongodb_does_not_match_postgresql() -> None:
    assert not skills_equivalent("MongoDB", "PostgreSQL")
