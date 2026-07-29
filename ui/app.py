from __future__ import annotations

import html as html_mod
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import settings
from services.ci_client import create_ci_client
from services.auth import (
    User,
    _COOKIE_NAME,
    authenticate_user,
    create_session,
    destroy_session,
    get_current_user,
    require_admin_role,
)
from services.env_writer import read_env_redacted, write_env
from services.run_tracker import run_tracker

logger = logging.getLogger(__name__)

_UI_DIR = Path(__file__).resolve().parent

router = APIRouter()

templates = Jinja2Templates(directory=str(_UI_DIR / "templates"))


# ---------------------------------------------------------------------------
# Authentication Routes
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request) -> HTMLResponse:
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))

    user = authenticate_user(username, password)
    if user is None:
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid credentials"}
        )

    token = create_session(user)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=24 * 60 * 60,
    )
    return response


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    token = request.cookies.get(_COOKIE_NAME)
    if token:
        destroy_session(token)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key=_COOKIE_NAME)
    return response


def _status_badge(status: str) -> str:
    if status == "AGENT_WORKING":
        return '<span class="badge badge-blue"><span class="status-dot status-dot-pulse bg-blue-400"></span>Agent Working</span>'
    if status == "processing":
        return '<span class="badge badge-blue"><span class="status-dot status-dot-pulse bg-blue-400"></span>Processing</span>'
    if status in ("PASSED", "success"):
        return '<span class="badge badge-green"><span class="status-dot bg-emerald-400"></span>Passed</span>'
    if status in ("FAILED", "failed"):
        return '<span class="badge badge-red"><span class="status-dot bg-rose-400"></span>Failed</span>'
    if status == "error":
        return '<span class="badge badge-orange"><span class="status-dot bg-amber-400"></span>Error</span>'
    return f'<span class="badge badge-purple">{html_mod.escape(status)}</span>'


def _format_uptime(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def _get_uptime_seconds() -> float:
    from server import _start_time as st

    return time.monotonic() - st if st else 0


def _get_settings() -> Any:
    redacted = read_env_redacted()

    class _S:
        pass

    s = _S()
    for k in [
        "github_token",
        "forgejo_token",
        "forgejo_base_url",
        "llm_provider",
        "openai_api_key",
        "openai_model",
        "anthropic_api_key",
        "anthropic_model",
        "gemini_api_key",
        "gemini_model",
        "ollama_base_url",
        "ollama_model",
        "azure_openai_endpoint",
        "azure_openai_api_key",
        "azure_openai_deployment",
        "mcp_server_command",
        "messaging_platform",
        "mattermost_webhook_url",
        "slack_webhook_url",
        "discord_webhook_url",
        "forgejo_webhook_secret",
        "github_webhook_secret",
        "max_retry_attempts",
        "poll_interval_seconds",
        "poll_max_wait_seconds",
        "log_max_tokens",
        "checkpointer_type",
        "notification_trigger_level",
        "auto_create_pull_request",
        "auto_fix_reruns",
        "git_repo_path",
        "git_default_branch",
    ]:
        setattr(s, k, redacted.get(k, getattr(settings, k, "")))
    return s


# ---------------------------------------------------------------------------
# UI Pages
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _user: User = Depends(get_current_user)) -> HTMLResponse:
    counts = await run_tracker.count_by_status()
    recent = await run_tracker.get_all_runs()
    recent = recent[-10:]
    recent.reverse()
    uptime = _format_uptime(_get_uptime_seconds())

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active_page": "dashboard",
            "active_count": counts.get("processing", 0),
            "success_count": counts.get("PASSED", 0) + counts.get("success", 0),
            "failed_count": counts.get("FAILED", 0)
            + counts.get("failed", 0)
            + counts.get("error", 0),
            "uptime": uptime,
            "recent_runs": [
                {
                    "repository": r["repository"],
                    "run_id": r["run_id"],
                    "run_attempt": r.get("run_attempt", "1"),
                    "status": r["status"],
                    "platform": r.get("platform", ""),
                    "branch": r.get("branch", ""),
                    "commit_sha": r.get("commit_sha", ""),
                    "author": r.get("author", ""),
                    "failure_summary": r.get("failure_summary", ""),
                    "patch_summary": r.get("patch_summary", ""),
                }
                for r in recent
            ],
            "last_updated": time.strftime("%H:%M:%S"),
        },
    )


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request, _user: User = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "active_page": "config",
            "settings": _get_settings(),
        },
    )


