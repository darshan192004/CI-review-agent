from graph import build_graph, route_ci_outcome


class TestRouteCiOutcome:
    def test_passed_routes_to_success(self):
        state = {"ci_status": "PASSED", "attempt_count": 1}
        assert route_ci_outcome(state) == "notify_success"

    def test_failed_under_max_routes_to_fix(self):
        state = {"ci_status": "FAILED", "attempt_count": 2}
        assert route_ci_outcome(state) == "fix_code"

    def test_failed_at_max_routes_to_escalation(self):
        state = {"ci_status": "FAILED", "attempt_count": 3}
        assert route_ci_outcome(state) == "notify_human_escalation"

    def test_first_failure_routes_to_fix(self):
        state = {"ci_status": "FAILED", "attempt_count": 1}
        assert route_ci_outcome(state) == "fix_code"

    def test_running_routes_to_fix(self):
        state = {"ci_status": "RUNNING", "attempt_count": 2}
        assert route_ci_outcome(state) == "fix_code"

    def test_cannot_fix_routes_to_escalation(self):
        state = {"ci_status": "CANNOT_FIX", "attempt_count": 1}
        assert route_ci_outcome(state) == "notify_human_escalation"


class TestBuildGraph:
    def test_graph_compiles(self):
        graph = build_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        graph = build_graph()
        node_names = list(graph.get_graph().nodes)
        assert "fetch_logs_and_alert" in node_names
        assert "llm_fix_code" in node_names
        assert "poll_ci_status" in node_names
        assert "notify_success" in node_names
        assert "notify_human_escalation" in node_names

    def test_graph_has_expected_edges(self):
        graph = build_graph()
        edges = [(e[0], e[1]) for e in graph.get_graph().edges]
        assert ("__start__", "fetch_logs_and_alert") in edges
        assert ("fetch_logs_and_alert", "llm_fix_code") in edges
        assert ("llm_fix_code", "poll_ci_status") in edges
        assert ("notify_success", "__end__") in edges
        assert ("notify_human_escalation", "__end__") in edges
