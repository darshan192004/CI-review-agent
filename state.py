from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, TypedDict

from pydantic import BaseModel, Field


class CIPlatform(StrEnum):
    GITHUB = "github"
    FORGEJO = "forgejo"


class CIStatus(StrEnum):
    FAILED = "FAILED"
    PASSED = "PASSED"
    RUNNING = "RUNNING"
    EXHAUSTED = "EXHAUSTED"
    CANNOT_FIX = "CANNOT_FIX"
    UNKNOWN = "UNKNOWN"


class FileFix(BaseModel):
    file_path: str = Field(description="Relative path of the file to update.")
    content: str = Field(description="The complete updated file content.")


class RepairAnalysis(BaseModel):
    explanation: str = Field(description="Root cause analysis of the CI failure.")
    modified_files: list[FileFix] = Field(description="List of files to overwrite with corrected content.")


class LLMAnalysisResponse(BaseModel):
    """Legacy compatibility wrapper — maps to RepairAnalysis for existing callers."""

    root_cause: str = Field(description="Short summary of why the CI failed")
    file_path: str = Field(description="Path to the file requiring modification")
    unified_diff: str = Field(description="Unified diff patch to fix the issue (deprecated)")
    explanation: str = Field(description="Detailed explanation of the fix")


class AgentState(TypedDict, total=False):
    # Workflow identifiers
    repository: str
    branch: str
    commit_sha: str
    ci_platform: str
    run_id: str
    run_attempt: str

    # Execution tracking
    attempt_count: int
    ci_status: str

    # Logs and analysis
    failed_logs: str
    llm_analysis: str

    # Prior attempt analysis persisted in the session row (externalized retry loop)
    previous_context: str

    # Structured fix output
    explanation: str
    patch_applied: bool

    # Communication audit
    notifications_sent: Annotated[list[str], operator.add]

    # Source code context for LLM
    source_files: dict[str, str]

    # Repo context for workspace git manager
    repo_info: dict[str, str]

    # Internal metadata
    ci_author: str
    commit_author: str
    commit_author_email: str
    failure_summary: str
    patch_summary: str
    session_id: int

    # Workspace/clone state (for bot identification and infrastructure errors)
    workspace_dir: str
    clone_error: str
    clone_url: str
