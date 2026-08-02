from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Any

from config import settings
from graph import build_graph
from state import AgentState

logger = logging.getLogger("ci_agent")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CI/CD Self-Healing Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    serve_parser = subparsers.add_parser("serve", help="Start the webhook receiver server")
    serve_parser.add_argument(
        "--host",
        default=settings.server_host,
        help=f"Bind host (default: {settings.server_host})",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=settings.server_port,
        help=f"Bind port (default: {settings.server_port})",
    )

    run_parser = subparsers.add_parser("run", help="Run agent directly against a CI run")
    run_parser.add_argument("--repo", required=True, help="Repository in owner/repo format")
    run_parser.add_argument("--run-id", required=True, help="CI run ID")
    run_parser.add_argument(
        "--platform",
        choices=["github", "forgejo"],
        default="github",
        help="CI platform (default: github)",
    )
    run_parser.add_argument("--branch", default="main", help="Branch name (default: main)")
    run_parser.add_argument("--commit-sha", default="", help="Commit SHA")
    run_parser.add_argument("--source-files", nargs="*", help="Source file paths for LLM context")
    run_parser.add_argument(
        "--messaging-platform",
        choices=["mattermost", "slack", "discord", "telegram"],
        default="mattermost",
        help="Messaging platform for notifications (default: mattermost)",
    )
    run_parser.add_argument(
        "--dev",
        action="store_true",
        help="Use InMemorySaver instead of AsyncSqliteSaver",
    )

    subparsers.add_parser("setup", help="First-run configuration wizard")

    return parser


async def run_agent(state: AgentState, use_dev_checkpointer: bool = True) -> dict[str, Any]:
    checkpointer = None
    if not use_dev_checkpointer:
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            async with AsyncSqliteSaver.from_conn_string("checkpoints.db") as saver:
                graph = build_graph(checkpointer=saver)
                config = {"configurable": {"thread_id": state.get("run_id", "default")}}
                result = await graph.ainvoke(state, config=config)
                return result
        except Exception as e:
            logger.warning("SQLite checkpointer failed, falling back to InMemorySaver: %s", e)

    graph = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": state.get("run_id", "default")}}
    return await graph.ainvoke(state, config=config)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    logger.info("Starting webhook server on %s:%s", args.host, args.port)
    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=True,
    )


def cmd_run(args: argparse.Namespace) -> None:
    source_files: dict[str, str] = {}
    if args.source_files:
        for path in args.source_files:
            try:
                with open(path, encoding="utf-8") as f:
                    source_files[path] = f.read()
            except FileNotFoundError:
                logger.warning("Source file not found: %s", path)

    if args.messaging_platform != "mattermost":
        settings.messaging_platform = args.messaging_platform

    initial_state: AgentState = {
        "repository": args.repo,
        "branch": args.branch,
        "commit_sha": args.commit_sha,
        "ci_platform": args.platform,
        "run_id": args.run_id,
        "attempt_count": 0,
        "ci_status": "RUNNING",
        "failed_logs": "",
        "llm_analysis": "",
        "explanation": "",
        "patch_applied": False,
        "repo_info": {},
        "notifications_sent": [],
        "source_files": source_files,
        "ci_author": "",
        "failure_summary": "",
        "patch_summary": "",
    }

    shutdown_event = asyncio.Event()

    def signal_handler() -> None:
        logger.info("Received shutdown signal, exiting gracefully...")
        shutdown_event.set()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    try:
        result = loop.run_until_complete(run_agent(initial_state, use_dev_checkpointer=args.dev))
        logger.info("Agent completed. Final status: %s", result.get("ci_status"))
        logger.info("Notifications sent: %s", result.get("notifications_sent", []))
        sys.exit(0)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error("Agent failed: %s", e)
        sys.exit(1)
    finally:
        loop.close()


def main() -> None:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "setup":
        from setup_wizard import run_wizard

        run_wizard()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
