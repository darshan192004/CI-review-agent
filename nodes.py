from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from config import settings
from services.ci_client import CIClient, create_ci_client
from services.git_manager import GitManager
from services.mcp_client import MCPClient
from state import AgentState, LLMAnalysisResponse

logger = logging.getLogger(__name__)


def _get_llm() -> Any:
    if settings.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
        )
    if settings.llm_provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.gemini_api_key,
        )
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
    )


def _build_ci_client(state: AgentState) -> CIClient:
    platform = state.get("ci_platform", "github")
    if platform == "github":
        return create_ci_client("github", settings.github_token, "")
    return create_ci_client(
        "forgejo", settings.forgejo_token, settings.forgejo_base_url
    )


def _build_mcp_client() -> MCPClient | None:
    if not settings.mcp_server_command:
        logger.warning("MCP server command not configured; notifications disabled")
        return None
    return MCPClient(
        command=settings.mcp_server_command,
        args=settings.mcp_server_args,
        env=settings.mcp_server_env_with_webhooks or None,
    )


async def node_fetch_logs_and_alert(state: AgentState) -> dict[str, Any]:
    logger.info("Fetching CI logs for run %s", state.get("run_id"))
    ci_client = _build_ci_client(state)
    mcp_client = _build_mcp_client()

    try:
        repo = state.get("repository", "")
        parts = repo.split("/")
        owner, repo_name = parts[0], parts[1]
        run_id = state.get("run_id", "")

        failed_logs = await ci_client.fetch_logs(owner, repo_name, run_id)

        run_info = await ci_client.get_run_info(owner, repo_name, run_id)
        author = run_info.get("actor", {}).get("login", "unknown")
        head_branch = run_info.get("head_branch", state.get("branch", "unknown"))
        head_sha = run_info.get("head_sha", state.get("commit_sha", "unknown"))

        failure_summary = _extract_failure_summary(failed_logs)

        if mcp_client:
            try:
                await mcp_client.connect()
                alert_payload = {
                    "platform": settings.messaging_platform,
                    "incident_title": f"CI Failed: {repo} (branch: {head_branch})",
                    "root_cause": failure_summary,
                    "resolution_steps": (
                        f"Automated fix attempt 1/{settings.max_retry_attempts} in progress.\n"
                        f"Repository: {repo}\n"
                        f"Branch: {head_branch}\n"
                        f"Commit: {head_sha}\n"
                        f"Author: {author}\n"
                        f"Run ID: {run_id}"
                    ),
                }
                await mcp_client.send_alert(alert_payload)
            except Exception as e:
                logger.error("MCP alert failed (non-fatal): %s", e)
            finally:
                await mcp_client.disconnect()

        return {
            "failed_logs": failed_logs,
            "attempt_count": 0,
            "ci_author": author,
            "failure_summary": failure_summary,
            "commit_sha": head_sha,
            "branch": head_branch,
            "notifications_sent": [f"Initial alert sent for run {run_id}"],
        }
    except Exception as e:
        logger.error("Failed to fetch logs: %s", e)
        return {
            "failed_logs": f"Error fetching logs: {e}",
            "attempt_count": 0,
            "failure_summary": f"Log fetch error: {e}",
            "notifications_sent": [],
        }
    finally:
        await ci_client.close()


async def node_llm_fix_code(state: AgentState) -> dict[str, Any]:
    attempt = state.get("attempt_count", 1)
    logger.info("Analyzing failure and generating fix (attempt %d)", attempt)

    source_files = state.get("source_files", {})
    source_context = ""
    for path, content in source_files.items():
        source_context += f"\n--- File: {path} ---\n{content}\n"

    previous_context = ""
    if attempt > 1:
        previous_context = (
            f"\nPrevious attempt #{attempt - 1} failed.\n"
            f"Previous analysis: {state.get('llm_analysis', 'N/A')}\n"
            "Please try a different approach.\n"
        )

    system_prompt = """You are an expert CI/CD fix agent. Given failing CI logs and source code,
analyze the root cause and provide a unified diff patch to fix the issue.

Return ONLY valid JSON with this exact structure:
{
    "root_cause": "Short summary of why CI failed",
    "file_path": "path/to/file/to/fix",
    "unified_diff": "--- a/file.py\\n+++ b/file.py\\n@@ -1,5 +1,5 @@\\n ...",
    "explanation": "Detailed explanation of the fix"
}

IMPORTANT: The unified_diff must be a valid unified diff format. Include context lines.
Only fix the actual issue - do not make unrelated changes."""

    user_prompt = f"""Repository: {state.get("repository", "unknown")}
Branch: {state.get("branch", "unknown")}
Attempt: {attempt}/{settings.max_retry_attempts}

FAILING CI LOGS:
{state.get("failed_logs", "No logs available")}

SOURCE CODE:
{source_context}
{previous_context}

Analyze the failure and provide a fix as JSON."""

    llm = _get_llm()
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        response = await llm.ainvoke(messages)
        content = response.content

        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            raise ValueError("No JSON found in LLM response")

        parsed = json.loads(json_match.group())
        analysis = LLMAnalysisResponse(**parsed)

        git_manager = GitManager(settings.git_repo_path)
        await git_manager.reset_clean()

        patch_applied = await git_manager.apply_patch(analysis.unified_diff)
        if not patch_applied:
            logger.warning("Patch failed to apply, skipping commit/push")
            return {
                "llm_analysis": analysis.root_cause,
                "patch_diff": "",
                "attempt_count": attempt + 1,
                "notifications_sent": ["Patch failed to apply"],
            }

        commit_msg = f"fix(ci): automated patch attempt {attempt}"
        commit_sha = await git_manager.commit(commit_msg)

        await git_manager.push(state.get("branch", "main"))

        new_run_id = state.get("run_id", "")
        try:
            ci_client = _build_ci_client(state)
            repo = state.get("repository", "")
            parts = repo.split("/")
            owner, repo_name = parts[0], parts[1]
            branch = state.get("branch", "main")
            await asyncio.sleep(5)
            runs = await ci_client.list_runs(owner, repo_name, branch)
            if runs:
                new_run_id = str(runs[0].get("id", state.get("run_id", "")))
        except Exception as e:
            logger.warning("Failed to discover new CI run after push: %s", e)
        finally:
            await ci_client.close()

        return {
            "llm_analysis": analysis.root_cause,
            "patch_diff": analysis.unified_diff,
            "commit_sha": commit_sha,
            "run_id": new_run_id,
            "patch_summary": analysis.explanation,
            "attempt_count": attempt + 1,
            "notifications_sent": [f"Patch committed: {commit_sha[:8]}"],
        }

    except Exception as e:
        logger.error("LLM fix generation failed: %s", e)
        return {
            "llm_analysis": f"LLM error: {e}",
            "patch_diff": "",
            "attempt_count": attempt + 1,
            "notifications_sent": [f"LLM fix attempt failed: {e}"],
        }


