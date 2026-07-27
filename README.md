# CI Review Agent

An autonomous, self-healing CI/CD agent built with **LangGraph**, **FastAPI**, **HTMX**, and **Model Context Protocol (MCP)**. It monitors CI build failures (GitHub Actions / Forgejo CI), diagnoses root causes using Cloud or Local LLMs (OpenAI, Anthropic, Ollama, Azure OpenAI), applies code patches, verifies fixes, and notifies engineering teams.

**Current Status**: Functional MVP — webhook ingestion, LLM analysis, and run tracking are operational. Dynamic repo cloning and reliable patch application are in active development (see [docs/PRODUCT_AND_TECHNICAL_DOCUMENTATION.md](docs/PRODUCT_AND_TECHNICAL_DOCUMENTATION.md)).

---

## ⚡ Quick Start

### 1. Install & Setup
```bash
# Clone the repository
git clone https://github.com/darshan192004/Ci-review-agent.git
cd Ci-review-agent

# Install dependencies
pip install -e .
```

### 2. First-Run Configuration
Run the interactive setup wizard or edit `.env`:
```bash
python setup_wizard.py
```

### 3. Launch Web Server & Dashboard
```bash
python main.py serve
```

Open your browser to access the management UI:
* **Configuration Page:** [http://localhost:8000/config](http://localhost:8000/config)
* **Real-time Dashboard:** [http://localhost:8000/](http://localhost:8000/)
* **Run History Audit:** [http://localhost:8000/runs](http://localhost:8000/runs)

---

## 🏗️ Architecture

```
Webhook (GitHub/Forgejo) ──► FastAPI Web Server ──► LangGraph Agent
                                                         │
                                              ┌──────────┼──────────┐
                                              ▼          ▼          ▼
                                         Fetch Logs  LLM Analyze  Apply Patch
                                              │          │          │
                                              ▼          ▼          ▼
                                           Poll CI ◄── Fix ◄── Git Commit
                                              │
                                              ▼
                                        Notify via MCP ──► Mattermost/Slack/Discord
```

For detailed component descriptions, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## ✨ Features

* **Webhook Integration**: Native receivers for GitHub Actions (`workflow_run`) and Forgejo/Gitea webhooks with HMAC SHA256 signature verification.
* **Multi-LLM Support**:
  * **OpenAI**: `gpt-4o`, `gpt-4o-mini`, `o3-mini`, `o1`
  * **Anthropic**: `claude-3-7-sonnet-latest`, `claude-3-5-haiku-latest`
  * **Ollama (Local LLMs)**: `llama3.3`, `deepseek-r1`, `qwen2.5-coder`
  * **Azure OpenAI Service**
* **Autonomous Self-Correction Loop**: Applies unified diff patches, commits changes, and polls CI runners for up to $N$ configured retry attempts.
* **Universal Messaging via MCP**: Sends structured incident alerts and escalation reports to **Mattermost**, **Slack**, or **Discord**.
* **1-Second Real-Time Web UI**: Fast Jinja2 + HTMX web dashboard featuring live 1s metric syncing, secret toggles, connection testing, and simulation triggers.
* **Persistent Checkpointing**: LangGraph state machine with SQLite checkpointer (`checkpoints.db`) or in-memory execution.

---

## 📘 Documentation & Architecture Decisions (ADRs)

* [API Reference](docs/API.md): Complete REST endpoint and webhook reference.
* [Architecture Deep Dive](docs/ARCHITECTURE.md): Component diagrams, state graph nodes, and data flow.
* **Architecture Decision Records**:
  * [ADR-001: LangGraph Stateful Engine](docs/decisions/ADR-001-LangGraph-State-Machine.md)
  * [ADR-002: FastAPI + HTMX Web UI](docs/decisions/ADR-002-FastAPI-HTMX-Web-UI.md)
  * [ADR-003: MCP Universal Messaging Transport](docs/decisions/ADR-003-Universal-Messaging-MCP-Integration.md)
  * [ADR-004: Multi-LLM Provider Abstraction](docs/decisions/ADR-004-Multi-LLM-Provider-Abstraction.md)

---

## 🛠️ CLI Usage

```bash
# Start webhook server and management dashboard
python main.py serve --host 127.0.0.1 --port 8000

# Run agent directly against a specific CI failure run
python main.py run --repo owner/repo --run-id 12345 --platform github --branch main
```

---

## 🐳 Docker Deployment

```bash
# Build image
docker build -t ci-review-agent .

# Run container
docker run -d -p 8000:8000 --env-file .env ci-review-agent
```

---

## 🧪 Testing & Linting

```bash
# Run unit & integration tests
pytest tests/ -v

# Run linter
ruff check .
```

---

## 📄 License

MIT License &copy; 2026 CI Review Agent Team
