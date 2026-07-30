from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from config import settings
from services.ci_client import CIClient, create_ci_client
from services.git_manager import GitError, WorkspaceGitManager
from services.mcp_client import MCPClient
from state import AgentState, RepairAnalysis

logger = logging.getLogger(__name__)


def _get_llm() -> Any:
    provider = settings.llm_provider

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
        )

    if provider == "bedrock":
        from langchain_aws import ChatBedrock

        return ChatBedrock(
            model=settings.bedrock_model,
            region_name=settings.bedrock_region,
            aws_access_key_id=settings.bedrock_aws_access_key_id or None,
            aws_secret_access_key=settings.bedrock_aws_secret_access_key or None,
        )

    if provider == "azure_openai":
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            azure_deployment=settings.azure_openai_deployment,
            api_version=settings.azure_openai_api_version,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
        )

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.gemini_api_key,
        )

    if provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        return ChatMistralAI(
            model=settings.mistral_model,
            api_key=settings.mistral_api_key,
        )

    if provider == "cohere":
        from langchain_cohere import ChatCohere

        return ChatCohere(
            model=settings.cohere_model,
            api_key=settings.cohere_api_key,
        )

    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
        )

    if provider == "together":
        from langchain_together import ChatTogether

        return ChatTogether(
            model=settings.together_model,
            api_key=settings.together_api_key,
        )

    if provider == "deepseek":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )

    if provider == "xai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.xai_model,
            api_key=settings.xai_api_key,
            base_url=settings.xai_base_url,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
        )

    # Default: OpenAI
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


