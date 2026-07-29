# Plan: Local Forgejo Fork + Docker Runner + Global Org Webhooks + Agent

## 0. Constraint Check

- Forgejo fork is at `/home/darshan.parmar/Desktop/forgejo`
- CI-review-agent is at `/home/darshan.parmar/Desktop/CI-review-agent`
- Agent will run on host port `8000` by default
- Goal: ONE webhook configuration at org level, not per-repo

---

## 1. Build & Start Local Forgejo Instance

Since there is no docker-compose in the fork, start Forgejo by building from source or using the existing Dockerfile.

### Option A: Build Docker Image from Fork (fastest for local)

```bash
cd /home/darshan.parmar/Desktop/forgejo
docker build -t local-forgejo:latest .
```

Then run:

```bash
docker run -d \
  --name forgejo \
  -p 3000:3000 \
  -p 2222:22 \
  -e FORGEJO__database__DB_TYPE=sqlite3 \
  -e FORGEJO__database__PATH=/data/forgejo.db \
  -e FORGEJO__server__ROOT_URL=http://localhost:3000 \
  -e FORGEJO__server__HTTP_PORT=3000 \
  -v forgejo-data:/data \
  --restart unless-stopped \
  local-forgejo:latest
```

### Option B: Run Binary Directly (simpler for testing)

```bash
cd /home/darshan.parmar/Desktop/forgejo
make go-check generate-backend static-executable
FORGEJO__database__DB_TYPE=sqlite3 \
FORGEJO__database__PATH=/tmp/forgejo.db \
FORGEJO__server__ROOT_URL=http://localhost:3000 \
./gitea web
```

### Bootstrap Admin

```bash
# If using Docker:
docker exec -it forgejo /usr/bin/s6-svscan/control /etc/s6 gitea shutdown || true
docker exec -it forgejo /usr/bin/entrypoint gitea admin create-user --admin --username admin --password admin123 --email admin@localhost

# If using binary directly:
FORGEJO__database__DB_TYPE=sqlite3 ./gitea admin create-user --admin --username admin --password admin123 --email admin@localhost
```

### Get Runner Registration Token

```bash
curl -u admin:admin123 http://localhost:3000/api/v1/admin/actions/runners/registration-token
```

---

## 2. Configure Docker Self-Hosted Runner

Use the official Forgejo runner image:

```bash
REG_TOKEN=$(curl -s -u admin:admin123 http://localhost:3000/api/v1/admin/actions/runners/registration-token | jq -r '.token')

docker run -d \
  --name forgejo-runner \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e FORGEJO_RUNNER_REGISTRATION_TOKEN="$REG_TOKEN" \
  -e FORGEJO_RUNNER_URL=http://host.docker.internal:3000 \
  -e FORGEJO_RUNNER_NAME=docker-runner \
  -e FORGEJO_RUNNER_WORK_PATH=/data \
  -v runner-data:/data \
  --restart unless-stopped \
  code.forgejo.org/forgejo/runner:latest
```

> **Note**: If runner runs inside Docker and Forgejo also inside Docker, use `host.docker.internal` or create a shared Docker network. If Forgejo runs on host binary, `localhost:3000` works from runner container.

Verify runner appears online in Forgejo UI: **Site Admin → Actions → Runners**.

---

## 3. Agent Configuration ()

Update `/home/darshan.parmar/Desktop/CI-review-agent/.env`:

```env
FORGEJO_TOKEN=<PAT with repo and actions scope, created in Forgejo UI>
FORGEJO_BASE_URL=http://localhost:3000
FORGEJO_WEBHOOK_SECRET=<random string for HMAC verification>
```

Start agent:

```bash
cd /home/darshan.parmar/Desktop/CI-review-agent
python main.py serve --host 0.0.0.0 --port 8000
```

---

## 4. Global Organization Webhook (No Per-Repo Config)

Forgejo supports **organization-level webhooks** that fire for all repos in that org.

### Create Org Webhook via API

