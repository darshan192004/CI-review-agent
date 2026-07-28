# Comprehensive Technical Specification & Blueprint: Production-Grade Autonomous CI/CD Self-Healing Agent

---

## 1. Executive Summary & Core Architectural Shift

This specification details the end-to-end overhaul required to take the **CI Review Agent** from a local prototype to a production- and enterprise-grade autonomous system.

### Core Architectural Changes
1. **Dynamic Workspace Management:** Replaces hardcoded path assumptions with context-managed temporary workspaces (`tempfile.mkdtemp`), cloning repositories dynamically using Personal Access Tokens (PAT).
2. **Deterministic Code Application:** Replaces fragile `git apply` / Unified Diff patches with **Pydantic Structured JSON Outputs** from the LLM, overwriting file contents cleanly and safely.
3. **Composite Webhook Keying:** Fixes dropped event bugs on CI restarts by tracking runs with `(repository_full_name, run_id, run_attempt)`.
4. **Real-Time UI Synchronization:** Replaces HTTP polling with **Server-Sent Events (SSE)** integrated directly with HTMX.
5. **Production & Enterprise Readiness:** Implements sandboxed isolation, audit trails, secrets management, and observability matrices.

---

## 2. End-to-End System Architecture

```
                  ┌─────────────────────────────────────────────────────────┐
                  │                      Git Platform                       │
                  │                   (Forgejo / GitHub)                    │
                  └────────────────────────────┬────────────────────────────┘
                                               │ Webhook Event (HTTP POST)
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ FastAPI Webhook Ingestion Service                                                       │
│ 1. Validate HMAC Signature                                                              │
│ 2. Deduplicate using (repo, run_id, attempt)                                            │
│ 3. Enqueue job into Redis                                                               │
└──────────────────────────────────────────────┬──────────────────────────────────────────┘
                                               │
                                               ▼
                                      ┌─────────────────┐
                                      │   Redis Queue   │
                                      └────────┬────────┘
                                               │ Async Worker Task
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ LangGraph Agent Worker Node                                                             │
│                                                                                         │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐    ┌───────────┐  │
│  │  1. Fetch Logs   │───►│   2. AI Repair  │───►│ 3. Workspace Fix │───►│ 4. Push   │  │
│  │  (Forgejo API)   │    │  (Gemini Model)  │    │ (Temp Git Manager│    │  & Retry  │  │
│  └──────────────────┘    └──────────────────┘    └──────────────────┘    └───────────┘  │
└──────────────────────────────────────────────┬──────────────────────────────────────────┘
                                               │ State Updates & Logs
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ Persistence & Real-time Layer                                                           │
│ - PostgreSQL DB: Track runs, tokens, and audit trails                                   │
│ - Redis Pub/Sub: Drive live SSE updates to HTMX Dashboard                               │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Detailed Component Implementations

### 3.1 Dynamic Workspace Git Manager (`services/git_manager.py`)

This component manages isolated temporary workspaces, dynamic cloning with authentication, file path safety checks, and atomic commit/push operations.

```python
import os
import shutil
import tempfile
import git
from typing import List, Dict, Any