async def node_fetch_logs_and_alert(state: AgentState) -> dict[str, Any]:
    logger.info("Fetching CI logs for run %s", state.get("run_id"))
    ci_client = _build_ci_client(state)
    mcp_client = _build_mcp_client()

    try:
        repo = state.get("repository", "")
        parts = repo.split("/")
        owner, repo_name = parts[0], parts[1]
        run_id = state.get("run_id", "")
        token = settings.github_token if state.get("ci_platform") == "github" else settings.forgejo_token

        logger.info(
            "=== FETCHING LOGS === owner=%s repo=%s run_id=%s platform=%s token_present=%s forgejo_base=%s",
            owner,
            repo_name,
            run_id,
            state.get("ci_platform"),
            bool(token),
            settings.forgejo_base_url if state.get("ci_platform") == "forgejo" else "N/A",
        )

        failed_logs = await ci_client.fetch_logs(owner, repo_name, run_id)
        logger.info(
            "=== LOGS FETCHED === run=%s len=%d starts_with=%s",
            run_id,
            len(failed_logs) if isinstance(failed_logs, str) else -1,
            (failed_logs[:80] + "...") if isinstance(failed_logs, str) and len(failed_logs) > 80 else failed_logs,
        )
        preview_len = 500
        raw_preview = (
            (failed_logs[:preview_len] + "...")
            if isinstance(failed_logs, str) and len(failed_logs) > preview_len
            else failed_logs
        )
        logger.info("=== RAW CI LOG PREVIEW (first 500 chars) === %s", raw_preview)

        run_info = await ci_client.get_run_info(owner, repo_name, run_id)
        author = run_info.get("actor", {}).get("login", "unknown")
        if author == "unknown":
            author = run_info.get("trigger_user", {}).get("login", "unknown")
        head_branch = run_info.get("head_branch", state.get("branch", "unknown"))
        if head_branch == "unknown":
            head_branch = run_info.get("prettyref", run_info.get("ref_name", "main"))
        head_sha = run_info.get("head_sha", state.get("commit_sha", "unknown"))
        if head_sha == "unknown":
            head_sha = run_info.get("commit_sha", "")

        failure_summary = _extract_failure_summary(failed_logs)

        clone_url = state.get("repo_info", {}).get("clone_url", "")
        if not clone_url:
            clone_url = run_info.get("head_repository", {}).get("clone_url", "")
        if not clone_url:
            clone_url = run_info.get("repository", {}).get("clone_url", "")
        if not clone_url and settings.forgejo_base_url:
            clone_url = f"{settings.forgejo_base_url.rstrip('/')}/{owner}/{repo_name}.git"
        token = settings.github_token if state.get("ci_platform") == "github" else settings.forgejo_token

        repo_info = {
            "name": repo,
            "clone_url": clone_url,
            "token": token,
            "branch": head_branch,
            "commit_sha": head_sha,
        }

        source_files: dict[str, str] = {}
        if clone_url and token:
            try:
                import httpx as _httpx

                tree_url = f"{settings.forgejo_base_url.rstrip('/')}/api/v1/repos/{owner}/{repo_name}/git/trees/{head_sha}?recursive=1"
                logger.info("=== FETCHING SOURCE TREE === url=%s", tree_url)
                async with _httpx.AsyncClient(timeout=30.0) as hc:
                    tree_resp = await hc.get(tree_url, headers={"Authorization": f"token {token}"})
                    if tree_resp.status_code == 200:
                        tree_data = tree_resp.json()
                        py_files = [e["path"] for e in tree_data.get("tree", []) if e["type"] == "blob" and e["path"].endswith(".py")]
                        for fpath in py_files[:15]:
                            file_url = f"{settings.forgejo_base_url.rstrip('/')}/api/v1/repos/{owner}/{repo_name}/contents/{fpath}?ref={head_sha}"
                            file_resp = await hc.get(file_url, headers={"Authorization": f"token {token}"})
                            if file_resp.status_code == 200:
                                import base64 as _b64
                                file_data = file_resp.json()
                                content = _b64.b64decode(file_data["content"]).decode("utf-8", errors="replace")
                                source_files[fpath] = content
                        logger.info("=== FETCHED %d SOURCE FILES === files=%s", len(source_files), list(source_files.keys()))
                    else:
                        logger.warning("=== SOURCE TREE FETCH FAILED === status=%d", tree_resp.status_code)
            except Exception as e:
                logger.warning("Failed to fetch source files (non-fatal): %s", e)

        if not source_files:
            logger.warning(
                "=== NO SOURCE FILES FETCHED === LLM will have zero code context. "
                "Check token permissions, SHA validity, or API connectivity for %s/%s @ %s",
                owner, repo_name, head_sha,
            )

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
            "repo_info": repo_info,
            "source_files": source_files,
            "notifications_sent": [f"Initial alert sent for run {run_id}"],
        }
    except Exception as e:
        redacted = str(e).replace(settings.github_token or "", "[REDACTED]").replace(settings.forgejo_token or "", "[REDACTED]")
        logger.error("Failed to fetch logs: %s", e)
        return {
            "failed_logs": f"Error fetching logs: {redacted}",
            "attempt_count": 0,
            "failure_summary": f"Log fetch error: {redacted}",
            "repo_info": {},
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

    failed_logs = state.get("failed_logs", "No logs available")
    logger.info(
        "LLM prompt prepared: failed_logs_len=%d, source_files=%d, attempt=%d",
        len(failed_logs) if isinstance(failed_logs, str) else -1,
        len(source_files),
        attempt,
    )

    system_prompt = (
        "You are an expert CI/CD fix agent. Given failing CI logs and source code, "
        "analyze the root cause and provide exact file modifications to resolve the issue.\n\n"
        "Return a JSON object with:\n"
        '- "explanation": root cause analysis of the CI failure\n'
        '- "modified_files": list of objects with "file_path" (relative path) and '
        '"content" (complete updated file content)\n\n'
        "IMPORTANT: Return COMPLETE file contents, not diffs. Only fix the actual issue.\n\n"
        "STRICT RULES:\n"
        "- NEVER modify CI configuration files (.github/workflows/*, .gitlab-ci.yml, Jenkinsfile, etc.)\n"
        "- NEVER modify Dockerfiles, docker-compose files, or container configs\n"
        "- NEVER modify .env, .env.local, .env.production, or any secrets files\n"
        "- NEVER modify dependency manifests (pyproject.toml, package.json, go.mod, Cargo.toml)\n"
        "- NEVER modify build system files (Makefile, CMakeLists.txt, etc.)\n"
        "- NEVER modify infrastructure-as-code files (*.tf, *.tfvars, terragrunt.hcl)\n"
        "- NEVER modify .gitignore or any git-related config files\n"
        "- Only modify application source code files (.py, .js, .ts, .java, .go, .rs, etc.)\n"
        "- Keep changes minimal and focused on the root cause of the CI failure\n\n"
        "INFRASTRUCTURE ERROR DETECTION:\n"
        "- If the CI log indicates an infrastructure issue (authentication failure, "
        "credential error, git clone failure, permission denied, missing token, "
        "runner misconfiguration, network timeout, or DNS resolution error), "
        "do NOT attempt to write code to fix it.\n"
        "- Instead, set modified_files to an empty list and set explanation to "
        "'INFRASTRUCTURE_ERROR: <describe the specific infrastructure issue>'\n"
        "- Code changes cannot fix missing credentials, expired tokens, or "
        "misconfigured CI runners — these require human infrastructure intervention."
    )

    user_prompt = f"""Repository: {state.get("repository", "unknown")}
Branch: {state.get("branch", "unknown")}
Attempt: {attempt}/{settings.max_retry_attempts}

FAILING CI LOGS:
{state.get("failed_logs", "No logs available")}

SOURCE CODE:
{source_context}
{previous_context}

Analyze the failure and provide a fix as JSON."""

    # Gate: if no source files are available, the LLM cannot produce a correct fix.
    # Skip the LLM call and escalate to human to prevent hallucination.
    if not source_files:
        logger.warning(
            "=== SKIPPING LLM FIX === source_files is empty (attempt %d/%d). "
            "No code context available — cannot attempt fix.",
            attempt,
            settings.max_retry_attempts,
        )
        return {
            "explanation": (
                "No source files available for analysis. The LLM cannot produce a "
                "correct fix without repository code context. This may be caused by "
                "authentication/connectivity issues with the CI provider API. "
                "Human intervention required."
            ),
            "patch_applied": False,
            "ci_status": "CANNOT_FIX",
            "attempt_count": attempt + 1,
            "notifications_sent": ["No source files available — cannot attempt fix"],
        }

    llm = _get_llm()
    structured_llm = llm.with_structured_output(RepairAnalysis)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        response: RepairAnalysis = await structured_llm.ainvoke(messages)
        logger.info(
            "LLM response: explanation_len=%d, modified_files=%d",
            len(response.explanation or ""),
            len(response.modified_files or []),
        )

        repo_info = state.get("repo_info", {})
        clone_url = repo_info.get("clone_url", "")
        token = repo_info.get("token", "")

        if not clone_url or not token:
            logger.warning("Missing clone_url or token in repo_info, cannot apply fix")
            return {
                "explanation": response.explanation,
                "patch_applied": False,
                "attempt_count": attempt + 1,
                "notifications_sent": ["Missing repo credentials for workspace"],
            }

        success = False
        with WorkspaceGitManager(
            clone_url=clone_url,
            token=token,
            branch=repo_info.get("branch", state.get("branch", "main")),
            commit_sha=repo_info.get("commit_sha", state.get("commit_sha", "")),
            depth=settings.git_clone_depth,
        ) as git_ws:
            new_run_id = state.get("run_id", "")
            file_changes = [{"file_path": f.file_path, "content": f.content} for f in response.modified_files]
            if not file_changes:
                logger.warning("LLM returned no modified_files for attempt %d", attempt)
            logger.info(
                "Applying patch: file_changes=%d, clone_url=%s",
                len(file_changes),
                "yes" if clone_url else "no",
            )
            if file_changes and git_ws.apply_file_changes(file_changes):
                commit_msg = (
                    f"fix(ci): auto-repair attempt {attempt}\n\n{response.explanation}"
                )
                success = git_ws.commit_and_push(commit_msg)
                logger.info("Push result: success=%s, new_run_id=%s", success, new_run_id)

        if success:
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
                    logger.info("Discovered new CI run: %s", new_run_id)
                else:
                    logger.warning("No CI runs found after push")
            except Exception as e:
                logger.warning("Failed to discover new CI run after push: %s", e)
            finally:
                await ci_client.close()

        return {
            "explanation": response.explanation,
            "patch_applied": success,
            "commit_sha": repo_info.get("commit_sha", state.get("commit_sha", "")),
            "run_id": new_run_id,
            "patch_summary": response.explanation[:200],
            "attempt_count": attempt + 1,
            "notifications_sent": [f"Patch applied: {success}"],
        }

    except GitError as e:
        logger.error("Git operation failed — cannot apply fix: %s", e)
        return {
            "explanation": f"INFRASTRUCTURE_ERROR: {e}",
            "patch_applied": False,
            "ci_status": "CANNOT_FIX",
            "attempt_count": attempt,
            "notifications_sent": [f"Git operation failed: {e}"],
        }
    except Exception as e:
        redacted = str(e).replace(settings.github_token or "", "[REDACTED]").replace(settings.forgejo_token or "", "[REDACTED]")
        logger.error("LLM fix generation failed: %s", e)
        return {
            "explanation": f"LLM error: {redacted}",
            "patch_applied": False,
            "attempt_count": attempt + 1,
            "notifications_sent": [f"LLM fix attempt failed: {redacted}"],
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
                    f"Explanation:\n{state.get('explanation', 'N/A')[:500]}"
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
                    f"LLM proposed fix:\n{state.get('explanation', 'N/A')[:500]}"
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
