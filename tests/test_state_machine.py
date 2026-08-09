"""Tests for the job status state machine."""

from __future__ import annotations

import pytest

from app.models.job import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    JobStatus,
    can_transition,
    validate_transition,
)
from app.services.job_service import JobService
from tests.conftest import make_job


class TestStateMachine:
    def test_awaiting_approval_can_go_to_approved_structurally(self) -> None:
        assert can_transition(JobStatus.AWAITING_APPROVAL, JobStatus.APPROVED)

    def test_awaiting_approval_can_go_to_rejected(self) -> None:
        assert can_transition(JobStatus.AWAITING_APPROVAL, JobStatus.REJECTED)

    def test_rejected_cannot_go_to_approved(self) -> None:
        assert not can_transition(JobStatus.REJECTED, JobStatus.APPROVED)
        with pytest.raises(InvalidTransitionError):
            validate_transition(JobStatus.REJECTED, JobStatus.APPROVED)

    def test_recommended_cannot_skip_to_approved(self) -> None:
        assert not can_transition(JobStatus.RECOMMENDED, JobStatus.APPROVED)

    def test_discovered_cannot_skip_to_applying(self) -> None:
        assert not can_transition(JobStatus.DISCOVERED, JobStatus.APPLYING)

    def test_job_transition_to_refuses_approved(self, job_service: JobService) -> None:
        job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
        with pytest.raises(PermissionError, match="ApprovalService"):
            job.transition_to(JobStatus.APPROVED)

    def test_job_service_refuses_approved_transition(self, job_service: JobService) -> None:
        job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
        with pytest.raises(PermissionError, match="ApprovalService"):
            job_service.transition(job.id, JobStatus.APPROVED)

    def test_valid_non_approval_transition(self, job_service: JobService) -> None:
        job = make_job(job_service, status=JobStatus.DISCOVERED)
        updated = job_service.transition(job.id, JobStatus.SCORED)
        assert updated.status_enum == JobStatus.SCORED

    def test_invalid_transition_raises(self, job_service: JobService) -> None:
        job = make_job(job_service, status=JobStatus.DISCOVERED)
        with pytest.raises(InvalidTransitionError):
            job_service.transition(job.id, JobStatus.APPLIED)

    def test_every_status_has_transition_entry(self) -> None:
        for status in JobStatus:
            assert status in ALLOWED_TRANSITIONS