class WorkspaceGitManager:
    """
    Context manager for safely cloning, patching, and pushing git repository fixes
    in an isolated temporary directory.
    """
    def __init__(self, clone_url: str, token: str, branch: str, commit_sha: str):
        # Inject token into URL for authenticated cloning
        if "://" in clone_url:
            proto, rest = clone_url.split("://", 1)
            self.authenticated_url = f"{proto}://{token}@{rest}"
        else:
            self.authenticated_url = clone_url

        self.branch = branch
        self.commit_sha = commit_sha
        self.temp_dir = None
        self.repo = None

    def __enter__(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ci_agent_ws_")
        
        # Clone repository at the target branch
        self.repo = git.Repo.clone_from(
            self.authenticated_url,
            self.temp_dir,
            branch=self.branch
        )

        # Configure isolated committer identity
        with self.repo.config_writer() as config:
            config.set_value("user", "name", "CI Repair Agent")
            config.set_value("user", "email", "ci-agent@autofix.internal")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def apply_file_changes(self, modified_files: List[Dict[str, str]]) -> bool:
        """
        Overwrites file contents directly based on structured LLM output.
        Prevents directory traversal vulnerabilities.
        """
        try:
            for file_info in modified_files:
                rel_path = file_info["file_path"].lstrip("/")
                
                # Security Check: Prevent Directory Traversal
                full_path = os.path.abspath(os.path.join(self.temp_dir, rel_path))
                if not full_path.startswith(os.path.abspath(self.temp_dir)):
                    raise ValueError(f"Directory traversal detected: {rel_path}")

                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(file_info["content"])
                    
            return True
        except Exception as e:
            print(f"[WorkspaceGitManager] Error applying file changes: {e}")
            return False

    def commit_and_push(self, commit_message: str) -> bool:
        """Stages all changes, creates a commit, and pushes to remote."""
        try:
            if not self.repo.is_dirty(untracked_files=True):
                print("[WorkspaceGitManager] No changes detected to commit.")
                return False

            self.repo.git.add(A=True)
            self.repo.index.commit(commit_message)

            origin = self.repo.remote(name="origin")
            origin.push(refspec=f"{self.branch}:{self.branch}")
            print(f"[WorkspaceGitManager] Successfully pushed fixes to branch {self.branch}")
            return True
        except Exception as e:
            print(f"[WorkspaceGitManager] Commit and push failed: {e}")
            return False
```


## Module 2: Structured Output Prompts & LangGraph Node
**File:** `nodes.py`

Replaces raw unified diff generation with strict Pydantic schema enforcement to guarantee actionable code fixes.

```python
from pydantic import BaseModel, Field
from typing import List
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from services.git_manager import WorkspaceGitManager

class FileFix(BaseModel):
    file_path: str = Field(description="Relative path of the file to update.")
    content: str = Field(description="The complete updated file content.")

class RepairAnalysis(BaseModel):
    explanation: str = Field(description="Root cause analysis of the CI failure.")
    modified_files: List[FileFix] = Field(description="List of files to overwrite.")

async def node_analyze_and_fix(state: dict) -> dict:
    """Analyzes CI logs and generates structured file modifications."""
    ci_logs = state.get("ci_logs", "")
    repo_info = state.get("repo_info", {})
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are an automated CI repair agent. Analyze the provided CI build logs "
            "and produce exact code modifications to resolve the errors. "
            "Return a structured JSON object containing root cause explanation and "
            "complete file contents for any files needing changes."
        )),
        ("human", "Repo: {repo_name}\nCommit: {commit_sha}\n\nCI Error Logs:\n{logs}")
    ])
    
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.1)
    structured_llm = llm.with_structured_output(RepairAnalysis)
    
    chain = prompt | structured_llm
    response: RepairAnalysis = await chain.ainvoke({
        "repo_name": repo_info.get("name"),
        "commit_sha": repo_info.get("commit_sha"),
        "logs": ci_logs
    })
    
    # Apply changes using the temporary workspace manager
    success = False
    with WorkspaceGitManager(
        clone_url=repo_info["clone_url"],
        token=repo_info["token"],
        branch=repo_info["branch"],
        commit_sha=repo_info["commit_sha"]
    ) as git_ws:
        
        file_changes = [f.model_dump() for f in response.modified_files]
        if git_ws.apply_file_changes(file_changes):
            commit_msg = f"fix(ci): auto-reproduce fix [Attempt {state.get('attempt_count', 1)}]\n\n{response.explanation}"
            success = git_ws.commit_and_push(commit_msg)

    return {
        "explanation": response.explanation,
        "patch_applied": success,
        "attempt_count": state.get("attempt_count", 0) + 1
    }
```

## Module 3: Webhook Handler with Re-run Deduplication
**File:** `services/webhook_handler.py`

Ensures manual CI restarts generate unique composite task keys rather than getting dropped as duplicates.

```python
import hashlib
from typing import Dict, Any, Optional

