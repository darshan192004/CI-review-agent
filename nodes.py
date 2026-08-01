from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from config import settings
from services.ci_client import CIClient, create_ci_client
from services.commit_message import build_commit_message, derive_scope, derive_summary
from services.git_manager import GitError, WorkspaceGitManager
from services.mcp_client import MCPClient
from services.rate_limiter import invoke_with_llm_rate_limit
from state import AgentState, FileFix, RepairAnalysis

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
    return create_ci_client("forgejo", settings.forgejo_token, settings.forgejo_base_url)


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
    error_lines = [line for line in lines if re.search(r"(ERROR|FAIL|Exception|panic|FATAL)", line, re.IGNORECASE)]
    if error_lines:
        return error_lines[0][:200]
    return logs[:200] if logs else "Unknown failure"


def _redact(text: str) -> str:
    """Redact configured tokens from error text without corrupting it.

    ``str.replace("", ...)`` would insert the replacement between every
    character, so unset (empty) tokens must be skipped.
    """
    for secret in (settings.github_token, settings.forgejo_token):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


_INFRASTRUCTURE_ERROR_PATTERNS = [
    r"authentication\s+(failed|error)",
    r"authentication\s+failed",
    r"credential",  # credentials/credential error
    r"could not read username",  # git prompting for creds
    r"terminal prompts disabled",
    r"access\s+denied",
    r"(?:permission\s+)?denied\s+to\b",  # "permission denied to <user>"
    r"repository\s+not\s+found",
    r"does not appear to be a git repository",
    r"could not resolve host",  # DNS failure
    r"name or service not known",
    r"connection\s+(refused|reset|timed out|timed-out)",
    r"failed\s+to\s+connect\s+to",  # e.g. "Failed to connect to localhost port 3000"
    r"couldn'?t\s+connect\s+to\s+(server|localhost|host)",
    r"unable\s+to\s+access",  # git: "unable to access 'url'"
    r"network\s+is\s+unreachable",
    r"ssl.*(error|certificate)",
    r"remote: Invalid username or password",
    r"remote: Authentication failed",
    r"(?:missing|no)\s+token",
    r"token.*(invalid|expired|revoked)",
    r"runner\s+misconfigur",
    r"no\s+runner.*online",
    r"unable to access.*clone",
    r"fatal: could not read from remote",
    # Runner / container runtime failures — cannot be fixed with code edits.
    r"path\s+escapes\s+from\s+parent",  # forgejo-runner docker cp action copy bug
    r"copyDir:\s+failed\s+to\s+copy",
    r"executable\s+file\s+not\s+found\s+in",
    r"runtime\s+exec\s+failed",
    r"exitcode\s+'?127'?",  # 127 = command/executable not found (not test failures)
    r"command\s+not\s+found",
    r"error\s+response\s+from\s+daemon",
]


def _detect_infrastructure_error(logs: str) -> str:
    """Deterministically detect infrastructure-level CI failures.

    Infrastructure failures (git clone/auth, missing credentials, offline
    runners, network errors) cannot be fixed by editing source code. Returning
    a non-empty description lets the agent skip the LLM and escalate instead of
    hallucinating code changes.
    """
    if not logs:
        return ""
    normalized = logs.lower()
    for pattern in _INFRASTRUCTURE_ERROR_PATTERNS:
        match = re.search(pattern, normalized)
        if match:
            return match.group(0)
    return ""