```bash
ORG_NAME=your-org-name
AGENT_URL=http://localhost:8000
WEBHOOK_SECRET=<same as FORGEJO_WEBHOOK_SECRET in agent .env>

# 1. Create a PAT with admin:org scope to manage org webhooks
# 2. Create the webhook:
curl -X POST "http://localhost:3000/api/v1/orgs/$ORG_NAME/hooks" \
  -H "Authorization: Bearer $ORG_ADMIN_PAT" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "forgejo",
    "config": {
      "url": "'$AGENT_URL'/webhook/forgejo",
      "content_type": "json",
      "secret": "'$WEBHOOK_SECRET'"
    },
    "events": [
      "workflow_run",
      "push"
    ],
    "active": true,
    "branch_filter": "*"
  }'
```

**What this does**: Every CI webhook event for every repo in the org hits `http://<agent>:8000/webhook/forgejo` automatically. No per-repo setup needed.

### Verify

```bash
# List org webhooks:
curl "http://localhost:3000/api/v1/orgs/$ORG_NAME/hooks" \
  -H "Authorization: Bearer $ORG_ADMIN_PAT"
```

Repo-level webhooks are no longer required. New repos added to the org will also trigger the org webhook.

---

## 5. System-Level Webhook (Instance-Wide, All Orgs)

If you want **every repo across every org and user** on the instance to trigger the agent, use a system webhook instead:

```bash
curl -X POST "http://localhost:3000/api/v1/admin/hooks" \
  -H "Authorization: Bearer $ADMIN_PAT" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "forgejo",
    "config": {
      "url": "'$AGENT_URL'/webhook/forgejo",
      "content_type": "json",
      "secret": "'$WEBHOOK_SECRET'"
    },
    "events": [
      "workflow_run",
      "push"
    ],
    "active": true
  }'
```

> **Trade-off**: System hooks are simpler but fire for every event on the instance. Org hooks are scoped and safer for multi-tenant instances.

---

## 6. End-to-End Validation Flow

1. Forgejo running on `localhost:3000`
2. Runner online in Forgejo UI
3. Agent running on `localhost:8000`
4. Org webhook pointing to agent
5. Push commit to test repo in org
6. Runner executes workflow
7. Workflow fails → Forgejo sends `workflow_run` event to agent
8. Agent fetches logs, sends to LLM, returns patch
9. Agent applies patch, commits, pushes
10. New CI run triggers automatically
11. Dashboard `http://<agent>:8000/` and `/runs` track all runs persistently

---

## 7. Required User Inputs

| Variable | Where to get |
|----------|--------------|
| `ORG_NAME` | Your Forgejo organization name |
| `ORG_ADMIN_PAT` | PAT with `admin:org` scope from org admin |
| `ADMIN_PAT` | PAT with `admin:org` or `sudo` scope (for system hooks) |
| `AGENT_URL` | Host IP/domain reachable by Forgejo VM (e.g., `http://<agent-ip>:8000`) |

---

## 8. Implementation Checklist

1. Build Forgejo image: `cd ~/Desktop/forgejo && docker build -t local-forgejo:latest .`
2. Start Forgejo container with ports 3000/2222 and SQLite volume
3. Bootstrap admin user via `gitea admin create-user`
4. Get runner registration token via API
5. Start runner container with token, URL, and Docker socket mount
6. Update agent `.env` with Forgejo token, base URL, webhook secret
7. Start agent: `python main.py serve --host 0.0.0.0 --port 8000`
8. Create org webhook via curl (or use admin API script)
9. Push test commit to verify end-to-end flow
10. Dashboard tracks runs persistently in SQLite

---

## 9. Notes

- The persistent SQLite tracker is already implemented in this repo (`.ci_runs.db`)
- Agent handles Forgejo webhook signature verification if `FORGEJO_WEBHOOK_SECRET` is set
- Runner needs Docker socket access (`-v /var/run/docker.sock:/var/run/docker.sock`) to spin CI containers
- If agent and Forgejo are on different hosts, use the agent's VM IP in the webhook URL, not `localhost`