def extract_run_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts normalized CI metadata across Forgejo and GitHub schemas."""
    workflow_run = payload.get("workflow_run") or payload.get("action_run") or {}
    repo = payload.get("repository", {})
    
    run_id = str(workflow_run.get("id") or payload.get("run_id", "0"))
    # Increment run_attempt on manual restarts
    run_attempt = str(workflow_run.get("run_attempt", 1))
    repo_name = repo.get("full_name") or f"{repo.get('owner', {}).get('username')}/{repo.get('name')}"

    # Composite key guarantees uniqueness across manual retries
    composite_key = f"{repo_name}:{run_id}:{run_attempt}"

    return {
        "task_key": composite_key,
        "repo_name": repo_name,
        "clone_url": repo.get("clone_url"),
        "run_id": run_id,
        "run_attempt": run_attempt,
        "event_action": payload.get("action"),
        "status": workflow_run.get("status"),
        "conclusion": workflow_run.get("conclusion"),
        "branch": workflow_run.get("head_branch"),
        "commit_sha": workflow_run.get("head_sha")
    }

async def process_incoming_webhook(payload: Dict[str, Any], tracker, event_queue):
    meta = extract_run_metadata(payload)
    
    # 1. Pipeline queued or running
    if meta["status"] in ["queued", "in_progress"]:
        await tracker.upsert_run(meta["task_key"], status="RUNNING", metadata=meta)
        await event_queue.put({"task_key": meta["task_key"], "status": "RUNNING", "meta": meta})

    # 2. Pipeline finished
    elif meta["status"] == "completed":
        if meta["conclusion"] == "failure":
            await tracker.upsert_run(meta["task_key"], status="FAILED", metadata=meta)
            await event_queue.put({"task_key": meta["task_key"], "status": "FAILED", "meta": meta})
            
            # Trigger background repair agent
            return meta  # Ready for agent execution
            
        elif meta["conclusion"] == "success":
            await tracker.upsert_run(meta["task_key"], status="PASSED", metadata=meta)
            await event_queue.put({"task_key": meta["task_key"], "status": "PASSED", "meta": meta})

    return None
```

## Module 4: Real-Time SSE Dashboard Server
**File:** `server.py`

Provides high-efficiency, zero-dependency real-time event streaming to the frontend.

```python
import asyncio
import json
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="CI Review Agent Dashboard")
templates = Jinja2Templates(directory="ui/templates")

# Event queue for broadcasting state updates
event_bus: asyncio.Queue = asyncio.Queue()

@app.get("/api/events")
async def sse_endpoint(request: Request):
    """Streams real-time updates directly to HTMX via SSE."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            
            event_data = await event_bus.get()
            # Render HTMX-compatible partial HTML row
            html_snippet = f"""
            <tr id="run-{event_data['task_key']}" hx-swap-oob="outerHTML">
                <td class="p-2 font-mono text-sm">{event_data['task_key']}</td>
                <td class="p-2 font-bold status-{event_data['status'].lower()}">{event_data['status']}</td>
                <td class="p-2 text-xs text-gray-400">{event_data['meta'].get('commit_sha', 'N/A')[:7]}</td>
            </tr>
            """
            yield f"event: ci_update\ndata: {html_snippet.replace('\n', '')}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```


## HTMX Dashboard Integration
**File:** `ui/templates/index.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>CI Agent Control Plane</title>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <script src="https://unpkg.com/htmx.org/dist/ext/sse.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .status-running { color: #f59e0b; }
        .status-failed { color: #ef4444; }
        .status-passed { color: #10b981; }
    </style>
</head>
<body class="bg-gray-900 text-white p-8">
    <h1 class="text-2xl font-bold mb-6">CI Self-Healing Dashboard</h1>
    
    <!-- Connect to Server-Sent Events stream -->
    <div hx-ext="sse" sse-connect="/api/events" sse-swap="ci_update">
        <table class="w-full text-left bg-gray-800 rounded-lg overflow-hidden">
            <thead class="bg-gray-700">
                <tr>
                    <th class="p-3">Task Key (Repo:ID:Attempt)</th>
                    <th class="p-3">Status</th>
                    <th class="p-3">Commit SHA</th>
                </tr>
            </thead>
            <tbody id="ci-run-list">
                <!-- Rows injected dynamically via SSE -->
            </tbody>
        </table>
    </div>
</body>
</html>
```


## 3. End-to-End Workflow Testing Plan

Perform these verification steps to validate the system.

