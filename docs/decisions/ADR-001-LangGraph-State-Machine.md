# ADR-001: Use LangGraph for Stateful CI Self-Healing Execution Loop

## Status
Accepted

## Date
2026-07-26

## Context
The CI Review Agent requires a resilient execution engine to automate the end-to-end workflow of handling broken CI runs:
1. Extract failure logs from GitHub Actions or Forgejo CI.
2. Send log context to an LLM for root-cause diagnosis and code patch generation.
3. Apply code fixes, commit, and push to a remote repository.
4. Poll CI status to verify whether the fix succeeded.
5. Loop back for up to $N$ retry attempts if CI fails again, or escalate to human engineers via universal messaging if retries exhaust.

Key requirements:
- Stateful loop management with persistent checkpoints.
- Strict control over state transitions (`RUNNING` → `PROCESSING` → `PASSED` / `FAILED` / `ESCALATED`).
- Ability to inspect execution history and recover state across server restarts.

## Decision
Use **LangGraph** (with `StateGraph` and `AsyncSqliteSaver` / `InMemorySaver`) as the core orchestrator for the CI self-healing cycle.

## Alternatives Considered

### Custom Async Loop / Python `while` Loop
- **Pros**: Minimal dependencies, simple to implement for a basic script.
- **Cons**: Lack of structured state checkpointing; brittle error recovery; hard to visually trace node transitions; requires custom state machine boilerplates.
- **Rejected**: Custom loops become unmaintainable when adding branching logic, human escalation gates, and persistent state checkpoints.

### Temporal / Celery Workflow Engine
- **Pros**: Enterprise-grade durability, distributed queue processing.
- **Cons**: High operational overhead, heavy external dependencies (Redis/RabbitMQ/Temporal cluster).
- **Rejected**: Overkill for a lightweight, self-contained CI review agent that needs to run as a single python daemon or CLI binary.

## Consequences
- Every workflow run is modeled as a explicit node transition (`fetch_logs` → `analyze_failure` → `apply_fix` → `verify_ci` → `send_notification`).
- State is checkpointed into SQLite (`checkpoints.db`) after each node, allowing seamless resume across crashes or server restarts.
- Easy to extend with new graph nodes (e.g. static analysis, security scanners) without altering existing node logic.
