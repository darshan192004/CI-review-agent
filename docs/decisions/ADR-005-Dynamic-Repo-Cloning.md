# ADR-005: Dynamic Repository Cloning for Multi-Repo CI Self-Healing

## Status
Accepted

## Date
2026-07-27

## Context
The CI Review Agent currently operates on a single, hardcoded local repository path (`GIT_REPO_PATH=/tmp/test-failing-ci`). This design was sufficient for proof-of-concept demonstrations but prevents the agent from:

1. Handling CI failures across multiple repositories in an organization
2. Operating as a production service for arbitrary webhook-triggered repos
3. Scaling to multiple tenants or orgs
4. Providing true "agentic" behavior where the agent autonomously locates and fixes any failing repo

When a webhook arrives for `owner/repo`, the agent must be able to:
- Clone the specific repository
- Checkout the failing commit/branch
- Apply the LLM-generated patch
- Commit and push the fix
- Trigger a new CI run

## Decision
Implement **dynamic repository cloning** with isolated temporary workspaces per CI run.

### Architecture Changes

1. **New Method**: `GitManager.clone_repo(owner, repo, branch, commit_sha, token)`
   - Uses `tempfile.mkdtemp()` for isolated workspace per run
   - Clones via HTTP with embedded PAT: `http://<token>@forgejo/owner/repo.git`
   - Checks out the specific commit SHA
   - Cleans up workspace after graph execution completes

2. **Graph Changes**:
   - `apply_fix` node now creates a `GitManager` with the dynamic workspace path
   - State carries `workspace_path` through the graph
   - `finally` block ensures cleanup even on failure

3. **Webhook Handler Changes**:
   - Extracts `owner/repo`, `branch`, `commit_sha` from payload
   - Passes these to graph initial state
   - Graph constructs clone URL from `FORGEJO_BASE_URL` + repository path

4. **Retry Logic Enhancement**:
   - When patch application fails, retry with modified prompt
   - When LLM returns empty diff, force fallback to full-file replacement
   - Max 3 attempts per CI run before escalation

## Alternatives Considered

### Static Repo Mapping
- **Pros**: Simple, no cloning overhead
- **Cons**: Requires pre-staging every repo; doesn't scale; not truly autonomous
- **Rejected**: Defeats the purpose of an agent that handles arbitrary failures

### GitArchive API
- **Pros**: No full clone needed; faster for read-only source access
- **Cons**: Cannot push patches back; read-only
- **Rejected**: Agent must commit and push fixes

### Shared Workspace with Branch Isolation
- **Pros**: Saves disk space; faster for repeated runs
- **Cons**: Race conditions with concurrent runs; complex cleanup; state leakage
- **Rejected:** Temporary isolated workspaces are safer and simpler

## Consequences

- Agent now truly handles arbitrary repos without pre-configuration
- Disk usage increases temporarily during patch generation (cleaned up after)
- Network overhead: one `git clone` per failed run (acceptable for CI cadence)
- Need to manage PAT token securely in workspace (embedded in clone URL only for duration of operation)
- Graph execution time increases by ~2-5s for clone + checkout

## Implementation Notes

```python
# services/git_manager.py
async def clone_repo(self, owner: str, repo: str, branch: str, commit_sha: str, token: str) -> Path:
    workspace = Path(tempfile.mkdtemp(prefix=f"ci-agent-{owner}-{repo}-"))
    clone_url = f"http://{token}@{self.base_url}/{owner}/{repo}.git"
    # Sparse checkout for efficiency
    # Checkout specific commit
    # Return workspace path
```

## Related ADRs
- ADR-001: LangGraph Stateful Engine (graph structure)
- ADR-002: FastAPI + HTMX Web UI (dashboard will show multi-repo runs)
- ADR-004: Multi-LLM Provider Abstraction (LLM generates patches)

## References
- [docs/PRODUCT_AND_TECHNICAL_DOCUMENTATION.md](../PRODUCT_AND_TECHNICAL_DOCUMENTATION.md) — Full product roadmap
- Issue #5: Dynamic repo cloning implementation
