"""Tests for the approval boundary — Phase 1 primary invariant.

NO APPLICATION WITHOUT EXPLICIT USER APPROVAL.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.agents.applicant.agent import ApplicantAgent, UnauthorizedApplicationError
from app.agents.resume.agent import ResumeAgent
from app.models.job import JobStatus
from app.services.approval_service import (
    ApprovalNotAllowedError,
    ApprovalService,
)
from app.services.job_service import JobService
from tests.conftest import make_job


class TestApprovalBoundary:
    def test_unapproved_job_cannot_enter_pipeline(
        self,
        job_service: JobService,
        approval_service: ApprovalService,
    ) -> None:
        job = make_job(job_service, status=JobStatus.DISCOVERED)
        assert approval_service.can_enter_application_pipeline(job.id) is False

    def test_recommended_job_cannot_enter_pipeline(
        self,
        job_service: JobService,
        approval_service: ApprovalService,
    ) -> None:
        job = make_job(job_service, status=JobStatus.RECOMMENDED)
        assert approval_service.can_enter_application_pipeline(job.id) is False

    def test_awaiting_approval_without_record_cannot_enter_pipeline(
        self,
        job_service: JobService,
        approval_service: ApprovalService,
    ) -> None:
        job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
        assert approval_service.can_enter_application_pipeline(job.id) is False

    def test_approved_job_with_valid_record_can_enter_pipeline(
        self,
        job_service: JobService,
        approval_service: ApprovalService,
    ) -> None:
        job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
        result = approval_service.approve_job(
            job.id,
            approved_by="tester (123)",
            approval_source="discord",
            approval_action="approve",
        )
        assert result.already_approved is False
        assert result.job.status_enum == JobStatus.APPROVED
        assert result.approval.job_id == job.id
        assert result.approval.approved_by == "tester (123)"
        assert result.approval.approval_source == "discord"
        assert approval_service.can_enter_application_pipeline(job.id) is True

    def test_approval_for_job_a_does_not_authorize_job_b(
        self,
        job_service: JobService,
        approval_service: ApprovalService,
    ) -> None:
        job_a = make_job(job_service, title="Role A", status=JobStatus.AWAITING_APPROVAL)
        job_b = make_job(job_service, title="Role B", status=JobStatus.AWAITING_APPROVAL)

        approval_service.approve_job(job_a.id, approved_by="tester (123)")

        assert approval_service.can_enter_application_pipeline(job_a.id) is True
        assert approval_service.can_enter_application_pipeline(job_b.id) is False

        # Even if somehow B were marked APPROVED without its own record:
        job_b.status = JobStatus.APPROVED.value
        assert approval_service.can_enter_application_pipeline(job_b.id) is False

    def test_duplicate_approvals_handled_safely(
        self,
        job_service: JobService,
        approval_service: ApprovalService,
        session: Session,
    ) -> None:
        job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
        first = approval_service.approve_job(job.id, approved_by="tester (1)")
        session.flush()

        second = approval_service.approve_job(job.id, approved_by="tester (2)")
        assert second.already_approved is True
        assert second.approval.id == first.approval.id
        assert second.approval.approved_by == "tester (1)"

        # Still only one approval row for this job
        assert approval_service.get_approval_for_job(job.id) is not None
        assert len(job.approvals) == 1

    def test_rejected_job_cannot_subsequently_be_approved(
        self,
        job_service: JobService,
        approval_service: ApprovalService,
    ) -> None:
        job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
        approval_service.reject_job(job.id, rejected_by="tester (123)")
        assert job.status_enum == JobStatus.REJECTED

        with pytest.raises(ApprovalNotAllowedError, match="REJECTED"):
            approval_service.approve_job(job.id, approved_by="tester (123)")

        assert approval_service.can_enter_application_pipeline(job.id) is False
        assert approval_service.get_approval_for_job(job.id) is None

    def test_status_approved_without_record_cannot_enter_pipeline(
        self,
        job_service: JobService,
        approval_service: ApprovalService,
    ) -> None:
        """Defense in depth: status alone is insufficient."""
        job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
        # Bypass ApprovalService — corrupt status to simulate bug/misuse
        job.status = JobStatus.APPROVED.value
        assert approval_service.can_enter_application_pipeline(job.id) is False

    def test_applicant_agent_refuses_unapproved_job(
        self,
        job_service: JobService,
        session: Session,
    ) -> None:
        job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
        agent = ApplicantAgent(session)
        with pytest.raises(UnauthorizedApplicationError, match="NO APPLICATION"):
            agent.apply_to_job(job.id)

    def test_applicant_agent_allows_authorized_placeholder(
        self,
        job_service: JobService,
        approval_service: ApprovalService,
        session: Session,
    ) -> None:
        job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
        approval_service.approve_job(job.id, approved_by="tester (123)")
        result = ApplicantAgent(session).apply_to_job(job.id)
        assert result.job_id == job.id
        assert "placeholder" in result.message.lower() or "Phase 1" in result.message

    def test_resume_agent_refuses_unapproved_job(
        self,
        job_service: JobService,
        session: Session,
    ) -> None:
        job = make_job(job_service, status=JobStatus.RECOMMENDED)
        result = ResumeAgent(session).generate_for_job(job.id)
        assert result.success is False
        assert "not authorized" in result.message.lower()

    def test_nonexistent_job_cannot_enter_pipeline(
        self,
        approval_service: ApprovalService,
    ) -> None:
        assert approval_service.can_enter_application_pipeline(999999) is False

    def test_approve_wrong_status_raises(
        self,
        job_service: JobService,
        approval_service: ApprovalService,
    ) -> None:
        job = make_job(job_service, status=JobStatus.SCORED)
        with pytest.raises(ApprovalNotAllowedError, match="AWAITING_APPROVAL"):
            approval_service.approve_job(job.id, approved_by="tester (123)")


class TestFakeRecommendation:
    def test_create_fake_recommendation(self, job_service: JobService) -> None:
        job = job_service.create_fake_recommendation()
        assert job.status_enum == JobStatus.AWAITING_APPROVAL
        assert job.company == "Example Corp"
        assert job.title == "Senior Software Engineer"
        assert job.fit_score == 0.91
        assert job.salary_min == 150000
        assert job.salary_max == 180000
        assert "Java" in (job.recommendation_reason or "")
        assert "Terraform" in (job.recommendation_reason or "")
