from __future__ import annotations

import contextlib
import html as html_mod
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import settings
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
from services.messaging import send_alert
from services.run_tracker import run_tracker
from ui.badges import status_badge
from ui.formatters import format_failure_summary, format_run_time
from ui.status import canonical_status

logger = logging.getLogger(__name__)

_UI_DIR = Path(__file__).resolve().parent

router = APIRouter()

templates = Jinja2Templates(directory=str(_UI_DIR / "templates"))
templates.env.globals["status_badge"] = status_badge
templates.env.globals["format_run_time"] = format_run_time
templates.env.globals["format_failure_summary"] = format_failure_summary


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
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid credentials"})

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


def _format_uptime(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}h {minutes}m {secs}s"


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
        "messaging_platform",
        "mattermost_webhook_url",
        "slack_webhook_url",
        "discord_webhook_url",
        "telegram_bot_token",
        "telegram_chat_id",
        "forgejo_webhook_secret",
        "github_webhook_secret",
        "max_retry_attempts",
        "log_max_tokens",
        "checkpointer_type",
        "notification_trigger_level",
        "auto_create_pull_request",
        "auto_fix_reruns",
        "git_repo_path",
        "git_default_branch",
        "forgejo_org",
        "github_org",
        "forgejo_discovery_mode",
        "github_discovery_mode",
        "forgejo_username",
        "github_username",
        "llm_rate_limit_per_minute",
        "llm_rate_limit_burst",
        "llm_max_retries",
        "llm_retry_backoff_seconds",
        "server_host",
        "server_port",
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
    recent = recent[:10]
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
            + counts.get("failure", 0)
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
                    "attempt_count": r.get("attempt_count", 0),
                    "failure_summary": r.get("failure_summary", ""),
                    "patch_summary": r.get("patch_summary", ""),
                    "run_time": format_run_time(r.get("created_at", 0)),
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

    if status:
        all_runs = [r for r in all_runs if canonical_status(r["status"]) == canonical_status(status)]
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
            "run_time": format_run_time(r.get("created_at", 0)),
        }
        for r in all_runs
    ]

    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request,
            "partials/runs_table.html",
            {"runs": runs_data},
        )

    return templates.TemplateResponse(
        request,
        "runs.html",
        {"active_page": "runs", "runs": runs_data},
    )


# ---------------------------------------------------------------------------
# HTMX partials: dashboard runs table + metrics (separate endpoints)
# ---------------------------------------------------------------------------


@router.get("/api/dashboard/partial", response_class=HTMLResponse)
async def dashboard_partial(
    _user: User = Depends(get_current_user),
    repo: str = Query("", alias="repo"),
) -> HTMLResponse:
    recent = await run_tracker.get_all_runs()

    if repo:
        recent = [r for r in recent if r["repository"] == repo]

    rows = ""
    e = html_mod.escape
    for run in recent:
        commit_short = e(run.get("commit_sha", "")[:8]) if run.get("commit_sha") else "\u2014"
        run_time = format_run_time(run.get("created_at", 0))
        rows += f"""<tr id="run-{e(run["repository"])}:{e(run["run_id"])}:{e(run.get("run_attempt", "1"))}">
            <td class="font-mono text-xs text-indigo-400">{e(run["repository"])}</td>
            <td class="font-mono text-xs text-slate-400">{e(run_time)}</td>
            <td class="font-mono text-xs text-slate-400">#{e(run.get("run_attempt", "1"))}</td>
            <td>{status_badge(run["status"])}</td>
            <td class="text-xs text-slate-400 uppercase font-mono">{e(run.get("platform", "\u2014"))}</td>
            <td class="text-xs text-slate-400">{e(run.get("branch", "\u2014"))}</td>
            <td class="text-xs text-slate-400 font-mono">{commit_short}</td>
            <td class="text-xs text-slate-400">{e(run.get("author", "\u2014"))}</td>
        </tr>"""

    html = f"""<div class="overflow-x-auto"><table class="data-table">
      <thead>
        <tr>
          <th scope="col">Repository</th>
          <th scope="col">Run Time (IST)</th>
          <th scope="col">Attempt</th>
          <th scope="col">Status</th>
          <th scope="col">Platform</th>
          <th scope="col">Branch</th>
          <th scope="col">Commit</th>
          <th scope="col">Author</th>
        </tr>
      </thead>
      <tbody>
        {
        rows
        if rows
        else '<tr><td colspan="8" class="p-12 text-center text-slate-400 text-sm">'
        "No webhook runs recorded yet.</td></tr>"
    }
      </tbody>
    </table></div>"""

    return HTMLResponse(content=html)


