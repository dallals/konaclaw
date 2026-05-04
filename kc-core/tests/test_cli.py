from typer.testing import CliRunner
from kc_core.cli import app


runner = CliRunner()


def test_cli_help_lists_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "kc-chat" in result.stdout or "Usage" in result.stdout


def test_cli_requires_agent_arg():
    result = runner.invoke(app, [])
    assert result.exit_code != 0
