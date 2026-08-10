"""Agent vocabulary and pipeline domain enums — shared by models and services."""

from __future__ import annotations

from enum import Enum


class AgentType(str, Enum):
    """Narrow agent identities. Not every agent is implemented yet."""

    SCOUT = "SCOUT"
    RESUME = "RESUME"
    RESUME_REVIEW = "RESUME_REVIEW"
    APPLICANT = "APPLICANT"
    TRACKER = "TRACKER"
    DISCOVERY = "DISCOVERY"


class AgentCapability(str, Enum):
    """High-level capability labels for documentation / status surfaces."""

    EVALUATE_JOBS = "EVALUATE_JOBS"
    CREATE_SCOUT_EVALUATION = "CREATE_SCOUT_EVALUATION"
    RECOMMEND_JOBS = "RECOMMEND_JOBS"
    BUILD_RESUME_PLAN = "BUILD_RESUME_PLAN"
    GENERATE_RESUME = "GENERATE_RESUME"
    REVIEW_RESUME = "REVIEW_RESUME"
    PREPARE_APPLICATION = "PREPARE_APPLICATION"
    SUBMIT_APPLICATION = "SUBMIT_APPLICATION"
    TRACK_OUTCOMES = "TRACK_OUTCOMES"
    DISCOVER_JOBS = "DISCOVER_JOBS"


# Explicit permission matrix (documentation + runtime checks where useful).
AGENT_PERMISSIONS: dict[AgentType, dict[str, set[AgentCapability]]] = {
    AgentType.SCOUT: {
        "may": {
            AgentCapability.EVALUATE_JOBS,
            AgentCapability.CREATE_SCOUT_EVALUATION,
            AgentCapability.RECOMMEND_JOBS,
        },
        "may_not": {
            AgentCapability.SUBMIT_APPLICATION,
            AgentCapability.GENERATE_RESUME,
        },
    },
    AgentType.RESUME: {
        "may": {
            AgentCapability.BUILD_RESUME_PLAN,
            AgentCapability.GENERATE_RESUME,
        },
        "may_not": {
            AgentCapability.SUBMIT_APPLICATION,
            AgentCapability.DISCOVER_JOBS,
        },
    },
    AgentType.RESUME_REVIEW: {
        "may": {AgentCapability.REVIEW_RESUME},
        "may_not": {AgentCapability.SUBMIT_APPLICATION},
    },
    AgentType.APPLICANT: {
        "may": {
            AgentCapability.PREPARE_APPLICATION,
            AgentCapability.SUBMIT_APPLICATION,
        },
        "may_not": {AgentCapability.DISCOVER_JOBS},
    },
    AgentType.TRACKER: {
        "may": {AgentCapability.TRACK_OUTCOMES},
        "may_not": {AgentCapability.SUBMIT_APPLICATION},
    },
    AgentType.DISCOVERY: {
        "may": {AgentCapability.DISCOVER_JOBS},
        "may_not": {AgentCapability.SUBMIT_APPLICATION},
    },
}


class PipelineStatus(str, Enum):
    """ApplicationPipeline lifecycle — separate from Job opportunity status.

    Job = opportunity. ApplicationPipeline = our attempt to apply.
    Gate 1 (preparation Approval) creates this pipeline.
    Gate 2 (SubmissionAuthorization) is required before submission — not implemented yet.
    """

    PREPARATION_QUEUED = "PREPARATION_QUEUED"
    RESUME_PLANNING = "RESUME_PLANNING"
    RESUME_PLAN_READY = "RESUME_PLAN_READY"
    # Future stages (not advanced in this phase):
    RESUME_REVIEWING = "RESUME_REVIEWING"
    APPLICATION_READY = "APPLICATION_READY"
    SUBMISSION_APPROVED = "SUBMISSION_APPROVED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkItemStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class WorkItemTaskType(str, Enum):
    BUILD_RESUME_PLAN = "BUILD_RESUME_PLAN"
    # Future:
    GENERATE_RESUME_DOCUMENT = "GENERATE_RESUME_DOCUMENT"
    REVIEW_RESUME = "REVIEW_RESUME"
    PREPARE_APPLICATION = "PREPARE_APPLICATION"
    SUBMIT_APPLICATION = "SUBMIT_APPLICATION"


# Agents that are operational vs informational placeholders in Discord /agents
IMPLEMENTED_AGENTS: set[AgentType] = {AgentType.SCOUT, AgentType.RESUME}