@router.get("/api/metrics/partial", response_class=HTMLResponse)
async def metrics_partial(
    _user: User = Depends(get_current_user),
    repo: str = Query("", alias="repo"),
) -> HTMLResponse:
    if repo:
        counts = await run_tracker.count_by_status(repo=repo)
        recent = await run_tracker.get_all_runs()
        recent = [r for r in recent if r["repository"] == repo]
        active_cnt = sum(
            1
            for r in recent
            if r["status"] in ("processing", "AGENT_WORKING", "RUNNING", "PENDING", "QUEUED", "WAITING")
        )
        success_cnt = sum(1 for r in recent if r["status"] in ("PASSED", "success"))
        failed_cnt = sum(1 for r in recent if r["status"] in ("FAILED", "failed", "error", "EXHAUSTED"))
    else:
        counts = await run_tracker.count_by_status()
        active_cnt = counts.get("processing", 0) + counts.get("AGENT_WORKING", 0)
        success_cnt = counts.get("PASSED", 0) + counts.get("success", 0)
        failed_cnt = (
            counts.get("FAILED", 0) + counts.get("failed", 0) + counts.get("failure", 0) + counts.get("error", 0)
        )

    uptime = _format_uptime(_get_uptime_seconds())

    html = f"""<p class="text-3xl font-bold text-blue-400 font-mono" id="stat-processing-jobs">{active_cnt}</p>
<p class="text-3xl font-bold text-emerald-400 font-mono" id="stat-succeeded-runs">{success_cnt}</p>
<p class="text-3xl font-bold text-rose-400 font-mono" id="stat-failed-runs">{failed_cnt}</p>
<p class="text-3xl font-bold ci-text-main font-mono" id="stat-system-uptime">{uptime}</p>"""

    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Repo Discovery API
# ---------------------------------------------------------------------------


_BROKEN_STATUSES = frozenset({"FAILED", "failed", "EXHAUSTED", "error"})


@router.get("/api/repos")
async def list_repos(_user: User = Depends(get_current_user)) -> JSONResponse:
    """List all known repos with latest status, sorted broken-first.

    Merges repos from two sources:
      1. Webhook history (repos that have sent webhooks)
      2. CI API discovery (repos the token can access), across both platforms.

    Returns a structured payload so the UI can distinguish "connected",
    "not configured", and "error" states instead of showing an empty dropdown.
    """
    from services.repo_discovery import discover_repos, get_known_repos, is_discovery_configured

    known_repos = await get_known_repos()

    results = [await discover_repos("forgejo"), await discover_repos("github")]
    ok_results = [r for r in results if r["status"] == "ok"]

    discovered: list[str] = []
    for result in ok_results:
        for repo in result["repos"]:
            if repo not in discovered:
                discovered.append(repo)

    seen: set[str] = set(discovered)
    all_repos = list(discovered)
    for r in known_repos:
        if r not in seen:
            seen.add(r)
            all_repos.append(r)

    all_runs = await run_tracker.get_all_runs()
    latest: dict[str, str] = {}
    for run in all_runs:
        latest[run["repository"]] = run["status"]

    def _dot_color(status: str | None) -> str:
        if status is None:
            return "slate"
        if status in ("PASSED", "success"):
            return "emerald"
        if status in _BROKEN_STATUSES:
            return "rose"
        return "blue"

    def _sort_key(repo: str) -> tuple[int, str]:
        status = latest.get(repo)
        is_broken = 0 if (status and status in _BROKEN_STATUSES) else 1
        return (is_broken, repo.lower())

    all_repos.sort(key=_sort_key)

    repos_data = [
        {
            "name": repo,
            "status": latest.get(repo, "unknown"),
            "dot_color": _dot_color(latest.get(repo)),
        }
        for repo in all_repos
    ]

    status, detail = _summarize_discovery(results)

    return JSONResponse(
        content={
            "repos": repos_data,
            "status": status,
            "detail": detail,
            "configured": is_discovery_configured(),
            "disable_popup": settings.disable_auto_discovery_popup == "true",
        }
    )


def _summarize_discovery(results: list[dict[str, Any]]) -> tuple[str, str]:
    ok = [r for r in results if r["status"] == "ok"]
    if ok:
        return "ok", " ".join(r["detail"] for r in ok)
    errors = [r for r in results if r["status"] == "error"]
    if errors:
        return "error", errors[0]["detail"]
    return "not_configured", "No CI token configured. Add a Forgejo or GitHub token in Settings."