@router.get("/runs", response_class=HTMLResponse)
async def runs_page(
    request: Request,
    status: str = Query("", alias="status"),
    platform: str = Query("", alias="platform"),
    _user: User = Depends(get_current_user),
) -> HTMLResponse:
    all_runs = await run_tracker.get_all_runs()
    all_runs.reverse()

    if status:
        all_runs = [r for r in all_runs if r["status"] == status]
    if platform:
        all_runs = [r for r in all_runs if r.get("platform") == platform]

    runs_data = [
        {
            "repository": r["repository"],
            "run_id": r["run_id"],
            "run_attempt": r.get("run_attempt", "1"),
            "status": r["status"],
            "platform": r.get("platform", ""),
            "branch": r.get("branch", ""),
            "commit_sha": r.get("commit_sha", ""),
            "author": r.get("author", ""),
            "failure_summary": r.get("failure_summary", ""),
            "patch_summary": r.get("patch_summary", ""),
        }
        for r in all_runs
    ]

    return templates.TemplateResponse(
        request,
        "runs.html",
        {"active_page": "runs", "runs": runs_data},
    )


@router.get("/sync", response_class=HTMLResponse)
async def sync_page(request: Request, _user: User = Depends(require_admin_role)) -> HTMLResponse:
    recent = await run_tracker.get_all_runs()
    recent.reverse()
    return templates.TemplateResponse(
        request,
        "sync.html",
        {"active_page": "sync", "recent_runs": recent[:50]},
    )


# ---------------------------------------------------------------------------
# HTMX partial: dashboard live poll (Out-Of-Band Metric Syncing)
# ---------------------------------------------------------------------------


@router.get("/api/dashboard/partial", response_class=HTMLResponse)
async def dashboard_partial(
    _user: User = Depends(get_current_user),
    repo: str = Query("", alias="repo"),
) -> HTMLResponse:
    counts = await run_tracker.count_by_status()
    recent = await run_tracker.get_all_runs()
    recent.reverse()
    uptime = _format_uptime(_get_uptime_seconds())

    if repo:
        recent = [r for r in recent if r["repository"] == repo]
        # Recompute counts for filtered repo
        active_cnt = sum(
            1 for r in recent
            if r["status"] in ("processing", "AGENT_WORKING", "RUNNING", "PENDING", "QUEUED", "WAITING")
        )
        success_cnt = sum(1 for r in recent if r["status"] in ("PASSED", "success"))
        failed_cnt = sum(1 for r in recent if r["status"] in ("FAILED", "failed", "error", "EXHAUSTED"))
    else:
        active_cnt = counts.get("processing", 0) + counts.get("AGENT_WORKING", 0)
        success_cnt = counts.get("PASSED", 0) + counts.get("success", 0)
        failed_cnt = counts.get("FAILED", 0) + counts.get("failed", 0) + counts.get("error", 0)

    rows = ""
    e = html_mod.escape
    for run in recent:
        commit_short = e(run.get("commit_sha", "")[:8]) if run.get("commit_sha") else "\u2014"
        rows += f"""<tr>
            <td class="font-mono text-xs text-indigo-400">{e(run["repository"])}</td>
            <td class="font-mono text-xs text-slate-400">#{e(run["run_id"])}</td>
            <td class="font-mono text-xs text-slate-400">#{e(run.get("run_attempt", "1"))}</td>
            <td>{_status_badge(run["status"])}</td>
            <td class="text-xs text-slate-400 uppercase font-mono">{e(run.get("platform", "\u2014"))}</td>
            <td class="text-xs text-slate-400">{e(run.get("branch", "\u2014"))}</td>
            <td class="text-xs text-slate-400 font-mono">{commit_short}</td>
            <td class="text-xs text-slate-400">{e(run.get("author", "\u2014"))}</td>
        </tr>"""

    html = f"""
    <!-- Out of Band Metric Card Updates for Instant Syncing -->
    <p id="metric-active" hx-swap-oob="outerHTML" class="text-3xl font-bold text-blue-400 font-mono">{active_cnt}</p>
    <p id="metric-success" hx-swap-oob="outerHTML" class="text-3xl font-bold text-emerald-400 font-mono">{success_cnt}</p>
    <p id="metric-failed" hx-swap-oob="outerHTML" class="text-3xl font-bold text-rose-400 font-mono">{failed_cnt}</p>
    <p id="metric-uptime" hx-swap-oob="outerHTML" class="text-3xl font-bold ci-text-main font-mono">{uptime}</p>

    <!-- Table Body HTML -->
    <table class="data-table">
      <thead>
        <tr>
          <th>Repository</th>
          <th>Run ID</th>
          <th>Attempt</th>
          <th>Status</th>
          <th>Platform</th>
          <th>Branch</th>
          <th>Commit</th>
          <th>Author</th>
        </tr>
      </thead>
      <tbody>
        {rows if rows else '<tr><td colspan="8" class="p-12 text-center text-slate-400 text-sm">'
                'No webhook runs recorded yet.</td></tr>'}
      </tbody>
    </table>"""

    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Repo Status API
