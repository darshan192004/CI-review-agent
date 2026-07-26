from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from setup_wizard import _read_existing, _write_env, run_wizard


class TestReadExisting:
    def test_reads_env_file(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text('FOO=bar\nBAZ="quoted"\n# comment\nEMPTY=\n')
        with patch("setup_wizard._ENV_PATH", env_file):
            result = _read_existing()
        assert result["FOO"] == "bar"
        assert result["BAZ"] == "quoted"
        assert result["EMPTY"] == ""

    def test_returns_empty_dict_when_no_file(self, tmp_path: Path) -> None:
        with patch("setup_wizard._ENV_PATH", tmp_path / ".env"):
            result = _read_existing()
        assert result == {}


class TestWriteEnv:
    def test_writes_env_file(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        example_file = tmp_path / ".env.example"
        example_file.write_text("FOO=default\nBAR=keep\n")
        with patch("setup_wizard._ENV_PATH", env_file), patch("setup_wizard._EXAMPLE_PATH", example_file):
            _write_env({"FOO": "new_value", "BAR": ""})
        content = env_file.read_text()
        assert "FOO=new_value" in content
        assert "BAR=keep" in content


class TestSetupWizard:
    def test_setup_command_exists(self) -> None:
        from main import build_parser

        parser = build_parser()
        args = parser.parse_args(["setup"])
        assert args.command == "setup"