@router.post("/api/discovery/dismiss")
async def dismiss_discovery(request: Request, _user: User = Depends(require_admin_role)) -> JSONResponse:
    """Mark discovery as configured so the first-run popup stops showing.

    The optional ``disable_auto_discovery_popup`` checkbox persists a global
    preference to bypass the popup entirely on future logins (otherwise the
    popup shows on every login until the admin configures discovery).
    """
    form = await request.form()
    never_again = str(form.get("disable_auto_discovery_popup", "")).strip().lower() in ("1", "true", "on", "yes")
    updates = {"discovery_configured": "true"}
    settings.discovery_configured = "true"
    if never_again:
        updates["disable_auto_discovery_popup"] = "true"
        settings.disable_auto_discovery_popup = "true"
    write_env(updates)
    return JSONResponse(content={"ok": True})


@router.get("/api/webhook-health")
async def webhook_health(_user: User = Depends(get_current_user)) -> JSONResponse:
    """Report webhook delivery health for the repos currently being monitored.

    Returns ``{repo: {"status": "healthy" | "stale"}}`` for every repo the
    CI token can discover. A repo is *healthy* when the agent has recorded CI
    activity for it locally (runs seeded from delivered webhooks or a history
    sync) and *stale* when it is discovered but the agent has never seen a
    run from it — a strong hint its webhook is not configured.

    Returns an empty object when nothing is configured; the dashboard then
    hides the health line entirely.
    """
    from services.repo_discovery import discover_repos, get_known_repos

    seen: set[str] = set(await get_known_repos())
    health: dict[str, dict[str, str]] = {}

    for platform in ("forgejo", "github"):
        result = await discover_repos(platform)
        if result["status"] != "ok":
            continue
        for repo in result["repos"]:
            health[repo] = {"status": "healthy" if repo in seen else "stale"}

    return JSONResponse(content=health)


# ---------------------------------------------------------------------------
# History Seed API
# ---------------------------------------------------------------------------