async def node_poll_ci_status(state: AgentState) -> dict[str, Any]:
    logger.info("Polling CI status for run %s", state.get("run_id"))
    ci_client = _build_ci_client(state)

    try:
        repo = state.get("repository", "")
        parts = repo.split("/")
        owner, repo_name = parts[0], parts[1]
        run_id = state.get("run_id", "")

        ci_status = await ci_client.poll_status(
            owner,
            repo_name,
            run_id,
            interval=settings.poll_interval_seconds,
            max_wait=settings.poll_max_wait_seconds,
        )

        return {
            "ci_status": ci_status,
            "notifications_sent": [f"CI status: {ci_status}"],
        }

    except Exception as e:
        logger.error("CI poll failed: %s", e)
        return {
            "ci_status": "FAILED",
            "notifications_sent": [f"CI poll error: {e}"],
        }
    finally:
        await ci_client.close()


async def node_notify_success(state: AgentState) -> dict[str, Any]:
    attempt = state.get("attempt_count", 1)
    logger.info("CI PASSED after %d attempt(s)", attempt)
    mcp_client = _build_mcp_client()

    try:
        if mcp_client:
            await mcp_client.connect()
            alert_payload = {
                "platform": settings.messaging_platform,
                "incident_title": f"CI Fixed: {state.get('repository', 'unknown')}",
                "root_cause": state.get("failure_summary", "Previously failing CI"),
                "resolution_steps": (
                    f"Automated fix applied successfully after {attempt} attempt(s).\n"
                    f"Commit: {state.get('commit_sha', 'N/A')}\n"
                    f"Patch applied:\n{state.get('patch_diff', 'N/A')[:500]}"
                ),
            }
            await mcp_client.send_alert(alert_payload)
    except Exception as e:
        logger.error("Success notification failed: %s", e)
    finally:
        if mcp_client:
            await mcp_client.disconnect()

    return {
        "notifications_sent": [
            f"Success notification sent. CI passed after {attempt} attempt(s)"
        ]
    }


async def node_notify_human_escalation(state: AgentState) -> dict[str, Any]:
    attempt = state.get("attempt_count", 0)
    logger.warning("Escalating to human after %d failed attempts", attempt)
    mcp_client = _build_mcp_client()

    try:
        if mcp_client:
            await mcp_client.connect()
            alert_payload = {
                "platform": settings.messaging_platform,
                "incident_title": (
                    f"ESCALATION: CI Fix Failed ({state.get('repository', 'unknown')})"
                ),
                "root_cause": (
                    state.get(
                        "llm_analysis", "Automated analysis could not resolve the issue"
                    )
                ),
                "resolution_steps": (
                    f"Automated fix failed after {attempt} attempts. Human intervention required.\n"
                    f"Repository: {state.get('repository', 'N/A')}\n"
                    f"Branch: {state.get('branch', 'N/A')}\n"
                    f"Commit: {state.get('commit_sha', 'N/A')}\n\n"
                    f"Final error:\n{state.get('failed_logs', 'N/A')[:1000]}\n\n"
                    f"LLM proposed fix:\n{state.get('patch_diff', 'N/A')[:500]}"
                ),
            }
            await mcp_client.send_alert(alert_payload)
    except Exception as e:
        logger.error("Escalation notification failed: %s", e)
    finally:
        if mcp_client:
            await mcp_client.disconnect()

    return {
        "notifications_sent": [f"Escalation alert sent after {attempt} failed attempts"]
    }


def _extract_failure_summary(logs: str) -> str:
    lines = logs.splitlines()
    error_lines = [
        line
        for line in lines
        if re.search(r"(ERROR|FAIL|Exception|panic|FATAL)", line, re.IGNORECASE)
    ]
    if error_lines:
        return error_lines[0][:200]
    return logs[:200] if logs else "Unknown failure"
