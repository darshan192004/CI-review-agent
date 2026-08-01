from graph import build_graph, route_clone_outcome, route_fix_outcome
from nodes import _detect_infrastructure_error, _parse_repair_analysis


class TestRouteCloneOutcome:
    def test_clone_failure_routes_to_human_escalation(self):
        state = {"ci_status": "CANNOT_FIX"}
        assert route_clone_outcome(state) == "notify_human_escalation"

    def test_clone_success_routes_to_fetch_logs(self):
        state = {"ci_status": "RUNNING"}
        assert route_clone_outcome(state) == "fetch_logs_and_alert"


class TestDetectInfrastructureError:
    def test_detects_git_auth_failure(self):
        logs = "fatal: could not read Username for 'http://localhost:3000': terminal prompts disabled"
        assert _detect_infrastructure_error(logs)

    def test_detects_access_denied(self):
        logs = "remote: Permission to testadmin/test-failing-ci.git denied to testadmin."
        assert _detect_infrastructure_error(logs)

    def test_detects_repo_not_found(self):
        logs = "ERROR: Repository not found.\nfatal: repository 'x.git' does not exist"
        assert _detect_infrastructure_error(logs)

    def test_detects_missing_token(self):
        logs = "Error: No token provided. Set FORGEJO_TOKEN before running."
        assert _detect_infrastructure_error(logs)

    def test_detects_connection_refused(self):
        logs = "urllib3.exceptions.MaxRetryError ... Connection refused"
        assert _detect_infrastructure_error(logs)

    def test_returns_empty_for_code_failure(self):
        logs = "tests/test_calculator.py::test_add FAILED\nassert 1 + 2 == 3"
        assert _detect_infrastructure_error(logs) == ""

    def test_returns_empty_for_empty_logs(self):
        assert _detect_infrastructure_error("") == ""

    def test_detects_git_fetch_connection_failure(self):
        logs = (
            "fatal: unable to access 'http://localhost:3000/testadmin/test-failing-ci/': "
            "Failed to connect to localhost port 3000 after 0 ms: "
            "Couldn't connect to server"
        )
        assert _detect_infrastructure_error(logs)

    def test_detects_runner_path_escapes(self):
        logs = (
            "❌  Failure - Main actions/checkout@v4\n"
            "⚙️ [runner]: copyDir: failed to copy content to container: "
            "Error response from daemon: statat var/run/act/actions/x: "
            "path escapes from parent"
        )
        assert _detect_infrastructure_error(logs)

    def test_detects_runner_exec_failure(self):
        logs = (
            "OCI runtime exec failed: exec failed: unable to start container process: "
            'exec: "node": executable file not found in $PATH'
        )
        assert _detect_infrastructure_error(logs)

    def test_detects_exitcode_127(self):
        logs = "Error occurred running finally: exitcode '127': command not found"
        assert _detect_infrastructure_error(logs)

    def test_exitcode_1_is_not_infrastructure(self):
        """A normal test failure exits with code 1 — must NOT be misclassified
        as an infrastructure error (regression: exitcode '1' skipped LLM fix)."""
        logs = "=================== FAILURES ===================\ntest_calc.py::test_subtract FAILED\nexitcode '1'"
        assert _detect_infrastructure_error(logs) == ""