@router.get("/api/runs/history", response_class=HTMLResponse)
async def seed_run_history(
    _user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Fetch recent CI runs from provider API to seed the dashboard.

    Called once on dashboard page load (HTMX hx-trigger="load").
    Seeds the run_tracker with historical data, then returns the runs
    table HTML partial so the dashboard populates immediately.
    """
    from services.repo_discovery import fetch_repo_run_history

    try:
        count = await fetch_repo_run_history()
        logger.info("History seed complete: %d runs recorded", count)
    except Exception as e:
        logger.error("History seed failed: %s", e)

    recent = await run_tracker.get_all_runs()
    rows = ""
    e = html_mod.escape
    for run in recent:
        commit_short = e(run.get("commit_sha", "")[:8]) if run.get("commit_sha") else "\u2014"
        run_time = format_run_time(run.get("created_at", 0))
        rows += f"""<tr id="run-{e(run["repository"])}:{e(run["run_id"])}:{e(run.get("run_attempt", "1"))}">
            <td class="font-mono text-xs text-indigo-400">{e(run["repository"])}</td>
            <td class="font-mono text-xs text-slate-400">{e(run_time)}</td>
            <td class="font-mono text-xs text-slate-400">#{e(run.get("run_attempt", "1"))}</td>
            <td>{status_badge(run["status"])}</td>
            <td class="text-xs text-slate-400 uppercase font-mono">{e(run.get("platform", "\u2014"))}</td>
            <td class="text-xs text-slate-400">{e(run.get("branch", "\u2014"))}</td>
            <td class="text-xs text-slate-400 font-mono">{commit_short}</td>
            <td class="text-xs text-slate-400">{e(run.get("author", "\u2014"))}</td>
        </tr>"""

    html = f"""<div class="overflow-x-auto"><table class="data-table">
      <thead>
        <tr>
          <th scope="col">Repository</th>
          <th scope="col">Run Time (IST)</th>
          <th scope="col">Attempt</th>
          <th scope="col">Status</th>
          <th scope="col">Platform</th>
          <th scope="col">Branch</th>
          <th scope="col">Commit</th>
          <th scope="col">Author</th>
        </tr>
      </thead>
      <tbody>
        {
        rows
        if rows
        else '<tr><td colspan="8" class="p-12 text-center text-slate-400 text-sm">No CI history found.</td></tr>'
    }
      </tbody>
    </table></div>"""

    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Settings API
# ---------------------------------------------------------------------------


@router.get("/api/settings")
async def get_settings(_user: User = Depends(get_current_user)) -> dict[str, str]:
    return read_env_redacted()


_INT_SETTING_KEYS = {
    "server_port",
    "max_retry_attempts",
    "log_max_tokens",
    "llm_rate_limit_per_minute",
    "llm_rate_limit_burst",
    "llm_max_retries",
}
_FLOAT_SETTING_KEYS = {"llm_retry_backoff_seconds"}
_SETTING_NUMERIC_DEFAULTS = {
    "server_port": "8000",
    "max_retry_attempts": "3",
    "log_max_tokens": "4000",
    "llm_rate_limit_per_minute": "0",
    "llm_rate_limit_burst": "0",
    "llm_max_retries": "3",
    "llm_retry_backoff_seconds": "2.0",
}


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
        "mattermost_webhook_url",
        "slack_webhook_url",
        "discord_webhook_url",
        "telegram_bot_token",
        "telegram_chat_id",
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
        "forgejo_discovery_mode",
        "github_discovery_mode",
        "forgejo_username",
        "github_username",
        "discovery_configured",
        "max_retry_attempts",
        "log_max_tokens",
        "checkpointer_type",
        "notification_trigger_level",
        "auto_create_pull_request",
        "auto_fix_reruns",
        "git_repo_path",
        "git_default_branch",
        "server_host",
        "server_port",
        "forgejo_webhook_secret",
        "github_webhook_secret",
        "llm_rate_limit_per_minute",
        "llm_rate_limit_burst",
        "llm_max_retries",
        "llm_retry_backoff_seconds",
    }

    # Filter out redacted values ("••••••••") so we don't overwrite real secrets
    updates = {k: str(v).strip() for k, v in body.items() if k in allowed_keys and str(v).strip() != "••••••••"}

    # Empty numeric fields fall back to their defaults so ".env" never stores ""
    # (an empty int would fail pydantic validation on the next Settings() load).
    for k in list(updates):
        if k in _SETTING_NUMERIC_DEFAULTS and updates[k] == "":
            updates[k] = _SETTING_NUMERIC_DEFAULTS[k]

    if updates:
        write_env(updates)
        for k, v in updates.items():
            if not hasattr(settings, k):
                continue
            if k in _INT_SETTING_KEYS:
                with contextlib.suppress(ValueError):
                    setattr(settings, k, int(v))
            elif k in _FLOAT_SETTING_KEYS:
                with contextlib.suppress(ValueError):
                    setattr(settings, k, float(v))
            else:
                setattr(settings, k, v)

    return JSONResponse(content={"saved": len(updates), "keys": list(updates.keys())})


@router.post("/api/trigger-test-run")
async def trigger_test_run(
    _user: User = Depends(require_admin_role),
    repo: str = Query("", alias="repo"),
) -> JSONResponse:
    from server import broadcast_event

    import random

    run_id = str(random.randint(1000, 9999))
    if not repo:
        repo = "owner/ci-test-repo"
    attempt = "1"
    task_key = f"{repo}:{run_id}:{attempt}"
    await run_tracker.record(repository=repo, run_id=run_id, status="processing")
    broadcast_event(
        task_key=task_key,
        status="processing",
        meta={
            "repository": repo,
            "run_id": run_id,
            "run_attempt": attempt,
            "platform": "",
            "branch": "",
            "commit_sha": "",
            "author": "",
        },
    )

    import asyncio

    async def _complete():
        await asyncio.sleep(2)
        await run_tracker.update_status(repository=repo, run_id=run_id, status="PASSED")
        broadcast_event(
            task_key=task_key,
            status="PASSED",
            meta={
                "repository": repo,
                "run_id": run_id,
                "run_attempt": attempt,
                "platform": "",
                "branch": "",
                "commit_sha": "",
                "author": "",
            },
        )

    asyncio.create_task(_complete())
    return JSONResponse(content={"ok": True, "run_id": run_id, "repository": repo})


@router.post("/api/clear-history")
async def clear_history(_user: User = Depends(require_admin_role)) -> JSONResponse:
    await run_tracker.clear()
    return JSONResponse(content={"ok": True, "message": "History cleared"})


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
        return JSONResponse(status_code=400, content={"ok": False, "detail": "GitHub token not configured"})
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {settings.github_token}"},
            )
            if resp.status_code == 200:
                return JSONResponse(content={"ok": True, "user": resp.json().get("login", "unknown")})
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
        return JSONResponse(
            status_code=400,
            content={"ok": False, "detail": "Forgejo token or base URL not configured"},
        )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base}/api/v1/user",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                return JSONResponse(content={"ok": True, "user": resp.json().get("login", "unknown")})
            return JSONResponse(
                status_code=400,
                content={"ok": False, "detail": f"HTTP {resp.status_code}"},
            )
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "detail": str(e)})


@router.post("/api/test/messaging")
async def test_messaging(_user: User = Depends(require_admin_role)) -> JSONResponse:
    platform = settings.messaging_platform
    try:
        detail = await send_alert(
            platform,
            incident_title="CI Agent Connection Test",
            root_cause="Test notification from CI Review Agent. Ignore.",
            resolution_steps="No action required.",
        )
        return JSONResponse(content={"ok": True, "detail": detail})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "detail": str(e)})
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "detail": str(e)})