async def node_clone_repository(state: AgentState) -> dict[str, Any]:
    """Clone repository to local workspace.
    Exit early if clone fails to prevent hallucination on empty workspace.
    """
    from services.git_manager import GitError, WorkspaceGitManager

    logger.info("Cloning repository for %s run %s", state.get("repository", ""), state.get("run_id"))

    repo = state.get("repository", "")
    parts = repo.split("/")
    owner, repo_name = parts[0], parts[1] if len(parts) > 1 else ""

    # Need both forgejo_token or github_token for authentication
    platform = state.get("ci_platform", "github")
    token = settings.forgejo_token if platform == "forgejo" else settings.github_token
    if not token:
        error_msg = f"No authentication token for {platform} platform"
        logger.error("=== CLONE BLOCK: %s ===", error_msg)
        return {
            "ci_status": "CANNOT_FIX",
            "clone_error": error_msg,
            "failure_summary": f"INFRASTRUCTURE_ERROR: {error_msg}",
            "notifications_sent": ["Missing CI provider token"],
        }

    # Build clone URL using Forgejo base_url for Forgejo platform
    clone_url = ""
    if platform == "forgejo" and settings.forgejo_base_url:
        # Forgejo API expects http://localhost:3000/owner/repo.git
        # Use the same pattern as CI client: settings.forgejo_base_url + "/" + owner + "/" + repo_name + ".git"
        clone_url = f"{settings.forgejo_base_url.rstrip('/')}/{owner}/{repo_name}.git"
    elif platform == "github":
        clone_url = f"https://github.com/{owner}/{repo_name}.git"
    else:
        error_msg = f"Unsupported CI platform: {platform}"
        logger.error("=== CLONE BLOCK: %s ===", error_msg)
        return {
            "ci_status": "CANNOT_FIX",
            "clone_error": error_msg,
            "failure_summary": f"INFRASTRUCTURE_ERROR: {error_msg}",
            "notifications_sent": ["Unsupported CI platform"],
        }

    try:
        with WorkspaceGitManager(
            clone_url=clone_url,
            token=token,
            branch=state.get("branch", "main"),
            commit_sha=state.get("commit_sha", ""),
            depth=settings.git_clone_depth,
        ) as git_ws:
            # Success: capture workspace directory for later use
            logger.info("Repository cloned successfully: workspace=%s", git_ws.temp_dir)
            return {
                "workspace_dir": git_ws.temp_dir,
                "clone_url": clone_url,
                "repo_info": {
                    "temp_dir": git_ws.temp_dir,
                    "clone_url": clone_url,
                    "token": token,
                    "branch": state.get("branch", "main"),
                    "commit_sha": state.get("commit_sha", ""),
                },
                "ci_status": "RUNNING",
            }
    except GitError as e:
        logger.error("=== CLONE FAILED: %s ===", e)
        return {
            "ci_status": "CANNOT_FIX",
            "clone_error": str(e),
            "failure_summary": f"INFRASTRUCTURE_ERROR: Git clone failed — {e}",
            "notifications_sent": ["Repository clone failed"],
        }
    except Exception as e:
        redacted = _redact(str(e))
        logger.error("=== CLONE ERROR (unexpected): %s ===", e)
        return {
            "ci_status": "CANNOT_FIX",
            "clone_error": redacted,
            "failure_summary": f"INFRASTRUCTURE_ERROR: Clone error — {redacted}",
            "notifications_sent": ["Repository clone error"],
        }