# ---------------------------------------------------------------------------


_BROKEN_STATUSES = frozenset({"FAILED", "failed", "EXHAUSTED", "error"})


def _repo_dot_color(status: str | None) -> str:
    if status is None:
        return "slate"
    if status in ("PASSED", "success"):
        return "emerald"
    if status in _BROKEN_STATUSES:
        return "rose"
    return "blue"


@router.get("/api/repos")
async def list_repos(_user: User = Depends(get_current_user)) -> JSONResponse:
    """List all discovered repos with latest status and broken-first sort."""
    from services.continuous_sync import discover_repos

    org = settings.forgejo_org or ""
    platform = "forgejo"
    if not org:
        org = settings.github_org or ""
        platform = "github"

    if not org:
        return JSONResponse(content={
            "repos": [], "org": "",
            "error": "No organization configured. Set FORGEJO_ORG or GITHUB_ORG in .env or Configuration page.",
        })

    try:
        discovered = await discover_repos(org, platform)
    except Exception as e:
        logger.warning("Failed to discover repos for org %s: %s", org, e)
        return JSONResponse(content={
            "repos": [], "org": org,
            "error": f"Failed to discover repos: {e}",
        })

    latest = await run_tracker.get_latest_repo_statuses()

    def sort_key(repo: str) -> tuple[int, str]:
        status = latest.get(repo, {}).get("status")
        is_broken = 0 if (status and status in _BROKEN_STATUSES) else 1
        return (is_broken, repo.lower())

    discovered.sort(key=sort_key)

    repos_data = []
    for repo in discovered:
        info = latest.get(repo, {})
        status = info.get("status")
        repos_data.append({
            "name": repo,
            "status": status or "unknown",
            "dot_color": _repo_dot_color(status),
        })

    return JSONResponse(content={"repos": repos_data, "org": org})


# ---------------------------------------------------------------------------
# Settings API
# ---------------------------------------------------------------------------


@router.get("/api/settings")
async def get_settings(_user: User = Depends(get_current_user)) -> dict[str, str]:
    return read_env_redacted()


@router.put("/api/settings")
async def update_settings(request: Request, _user: User = Depends(require_admin_role)) -> JSONResponse:
    body: dict[str, Any] = {}
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
    else:
        form = await request.form()
        body = dict(form)

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON or Form dictionary")

    allowed_keys = {
        "github_token",
        "forgejo_token",
        "forgejo_base_url",
        "messaging_platform",
        "mcp_server_command",
        "mattermost_webhook_url",
        "slack_webhook_url",
        "discord_webhook_url",
        "openai_api_key",
        "openai_model",
        "anthropic_api_key",
        "anthropic_model",
        "gemini_api_key",
        "gemini_model",
        "ollama_base_url",
        "ollama_model",
        "azure_openai_endpoint",
        "azure_openai_api_key",
        "azure_openai_deployment",
        "llm_provider",
        "forgejo_org",
        "github_org",
        "forgejo_webhook_secret",
        "github_webhook_secret",
        "max_retry_attempts",
        "poll_interval_seconds",
        "poll_max_wait_seconds",
        "log_max_tokens",
        "checkpointer_type",
        "notification_trigger_level",
        "auto_create_pull_request",
        "auto_fix_reruns",
        "git_repo_path",
        "git_default_branch",
        "server_host",
        "server_port",
    }

    # Filter out redacted values ("••••••••") so we don't overwrite real secrets
    updates = {
        k: str(v).strip()
        for k, v in body.items()
        if k in allowed_keys and str(v).strip() != "••••••••"
    }

    if updates:
        write_env(updates)
        for k, v in updates.items():
            if hasattr(settings, k):
                if k in ("max_retry_attempts", "poll_interval_seconds", "poll_max_wait_seconds", "log_max_tokens", "server_port"):
                    try:
                        setattr(settings, k, int(v))
                    except ValueError:
                        pass
                else:
                    setattr(settings, k, v)

    return JSONResponse(content={"saved": len(updates), "keys": list(updates.keys())})


