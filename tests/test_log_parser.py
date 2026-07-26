from services.log_parser import parse_ci_logs, strip_ansi


class TestStripAnsi:
    def test_strips_color_codes(self):
        assert strip_ansi("\x1b[31mERROR\x1b[0m") == "ERROR"

    def test_strips_multiple_codes(self):
        assert strip_ansi("\x1b[1m\x1b[32mOK\x1b[0m\x1b[0m") == "OK"

    def test_no_ansi_passthrough(self):
        assert strip_ansi("plain text") == "plain text"

    def test_empty_string(self):
        assert strip_ansi("") == ""


class TestParseCiLogs:
    def test_extracts_error_lines(self):
        log = "line1\nline2\nERROR: something broke\nline4\nline5"
        result = parse_ci_logs(log)
        assert "ERROR: something broke" in result
        assert ">>>" in result

    def test_extracts_traceback(self):
        log = "normal line\nTraceback (most recent call last):\n  File test.py\nAssertionError"
        result = parse_ci_logs(log)
        assert "Traceback" in result
        assert "AssertionError" in result

    def test_extracts_fail_lines(self):
        log = "step1\nFAIL: test_foo failed\nstep3"
        result = parse_ci_logs(log)
        assert "FAIL: test_foo failed" in result

    def test_strips_ansi_from_logs(self):
        log = "\x1b[31mERROR: broken\x1b[0m"
        result = parse_ci_logs(log)
        assert "ERROR: broken" in result
        assert "\x1b" not in result

    def test_includes_context_lines(self):
        lines = [""] * 10 + ["ERROR: bug"] + [""] * 10
        log = "\n".join(lines)
        result = parse_ci_logs(log)
        assert "L6: " in result or "L16: " in result

    def test_truncates_long_output(self):
        log = "ERROR: " + "x" * 20000
        result = parse_ci_logs(log, max_chars=500)
        assert len(result) <= 530
        assert "[truncated]" in result

    def test_no_errors_returns_cleaned_log(self):
        log = "everything is fine\nno errors here"
        result = parse_ci_logs(log)
        assert "everything is fine" in result

    def test_empty_log(self):
        result = parse_ci_logs("")
        assert result == "" or "truncated" in result