```text
+-----------------------------------------------------------------------------------+
| Verification Steps                                                                |
|                                                                                   |
|  [Test Case 1] Auto-Fix Trigger   --> Verify clone, code fix, commit, & push      |
|  [Test Case 2] Manual CI Re-run   --> Verify unique composite key generation      |
|  [Test Case 3] Retry Limit Guard  --> Verify halt & alert on max attempts reached |
|  [Test Case 4] Path Traversal Security --> Verify rejection of malicious paths    |
+-----------------------------------------------------------------------------------+
```

### Test Case 1: Initial Failure and Autonomous Fix

**Setup:** Push a broken Python script containing a missing import or syntax error to a test repository on Forgejo/GitHub.

**Execution:** Allow the CI pipeline to fail.

**Verification:**

- Confirm FastAPI receives the webhook and generates `repo/name:run_id:1`.
- Confirm WorkspaceGitManager creates an isolated `/tmp/ci_agent_ws_*` directory.
- Confirm Gemini returns structured JSON with the corrected file content.
- Confirm the agent pushes a fix commit (`fix(ci): auto-reproduce fix...`).
- Confirm the target repository triggers a new CI build automatically.

### Test Case 2: Manual CI Restart (Deduplication Test)

**Setup:** Go to the Forgejo/GitHub UI and click "Re-run failed jobs" on an existing run.

**Execution:** Observe the agent dashboard.

**Verification:**

- Verify the webhook key updates to `repo/name:run_id:2`.
- Verify the dashboard updates the row status to RUNNING without dropping the event.

### Test Case 3: Retry Exhaustion & Escalation

**Setup:** Introduce an unfixable build error (e.g., non-existent dependency in private registry).

**Execution:** Let the loop run up to `MAX_RETRY_ATTEMPTS = 3`.

**Verification:**

- Verify the agent stops looping after 3 attempts.
- Verify state is marked as EXHAUSTED.
- Verify an escalation event is dispatched to Slack/Mattermost/Discord.

### Test Case 4: Security & Path Traversal Guardrail

**Setup:** Mock an LLM response containing a file path like `../../../../etc/passwd`.

**Execution:** Trigger the `apply_file_changes` method.

**Verification:**

- Verify the `ValueError("Directory traversal attack detected")` is caught.
- Verify no files outside the isolated workspace directory are modified.


## 4. Production-Grade & Enterprise-Grade Assessment Framework

Use these checklists to determine if your system meets enterprise deployment standards.

### Production-Grade Readiness Checklist

| Category | Requirement | Implementation Target | Status |
|----------|-------------|----------------------|--------|
| Reliability | Task Queueing | Move background execution to Redis/ARQ or Celery workers. | [ ] |
| Persistence | Durable Storage | Replace SQLite with PostgreSQL for multi-instance deployments. | [ ] |
| Resilience | State Checkpointing | Use LangGraph PostgresSaver to persist graph state across restarts. | [ ] |
| Observability | Tracing & Metrics | Integrate OpenTelemetry / Prometheus metrics (fix_success_rate, latency). | [ ] |
| Security | Secret Management | Store GitHub/Forgejo PATs in Vault or cloud secrets manager. | [ ] |
| Security | Webhook Security | Reject requests failing HMAC-SHA256 signature checks. | [ ] |

### Enterprise-Grade Readiness Checklist

| Feature | Description | Target Specification |
|---------|-------------|---------------------|
| Multi-Tenancy | Organization-level isolation | Support distinct PATs, LLM keys, and webhook secrets per organization. |
| RBAC & Governance | Access control & approvals | Option to require human sign-off before code fixes are pushed to production branches. |
| Audit Compliance | Tamper-proof logging | Store complete LLM prompts, proposed patches, and execution logs in immutable storage. |
| Rate Limiting | DDoS & cost control | Enforce token budgets per repository/organization to limit API expenditure. |
| Deployment Packaging | Infrastructure as Code | Provide Helm charts for Kubernetes or Docker Compose bundles for on-premise installs. |


## 5. Quantitative Evaluation Scorecard

Evaluate your deployment against these target metrics:

```text
+-----------------------------------------------------------------------------------+
| Enterprise KPI Targets                                                            |
|                                                                                   |
|  * MTTR Reduction Target:       > 60% decrease in mean time to resolution         |
|  * Autonomous Fix Rate:         > 40% of standard CI failures fixed without human |
|  * False Positive Push Rate:    < 2% invalid fix attempts pushed to remote        |
|  * Webhook Delivery SLA:        99.9% uptime on ingestion control plane           |
+-----------------------------------------------------------------------------------+
```

---

## The Solution

Instead of asking the LLM for a diff and running `git apply`, ask the LLM for structured JSON containing the full modified file content or explicit search/replace blocks, then write the files directly to an isolated temporary workspace.

## Production GitManager Implementation

Below is a complete, production-ready replacement for `services/git_manager.py` that handles isolated dynamic cloning using standard Python temporary directories and authenticated URLs:

```python
import os
import shutil
import tempfile
import git
from typing import List, Dict

class WorkspaceGitManager:
    def __init__(self, clone_url: str, token: str, branch: str, commit_sha: str):
        # Inject PAT token into clone URL for non-interactive auth
        # http://token@localhost:3000/owner/repo.git
        if "://" in clone_url:
            proto, rest = clone_url.split("://", 1)
            self.authenticated_url = f"{proto}://{token}@{rest}"
        else:
            self.authenticated_url = clone_url
            
        self.branch = branch
        self.commit_sha = commit_sha
        self.temp_dir = None
        self.repo = None

    def __enter__(self):
        """Creates an isolated temp directory and clones the repository."""
        self.temp_dir = tempfile.mkdtemp(prefix="ci_agent_ws_")
        
        # Clone repository
        self.repo = git.Repo.clone_from(
            self.authenticated_url, 
            self.temp_dir, 
            branch=self.branch
        )
        
        # Set Git committer identity for this session
        with self.repo.config_writer() as config:
            config.set_value("user", "name", "CI Review Bot")
            config.set_value("user", "email", "ci-bot@autofix.internal")
            
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up the directory on completion or failure."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def apply_file_changes(self, modified_files: List[Dict[str, str]]) -> bool:
        """
        Applies changes safely by overwriting file contents directly.
        modified_files format: [{"file_path": "src/main.py", "content": "..."}]
        """
        try:
            for file_info in modified_files:
                relative_path = file_info["file_path"].lstrip("/")
                # Security check: Prevent path traversal attacks
                full_path = os.path.abspath(os.path.join(self.temp_dir, relative_path))
                if not full_path.startswith(os.path.abspath(self.temp_dir)):
                    raise ValueError(f"Directory traversal detected: {relative_path}")

                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(file_info["content"])
            return True
        except Exception as e:
            print(f"[GitManager] Failed to apply changes: {e}")
            return False

    def commit_and_push(self, commit_message: str) -> bool:
        """Stages, commits, and pushes changes back to the remote branch."""
        try:
            if not self.repo.is_dirty(untracked_files=True):
                print("[GitManager] No changes detected in repository workspace.")
                return False

            self.repo.git.add(A=True)
            self.repo.index.commit(commit_message)
            
            # Push changes to the target branch
            origin = self.repo.remote(name="origin")
            origin.push(refspec=f"{self.branch}:{self.branch}")
            print(f"[GitManager] Successfully pushed fixes to branch {self.branch}")
            return True
        except Exception as e:
            print(f"[GitManager] Failed to commit and push: {e}")
            return False
```


## How to Prompt the LLM for Reliable File Modifications

Modify your LangGraph analyze node to enforce a structured JSON schema:

```json
{
  "explanation": "Fixed missing import in main.py that caused CI failure.",
  "modified_files": [
    {
      "file_path": "src/main.py",
      "content": "import sys\n\ndef main():\n    print('Hello World')\n"
    }
  ]
}
```

This eliminates `git apply` syntax failures completely.

## 2. Fixing Webhook Deduplication & CI Re-runs

### Why Manual CI Restarts Are Ignored

**Run Key Collisions:** When a user clicks "Re-run failed jobs" in Forgejo/GitHub, the system fires an `action_run` webhook with the same `run_id` or `commit_sha`.

**Deduplication Trap:** If your server checks `_active_tasks` or SQLite using only `run_id`, it drops the event thinking it is a duplicate.