@router.post("/api/trigger-test-run")
async def trigger_test_run(_user: User = Depends(require_admin_role)) -> JSONResponse:
    import random
    run_id = str(random.randint(1000, 9999))
    repo = "owner/ci-test-repo"
    await run_tracker.record(repository=repo, run_id=run_id, status="processing")
    
    # Simulate run completion after background task
    import asyncio
    async def _complete():
        await asyncio.sleep(2)
        await run_tracker.update_status(repository=repo, run_id=run_id, status="PASSED")

    asyncio.create_task(_complete())
    return JSONResponse(content={"ok": True, "run_id": run_id, "repository": repo})


def _map_forgejo_status(status: str) -> str:
    return {
        "success": "PASSED",
        "failure": "FAILED",
        "running": "RUNNING",
        "waiting": "processing",
        "cancelled": "error",
        "skipped": "error",
    }.get(status, "processing")


@router.post("/api/sync-runs")
async def sync_runs(request: Request, _user: User = Depends(require_admin_role)) -> JSONResponse:
    body: dict[str, Any] = {}
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
    elif "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        body = dict(form)

    repo = body.get("repo", "").strip()
    branch = body.get("branch", "main").strip()
    limit = min(int(body.get("limit", 20)), 50)
    continuous_val = body.get("continuous", True)
    if isinstance(continuous_val, bool):
        continuous = continuous_val
    else:
        continuous = str(continuous_val).lower() == "true"

    if "/" not in repo:
        raise HTTPException(status_code=400, detail="repo must be in owner/repo format")

    owner, repo_name = repo.split("/", 1)
    ci_client = create_ci_client("forgejo", settings.forgejo_token, settings.forgejo_base_url)

    try:
        runs = await ci_client.list_runs(owner, repo_name, branch, limit)
        synced = 0
        for run in runs:
            status = _map_forgejo_status(run.get("status", ""))
            actor = run.get("trigger_user") or run.get("actor") or {}
            await run_tracker.record(
                repository=repo,
                run_id=str(run.get("id", "")),
                run_attempt="1",
                status=status,
                platform="forgejo",
                branch=run.get("prettyref", branch),
                commit_sha=run.get("commit_sha", ""),
                author=actor.get("login", "unknown") if isinstance(actor, dict) else str(actor),
            )
            synced += 1

        # Start continuous sync if requested
        sync_started = False
        if continuous:
            from services.continuous_sync import start_continuous_sync
            sync_started = start_continuous_sync(repo, branch)

        return JSONResponse(content={
            "synced": synced,
            "repo": repo,
            "branch": branch,
            "continuous_sync": sync_started,
        })
    except Exception as e:
        logger.error("Sync failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Failed to sync from Forgejo: {e}")
    finally:
        await ci_client.close()


@router.post("/api/clear-history")
async def clear_history(_user: User = Depends(require_admin_role)) -> JSONResponse:
    await run_tracker.clear()
    return JSONResponse(content={"ok": True, "message": "History cleared"})


@router.get("/api/sync-status")
async def get_sync_status(_user: User = Depends(get_current_user)) -> JSONResponse:
    """Get status of active continuous sync tasks."""
    from services.continuous_sync import get_active_sync_tasks
    return JSONResponse(content={"tasks": get_active_sync_tasks()})


@router.post("/api/stop-sync")
async def stop_sync(request: Request, _user: User = Depends(require_admin_role)) -> JSONResponse:
    """Stop continuous sync for a repo."""
    from services.continuous_sync import stop_continuous_sync
    body: dict[str, Any] = {}
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        body = dict(form)

    repo = body.get("repo", "").strip()
    branch = body.get("branch", "main").strip()

    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")

    stopped = stop_continuous_sync(repo, branch)
    return JSONResponse(content={"stopped": stopped, "repo": repo})


@router.post("/api/sync-org")
async def sync_org(_user: User = Depends(require_admin_role)) -> JSONResponse:
    """Discover all repos in the configured org and start continuous sync."""
    from services.continuous_sync import discover_repos, start_continuous_sync

    org = settings.forgejo_org or ""
    platform = "forgejo"
    if not org:
        org = settings.github_org or ""
        platform = "github"
    if not org:
        raise HTTPException(status_code=400, detail="No org configured (set FORGEJO_ORG or GITHUB_ORG in .env)")

    try:
        repos = await discover_repos(org, platform)
        started = 0
        for repo in repos:
            if start_continuous_sync(repo):
                started += 1
        return JSONResponse(content={
            "ok": True,
            "org": org,
            "repos_found": len(repos),
            "sync_started": started,
        })
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Org sync failed: {e}")