async def node_fetch_logs_and_alert(state: AgentState) -> dict[str, Any]:
    logger.info("Fetching CI logs for run %s", state.get("run_id"))
    ci_client = _build_ci_client(state)
    mcp_client = _build_mcp_client()

    repo = state.get("repository", "")
    parts = repo.split("/")
    owner, repo_name = parts[0], parts[1] if len(parts) > 1 else ""
    run_id = state.get("run_id", "")
    token = settings.github_token if state.get("ci_platform") == "github" else settings.forgejo_token
    head_branch = state.get("branch", "main")
    head_sha = state.get("commit_sha", "")
    author = state.get("ci_author", "")

    failed_logs: str = ""
    failure_summary: str = ""
    source_files: dict[str, str] = {}

    # If we have a workspace, mark it ready for LLM context
    repo_info = state.get("repo_info", {})
    cloned_temp_dir = repo_info.get("temp_dir") if repo_info else None
    if cloned_temp_dir:
        logger.info("Using existing workspace: %s", cloned_temp_dir)

    try:
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
    except Exception as e:
        redacted = _redact(str(e))
        logger.error("Failed to fetch logs (continuing with state defaults): %s", e)
        failed_logs = f"Error fetching logs: {redacted}"
        failure_summary = f"Log fetch error: {redacted}"

    try:
        if not head_sha or head_sha == "unknown" or not author or author == "unknown":
            run_info = await ci_client.get_run_info(owner, repo_name, run_id)
            if author in ("", "unknown"):
                author = (
                    run_info.get("actor", {}).get("login", "")
                    or run_info.get("trigger_user", {}).get("login", "")
                    or state.get("ci_author", "")
                )
            if head_branch in ("", "unknown"):
                head_branch = (
                    run_info.get("head_branch", "")
                    or run_info.get("prettyref", "")
                    or run_info.get("ref_name", "")
                    or head_branch
                )
            if head_sha in ("", "unknown"):
                head_sha = run_info.get("head_sha", "") or run_info.get("commit_sha", "") or head_sha
    except Exception as e:
        logger.warning("Failed to fetch run info (continuing with state defaults): %s", e)

    if not failure_summary and failed_logs:
        failure_summary = _extract_failure_summary(failed_logs)

    clone_url = state.get("repo_info", {}).get("clone_url", "")
    if not clone_url and settings.forgejo_base_url:
        clone_url = f"{settings.forgejo_base_url.rstrip('/')}/{owner}/{repo_name}.git"

    repo_info = {
        "name": repo,
        "clone_url": clone_url,
        "token": token,
        "branch": head_branch,
        "commit_sha": head_sha,
    }

    if head_sha and token:
        try:
            import httpx as _httpx

            base = settings.forgejo_base_url.rstrip("/")
            tree_url = f"{base}/api/v1/repos/{owner}/{repo_name}/git/trees/{head_sha}?recursive=1"
            logger.info("=== FETCHING SOURCE TREE === url=%s", tree_url)
            async with _httpx.AsyncClient(timeout=30.0) as hc:
                tree_resp = await hc.get(tree_url, headers={"Authorization": f"token {token}"})
                if tree_resp.status_code == 200:
                    tree_data = tree_resp.json()
                    py_files = [
                        e["path"]
                        for e in tree_data.get("tree", [])
                        if e["type"] == "blob" and e["path"].endswith(".py")
                    ]
                    for fpath in py_files[:15]:
                        file_url = f"{base}/api/v1/repos/{owner}/{repo_name}/contents/{fpath}?ref={head_sha}"
                        file_resp = await hc.get(file_url, headers={"Authorization": f"token {token}"})
                        if file_resp.status_code == 200:
                            import base64 as _b64

                            file_data = file_resp.json()
                            content = _b64.b64decode(file_data["content"]).decode("utf-8", errors="replace")
                            source_files[fpath] = content
                    logger.info(
                        "=== FETCHED %d SOURCE FILES ===",
                        len(source_files),
                        list(source_files.keys()),
                    )
                else:
                    logger.warning("=== SOURCE TREE FETCH FAILED === status=%d", tree_resp.status_code)
        except Exception as e:
            logger.warning("Failed to fetch source files (non-fatal): %s", e)

    if not source_files:
        logger.warning(
            "=== NO SOURCE FILES FETCHED === LLM will have zero code context. "
            "Check token permissions, SHA validity, or API connectivity for %s/%s @ %s",
            owner,
            repo_name,
            head_sha,
        )

    if mcp_client:
        try:
            await mcp_client.connect()
            alert_payload = {
                "platform": settings.messaging_platform,
                "incident_title": f"CI Failed: {repo} (branch: {head_branch})",
                "root_cause": failure_summary or "Unknown failure",
                "resolution_steps": (
                    f"Automated fix attempt "
                    f"{state.get('attempt_count', 1)}/{settings.max_retry_attempts}"
                    " in progress.\n"
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

    await ci_client.close()
    return {
        "failed_logs": failed_logs,
        "ci_author": author,
        "failure_summary": failure_summary,
        "commit_sha": head_sha,
        "branch": head_branch,
        "repo_info": repo_info,
        "source_files": source_files,
        "notifications_sent": [f"Initial alert sent for run {run_id}"],
    }


def _coerce_content(content: Any) -> str:
    """Coerce a raw LLM response ``content`` to plain text.

    Different providers return different shapes: Groq/OpenAI return a ``str``,
    while Anthropic returns a list of content blocks. Normalize to text so the
    JSON parser always receives a string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        return "\n".join(parts)
    return str(content)


def _parse_repair_analysis(raw_text: str) -> RepairAnalysis:
    """Parse a RepairAnalysis from arbitrary LLM text output.

    Some providers (notably Groq via ``with_structured_output``) reject tool-call
    arguments server-side when the model emits invalid escapes such as ``\\'``.
    We therefore call the model in plain-text mode and parse the JSON ourselves,
    repairing common malformations:
      * markdown code fences / leading prose before the JSON object,
      * invalid ``\\'`` (and similar single-character) escapes inside strings,
      * stray trailing text after the JSON object.
    """
    text = raw_text.strip()

    json_block = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if json_block:
        text = json_block.group(1).strip()

    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    candidate = obj_match.group(0) if obj_match else text

    candidate = re.sub(r"\\([^\"\\/bfnrtu])", r"\1", candidate)

    data = json.loads(candidate)

    explanation = data.get("explanation")
    if explanation is None:
        raise ValueError("LLM response missing required field: explanation")

    modified_files = []
    for entry in data.get("modified_files") or []:
        if not isinstance(entry, dict):
            continue
        file_path = entry.get("file_path")
        content = entry.get("content")
        if isinstance(file_path, str) and file_path and isinstance(content, str):
            reason = entry.get("reason")
            reason = reason if isinstance(reason, str) and reason.strip() else None
            modified_files.append(FileFix(file_path=file_path, content=content, reason=reason))

    # Commit header fallbacks: LLM provides scope/summary/reason, but we derive
    # sane values when the model omits them so the message stays well-formed.
    commit_scope = data.get("commit_scope")
    if not isinstance(commit_scope, str) or not commit_scope.strip():
        commit_scope = derive_scope([f.file_path for f in modified_files])

    commit_summary = data.get("commit_summary")
    if not isinstance(commit_summary, str) or not commit_summary.strip():
        commit_summary = derive_summary(explanation)

    return RepairAnalysis(
        explanation=explanation,
        modified_files=modified_files,
        commit_scope=commit_scope,
        commit_summary=commit_summary,
    )


async def node_llm_fix_code(state: AgentState) -> dict[str, Any]:
    attempt = state.get("attempt_count", 1)
    logger.info("Analyzing failure and generating fix (attempt %d)", attempt)

    source_files = state.get("source_files", {})
    source_context = ""
    for path, content in source_files.items():
        source_context += f"\n--- File: {path} ---\n{content}\n"

    # Prior attempt analysis is provided by the webhook handler (externalized
    # retry loop) from the session row. Fall back to in-state analysis for
    # direct CLI runs that never went through the handler.
    previous_context = state.get("previous_context", "")
    if not previous_context and attempt > 1:
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

    # Deterministic infrastructure-error gate: skip the LLM entirely when the
    # failure is environmental (auth, clone, runner, network). Code edits cannot
    # fix these, and sending them to the LLM risks hallucinated "fixes".
    if isinstance(failed_logs, str) and failed_logs.startswith("Error fetching logs"):
        infra_error = "Log fetch from CI provider failed — cannot analyze failure"
    else:
        infra_error = _detect_infrastructure_error(failed_logs)
    if infra_error:
        logger.error(
            "=== SKIPPING LLM FIX (INFRASTRUCTURE ERROR) === detected=%r (attempt %d/%d)",
            infra_error,
            attempt,
            settings.max_retry_attempts,
        )
        return {
            "explanation": f"INFRASTRUCTURE_ERROR: {infra_error}",
            "patch_applied": False,
            "ci_status": "CANNOT_FIX",
            "attempt_count": attempt + 1,
            "failure_summary": f"INFRASTRUCTURE_ERROR: {infra_error}",
            "notifications_sent": ["Infrastructure error detected — escalated to human"],
        }

    system_prompt = (
        "You are an expert CI/CD fix agent. Given failing CI logs and source code, "
        "analyze the root cause and provide exact file modifications to resolve the issue.\n\n"
        "Return a JSON object with:\n"
        '- "explanation": root cause analysis of the CI failure\n'
        '- "commit_scope": short git commit scope (module/folder, e.g. "services", "ui")\n'
        '- "commit_summary": short imperative action (<= 60 chars) for the commit subject\n'
        '- "modified_files": list of objects with "file_path" (relative path), '
        '"content" (complete updated file content) and "reason" (one line why this file changed)\n\n'
        "IMPORTANT: Return COMPLETE file contents, not diffs. Only fix the actual issue.\n"
        "IMPORTANT: Respond with ONLY a single valid JSON object — no markdown, no code "
        "fences, no prose before or after.\n"
        "IMPORTANT: Inside JSON string values, escape ONLY double quotes and "
        "backslashes. Single quotes in code must NOT be backslash-escaped.\n\n"
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
        "COMMIT HEADER RULE:\n"
        '- "commit_scope" must be the top-level module/folder of the changed files '
        '(e.g. "services" for services/*.py).\n'
        '- "commit_summary" must be a short imperative action (<= 60 chars) that '
        'describes the fix, e.g. "fix flaky timeout in the test runner".\n'
        '- Every entry in "modified_files" must include a one-line "reason" '
        "explaining why that file changed.\n\n"
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
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        raw_response = await invoke_with_llm_rate_limit(
            llm,
            messages,
            max_retries=settings.llm_max_retries,
            base_backoff_seconds=settings.llm_retry_backoff_seconds,
        )
        response = _parse_repair_analysis(_coerce_content(raw_response.content))
        logger.info(
            "LLM response: explanation_len=%d, modified_files=%d",
            len(response.explanation or ""),
            len(response.modified_files or []),
        )

        if not response.modified_files:
            # The agent only runs on CI failures. An LLM that proposes no code
            # changes while the run is still failing must NOT be reported as
            # PASSED — that would claim a green build that never happened.
            # Treat it as an unresolved failure and escalate to a human.
            logger.info("LLM returned no modifications; escalating (CI still failing)")
            return {
                "explanation": response.explanation,
                "patch_applied": False,
                "ci_status": "CANNOT_FIX",
                "attempt_count": attempt + 1,
                "failure_summary": ("LLM suggested no code changes while CI is failing — escalated to human"),
                "notifications_sent": ["LLM suggested no code changes — escalated to human"],
            }

        repo_info = state.get("repo_info", {})
        clone_url = repo_info.get("clone_url", "")
        token = repo_info.get("token", "")

        if not clone_url or not token:
            logger.warning("Missing clone_url or token in repo_info, cannot apply fix")
            return {
                "explanation": response.explanation,
                "patch_applied": False,
                "ci_status": "CANNOT_FIX",
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
                commit_msg = build_commit_message(
                    summary=response.commit_summary or derive_summary(response.explanation),
                    scope=response.commit_scope,
                    explanation=response.explanation,
                    file_reasons=[(f.file_path, f.reason or "") for f in response.modified_files],
                    repo=state.get("repository", ""),
                    run_id=state.get("run_id", ""),
                    attempt=attempt,
                )
                success = git_ws.commit_and_push(commit_msg)
                logger.info("Push result: success=%s, new_run_id=%s", success, new_run_id)

            # Capture the new head SHA after push for ci_waiter
            new_head_sha = git_ws.get_head_sha()
            logger.info("New HEAD SHA after push: %s", new_head_sha[:12])

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
            "commit_sha": new_head_sha if success else repo_info.get("commit_sha", state.get("commit_sha", "")),
            "run_id": new_run_id,
            "ci_status": "FIX_PUSHED" if success else "CANNOT_FIX",
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
        redacted = _redact(str(e))
        logger.error("LLM fix generation failed: %s", e)
        return {
            "explanation": f"LLM error: {redacted}",
            "patch_applied": False,
            "ci_status": "CANNOT_FIX",
            "attempt_count": attempt + 1,
            "failure_summary": f"LLM fix generation failed — escalated to human: {redacted}",
            "notifications_sent": [f"LLM fix attempt failed: {redacted}"],
        }


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

    return {"notifications_sent": [f"Success notification sent. CI passed after {attempt} attempt(s)"]}


async def node_notify_human_escalation(state: AgentState) -> dict[str, Any]:
    attempt = state.get("attempt_count", 0)
    logger.warning("Escalating to human after %d failed attempts", attempt)
    mcp_client = _build_mcp_client()

    try:
        if mcp_client:
            await mcp_client.connect()
            alert_payload = {
                "platform": settings.messaging_platform,
                "incident_title": (f"ESCALATION: CI Fix Failed ({state.get('repository', 'unknown')})"),
                "root_cause": (state.get("llm_analysis", "Automated analysis could not resolve the issue")),
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

    return {"notifications_sent": [f"Escalation alert sent after {attempt} failed attempts"]}
