from __future__ import annotations

import operator
from enum import Enum
from typing import Annotated, TypedDict

from pydantic import BaseModel, Field


class CIPlatform(str, Enum):
    GITHUB = "github"
    FORGEJO = "forgejo"


class CIStatus(str, Enum):
    FAILED = "FAILED"
    PASSED = "PASSED"
    RUNNING = "RUNNING"
    UNKNOWN = "UNKNOWN"


class LLMAnalysisResponse(BaseModel):
    root_cause: str = Field(description="Short summary of why the CI failed")
    file_path: str = Field(description="Path to the file requiring modification")
    unified_diff: str = Field(description="Unified diff patch to fix the issue")
    explanation: str = Field(description="Detailed explanation of the fix")


class AgentState(TypedDict, total=False):
    # Workflow identifiers
    repository: str
    branch: str
    commit_sha: str
    ci_platform: str
    run_id: str

    # Execution tracking
    attempt_count: int
    ci_status: str

    # Logs and patches
    failed_logs: str
    llm_analysis: str
    patch_diff: str

    # Communication audit
    notifications_sent: Annotated[list[str], operator.add]

    # Source code context for LLM
    source_files: dict[str, str]

    # Internal metadata
    ci_author: str
    failure_summary: str
    patch_summary: str