@router.post("/api/test/ollama")
async def test_ollama(_user: User = Depends(require_admin_role)) -> JSONResponse:
    import httpx

    base = settings.ollama_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{base}/api/tags")
            if resp.status_code == 200:
                models = [m.get("name") for m in resp.json().get("models", [])]
                return JSONResponse(content={"ok": True, "models": models})
            return JSONResponse(status_code=400, content={"ok": False, "detail": f"HTTP {resp.status_code}"})
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "detail": str(e)})




# ---------------------------------------------------------------------------
# Connection Test Endpoints
# ---------------------------------------------------------------------------


@router.post("/api/test/github")
async def test_github(_user: User = Depends(require_admin_role)) -> JSONResponse:
    import httpx

    if not settings.github_token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {settings.github_token}"},
            )
            if resp.status_code == 200:
                return JSONResponse(
                    content={"ok": True, "user": resp.json().get("login", "unknown")}
                )
            return JSONResponse(
                status_code=400,
                content={"ok": False, "detail": f"HTTP {resp.status_code}"},
            )
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "detail": str(e)})


@router.post("/api/test/forgejo")
async def test_forgejo(_user: User = Depends(require_admin_role)) -> JSONResponse:
    import httpx

    token = settings.forgejo_token
    base = settings.forgejo_base_url.rstrip("/")
    if not token or "example.com" in base:
        raise HTTPException(
            status_code=400, detail="Forgejo token or base URL not configured"
        )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base}/api/v1/user",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                return JSONResponse(
                    content={"ok": True, "user": resp.json().get("login", "unknown")}
                )
            return JSONResponse(
                status_code=400,
                content={"ok": False, "detail": f"HTTP {resp.status_code}"},
            )
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "detail": str(e)})


@router.post("/api/test/mcp")
async def test_mcp(_user: User = Depends(require_admin_role)) -> JSONResponse:
    import asyncio
    import json
    import os

    command = settings.mcp_server_command
    env = os.environ.copy()
    env.update(settings.mcp_server_env_with_webhooks)

    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            "-transport",
            "stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ci-review-agent-test", "version": "0.1.0"},
            },
        }
        proc.stdin.write((json.dumps(init) + "\n").encode())
        await proc.stdin.drain()
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
            proc.terminate()
            resp = json.loads(line.decode())
            if "result" in resp:
                return JSONResponse(
                    content={
                        "ok": True,
                        "server": resp["result"]
                        .get("serverInfo", {})
                        .get("name", "unknown"),
                    }
                )
            return JSONResponse(content={"ok": False, "detail": "Unexpected response"})
        except asyncio.TimeoutError:
            proc.terminate()
            return JSONResponse(
                status_code=502,
                content={"ok": False, "detail": "MCP server did not respond"},
            )
    except FileNotFoundError:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "detail": f"Binary not found: {command}"},
        )
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "detail": str(e)})


@router.post("/api/test/messaging")
async def test_messaging(_user: User = Depends(require_admin_role)) -> JSONResponse:
    import asyncio
    import json
    import os

    command = settings.mcp_server_command
    env = os.environ.copy()
    env.update(settings.mcp_server_env_with_webhooks)

    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            "-transport",
            "stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ci-review-agent-test", "version": "0.1.0"},
            },
        }
        proc.stdin.write((json.dumps(init) + "\n").encode())
        await proc.stdin.drain()
        await asyncio.wait_for(proc.stdout.readline(), timeout=5)

        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        proc.stdin.write((json.dumps(notif) + "\n").encode())
        await proc.stdin.drain()

        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "send_alert",
                "arguments": {
                    "platform": settings.messaging_platform,
                    "incident_title": "CI Agent Connection Test",
                    "root_cause": "Test notification from CI Review Agent. Ignore.",
                    "resolution_steps": "No action required.",
                },
            },
        }
        proc.stdin.write((json.dumps(call) + "\n").encode())
        await proc.stdin.drain()

        line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        proc.terminate()
        resp = json.loads(line.decode())
        if "result" in resp:
            if not resp["result"].get("isError", False):
                return JSONResponse(
                    content={"ok": True, "detail": "Test notification sent"}
                )
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "detail": resp["result"]
                    .get("content", [{}])[0]
                    .get("text", "MCP error"),
                },
            )
        return JSONResponse(content={"ok": False, "detail": "Unexpected response"})
    except FileNotFoundError:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "detail": f"Binary not found: {command}"},
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "detail": "MCP server did not respond"},
        )
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "detail": str(e)})