class TestParseRepairAnalysis:
    def test_parses_plain_json(self):
        raw = (
            '{"explanation": "Fixed multiply bug", "modified_files": '
            '[{"file_path": "calculator.py", "content": "def multiply(a, b):\\n    return a * b"}]}'
        )
        result = _parse_repair_analysis(raw)
        assert result.explanation == "Fixed multiply bug"
        assert result.modified_files[0].file_path == "calculator.py"
        assert result.modified_files[0].content == "def multiply(a, b):\n    return a * b"

    def test_parses_markdown_code_fence(self):
        raw = '```json\n{"explanation": "x", "modified_files": []}\n```'
        result = _parse_repair_analysis(raw)
        assert result.explanation == "x"
        assert result.modified_files == []

    def test_repairs_invalid_single_quote_escapes(self):
        raw = (
            '{"explanation": "raise AssertionError(\\\'failed\\\')", '
            '"modified_files": [{"file_path": "calculator.py", '
            '"content": "raise ValueError(\\\'Division by zero\\\')"}]} '
        )
        result = _parse_repair_analysis(raw)
        assert result.explanation == "raise AssertionError('failed')"
        assert result.modified_files[0].content == "raise ValueError('Division by zero')"

    def test_parses_json_embedded_in_prose(self):
        raw = 'Here is my analysis:\n{"explanation": "root cause", "modified_files": []}\nHope that helps!'
        result = _parse_repair_analysis(raw)
        assert result.explanation == "root cause"

    def test_filters_invalid_modified_file_entries(self):
        raw = (
            '{"explanation": "x", "modified_files": ['
            '{"file_path": "a.py", "content": "ok"}, '
            '{"file_path": "", "content": "empty path"}, '
            '{"content": "missing path"}, "not-an-object"]}'
        )
        result = _parse_repair_analysis(raw)
        assert [f.file_path for f in result.modified_files] == ["a.py"]

    def test_missing_explanation_raises(self):
        import pytest

        with pytest.raises(ValueError):
            _parse_repair_analysis('{"modified_files": []}')

    def test_invalid_json_raises(self):
        import pytest

        with pytest.raises(ValueError):
            _parse_repair_analysis("this is not json")


class TestRouteFixOutcome:
    def test_fix_outcome_for_passed(self):
        # PASSED (LLM found nothing to change) terminates the single-pass
        # graph; the external webhook loop owns any follow-up.
        state = {"ci_status": "PASSED", "attempt_count": 1}
        assert route_fix_outcome(state) == "end"

    def test_fix_pushed_terminates_graph(self):
        # A successful push must terminate the graph run so the webhook handler
        # can record last_fix_sha on the still-active session (bug 1). Re-running
        # llm_fix_code would re-clone, re-push (non-fast-forward) and destroy the
        # session lineage.
        state = {"ci_status": "FIX_PUSHED", "attempt_count": 1}
        assert route_fix_outcome(state) == "end"

    def test_failed_under_max_routes_to_fix(self):
        state = {"ci_status": "FAILED", "attempt_count": 1}
        assert route_fix_outcome(state) == "fix_code"

    def test_failed_at_max_routes_to_escalation(self):
        state = {"ci_status": "FAILED", "attempt_count": 3}
        assert route_fix_outcome(state) == "notify_human_escalation"

    def test_first_failure_routes_to_fix(self):
        state = {"ci_status": "FAILED", "attempt_count": 1}
        assert route_fix_outcome(state) == "fix_code"

    def test_running_routes_to_fix(self):
        state = {"ci_status": "RUNNING", "attempt_count": 1}
        assert route_fix_outcome(state) == "fix_code"

    def test_cannot_fix_routes_to_escalation(self):
        state = {"ci_status": "CANNOT_FIX", "attempt_count": 1}
        assert route_fix_outcome(state) == "notify_human_escalation"

    def test_timeout_routes_to_escalation_not_retry(self):
        state = {"ci_status": "TIMEOUT", "attempt_count": 1}
        assert route_fix_outcome(state) == "notify_human_escalation"

    def test_cancelled_routes_to_escalation_not_retry(self):
        state = {"ci_status": "CANCELLED", "attempt_count": 1}
        assert route_fix_outcome(state) == "notify_human_escalation"


class TestBuildGraph:
    def test_graph_compiles(self):
        graph = build_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        graph = build_graph()
        node_names = list(graph.get_graph().nodes)
        assert "clone_repository" in node_names
        assert "fetch_logs_and_alert" in node_names
        assert "llm_fix_code" in node_names
        assert "notify_human_escalation" in node_names

    def test_graph_has_expected_edges(self):
        graph = build_graph()
        edges = [(e[0], e[1]) for e in graph.get_graph().edges]
        assert ("__start__", "clone_repository") in edges
        assert ("clone_repository", "fetch_logs_and_alert") in edges
        assert ("clone_repository", "notify_human_escalation") in edges
        assert ("fetch_logs_and_alert", "llm_fix_code") in edges
        assert ("llm_fix_code", "llm_fix_code") in edges
        assert ("llm_fix_code", "notify_human_escalation") in edges
        assert ("llm_fix_code", "__end__") in edges
        assert ("notify_human_escalation", "__end__") in edges