### The Fix

#### A. Primary Key Strategy

Identify CI executions using a composite key: `(repository_full_name, run_id, run_attempt)`.

```python
def extract_run_metadata(payload: dict) -> dict:
    workflow_run = payload.get("workflow_run") or payload.get("action_run") or {}
    return {
        "repo_owner": payload["repository"]["owner"]["username"],
        "repo_name": payload["repository"]["name"],
        "clone_url": payload["repository"]["clone_url"],
        "run_id": str(workflow_run.get("id")),
        # GitHub/Forgejo sends run_attempt; fallback to timestamp hash if absent
        "run_attempt": str(workflow_run.get("run_attempt", 1)),
        "event_action": payload.get("action"), # 'requested', 'completed', 're-started'
        "status": workflow_run.get("status"),   # 'queued', 'in_progress', 'completed'
        "conclusion": workflow_run.get("conclusion"), # 'success', 'failure', None
        "branch": workflow_run.get("head_branch"),
        "commit_sha": workflow_run.get("head_sha")
    }
```

#### B. State Transition Rules

In `webhook_handler.py`, process events based on their lifecycle status:

```python
task_key = f"{meta['repo_name']}:{meta['run_id']}:{meta['run_attempt']}"

if meta["status"] in ["queued", "in_progress"]:
    # Update UI status to "RUNNING"
    await run_tracker.update_status(task_key, status="IN_PROGRESS")
    await broadcast_ui_update(task_key, "IN_PROGRESS")

elif meta["status"] == "completed":
    if meta["conclusion"] == "failure":
        await run_tracker.update_status(task_key, status="FAILED")
        await broadcast_ui_update(task_key, "FAILED")
        
        # Trigger the LangGraph Agent in background task
        asyncio.create_task(trigger_agent_fix_workflow(meta))
        
    elif meta["conclusion"] == "success":
        await run_tracker.update_status(task_key, status="PASSED")
        await broadcast_ui_update(task_key, "PASSED")
```

#### C. HTMX / Server-Sent Events (SSE) Real-Time UI Sync

Stop using HTTP long-polling on the dashboard. Use Server-Sent Events (SSE) with HTMX:

**In FastAPI (`server.py`):**

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import asyncio

@app.get("/api/events")
async def event_stream():
    async def sse_generator():
        while True:
            # Consume events from an in-memory queue or Redis pub/sub
            data = await event_queue.get()
            yield f"event: ci_update\ndata: {data}\n\n"
            
    return StreamingResponse(sse_generator(), media_type="text/event-stream")
```

**In HTMX UI Dashboard (`index.html`):**

```html
<!-- Connect HTMX to SSE stream -->
<div hx-ext="sse" sse-connect="/api/events" sse-swap="ci_update">
    <div id="ci-run-list">
        <!-- Live rows swapped dynamically here -->
    </div>
</div>
```

## 4. Complete Action Plan for Production Readiness

### Phase 1: Core Repair (Immediate)

- **Swap Git Logic:** Replace `git_manager.py` with the context-managed `WorkspaceGitManager` above.
- **Switch to Structured JSON:** Update the prompt template in `nodes.py` to ask Gemini for full file contents in JSON format rather than unified diffs.
- **Fix Re-runs:** Update webhook payload extraction to use `run_id` + `run_attempt`.

### Phase 2: System Hardening (Week 2)

- **Task Queueing:** Move from inline `asyncio.create_task` to an explicit async worker library like ARQ or Celery with Redis.
- **PostgreSQL Migration:** Migrate `.ci_runs.db` from SQLite to PostgreSQL to handle concurrent database writes from multiple workers.
- **Workspace Isolation:** Run the Git modification and build testing inside ephemeral Docker containers (or sandbox directories) to prevent arbitrary code execution vulnerabilities during auto-fixes.

### Phase 3: Enterprise Features (Week 3+)

- **Fallback Strategy:** If an auto-fix push fails CI a second time, increase the LLM prompt context by attaching the new error logs alongside the previous attempt history.
- **Max Attempts Guardrail:** Limit automatic retries to N=3 attempts (Attempt count ≤ N). If Attempt count > N, trigger human escalation via Mattermost/Slack webhooks.