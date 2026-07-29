"""Tests for CLI command ergonomics."""

from __future__ import annotations

import json
import time

import pytest
from click.testing import CliRunner

from frameio_mcp.auth import Tokens
from frameio_mcp.cli import cli
from frameio_mcp.config import resolve_tokens_path


@pytest.fixture
def no_adobe_credentials(monkeypatch):
    """Simulate a machine that has never been configured with Adobe credentials."""
    monkeypatch.delenv("FRAMEIO_CLIENT_ID", raising=False)
    monkeypatch.delenv("FRAMEIO_CLIENT_SECRET", raising=False)


class TestResolveTokensPath:
    def test_honours_the_override_env_var(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom.json"
        monkeypatch.setenv("FRAMEIO_TOKENS_PATH", str(custom))
        assert resolve_tokens_path() == custom

    def test_defaults_under_the_home_directory(self, monkeypatch):
        monkeypatch.delenv("FRAMEIO_TOKENS_PATH", raising=False)
        assert resolve_tokens_path().name == "tokens.json"


class TestLogout:
    def test_works_without_adobe_credentials(
        self, no_adobe_credentials, monkeypatch, tmp_path
    ):
        """Deleting a local token file must not require a client secret.

        Requiring credentials to log out blocks the one command a user reaches for
        when their credentials are the thing that is wrong.
        """
        tokens_file = tmp_path / "tokens.json"
        tokens_file.write_text(json.dumps({
            "access_token": "a",
            "refresh_token": "r",
            "expires_at": time.time() + 3600,
        }))
        monkeypatch.setenv("FRAMEIO_TOKENS_PATH", str(tokens_file))

        result = CliRunner().invoke(cli, ["logout"])

        assert result.exit_code == 0, result.output
        assert not tokens_file.exists()

    def test_reports_cleanly_when_no_tokens_exist(
        self, no_adobe_credentials, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("FRAMEIO_TOKENS_PATH", str(tmp_path / "absent.json"))

        result = CliRunner().invoke(cli, ["logout"])

        assert result.exit_code == 0
        assert "already logged out" in result.output


class TestStatus:
    def test_works_without_adobe_credentials(
        self, no_adobe_credentials, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("FRAMEIO_TOKENS_PATH", str(tmp_path / "absent.json"))

        result = CliRunner().invoke(cli, ["status"])

        assert result.exit_code == 0, result.output
        assert "Not authenticated" in result.output

    def test_reports_remaining_validity(
        self, no_adobe_credentials, monkeypatch, tmp_path
    ):
        tokens_file = tmp_path / "tokens.json"
        tokens_file.write_text(json.dumps({
            "access_token": "a",
            "refresh_token": "r",
            "expires_at": time.time() + 3600,
            "account_id": "acct-1",
        }))
        monkeypatch.setenv("FRAMEIO_TOKENS_PATH", str(tokens_file))

        result = CliRunner().invoke(cli, ["status"])

        assert result.exit_code == 0
        assert "Authenticated" in result.output
        assert "acct-1" in result.output


class TestLogin:
    @pytest.fixture
    def adobe_credentials(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FRAMEIO_CLIENT_ID", "test-client")
        monkeypatch.setenv("FRAMEIO_CLIENT_SECRET", "test-secret")
        monkeypatch.setenv("FRAMEIO_TOKENS_PATH", str(tmp_path / "tokens.json"))

    def test_code_option_skips_the_interactive_prompt(
        self, adobe_credentials, monkeypatch
    ):
        """Pasting into a hidden prompt fails in some terminals, so --code is the escape hatch."""
        exchanged = {}

        async def fake_exchange(config, code):
            exchanged["code"] = code
            return Tokens(
                access_token="a", refresh_token="r", expires_at=time.time() + 3600
            )

        monkeypatch.setattr("frameio_mcp.cli.exchange_code_for_tokens", fake_exchange)

        result = CliRunner().invoke(cli, ["login", "--code", "  abc-123  "])

        assert result.exit_code == 0, result.output
        assert exchanged["code"] == "abc-123", "surrounding whitespace must be stripped"

    def test_empty_code_option_aborts(self, adobe_credentials):
        result = CliRunner().invoke(cli, ["login", "--code", "   "])

        assert result.exit_code == 1
        assert "No code entered" in result.output

    def test_no_browser_flag_still_prints_the_url(self, adobe_credentials, monkeypatch):
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

        result = CliRunner().invoke(cli, ["login", "--no-browser"], input="\n")

        assert not opened, "--no-browser must not launch a browser"
        assert "ims-na1.adobelogin.com" in result.output

    def test_interactive_prompt_echoes_input(self, adobe_credentials, monkeypatch):
        """A hidden prompt is the defect being fixed; the typed code must be visible."""
        monkeypatch.setattr("webbrowser.open", lambda url: None)

        async def fake_exchange(config, code):
            return Tokens(
                access_token="a", refresh_token="r", expires_at=time.time() + 3600
            )

        monkeypatch.setattr("frameio_mcp.cli.exchange_code_for_tokens", fake_exchange)

        result = CliRunner().invoke(cli, ["login"], input="typed-code-999\n")

        assert result.exit_code == 0, result.output
        assert "typed-code-999" in result.output


class TestVerify:
    def test_write_flag_requires_a_url(self, no_adobe_credentials):
        result = CliRunner().invoke(cli, ["verify", "--write"])

        assert result.exit_code == 1
        assert "--write requires --url" in result.output

    def test_missing_credentials_explains_the_env_setup(self, no_adobe_credentials):
        """verify genuinely needs credentials (it may refresh), so it must say so clearly."""
        result = CliRunner().invoke(cli, ["verify"])

        assert result.exit_code == 1
        assert "FRAMEIO_CLIENT_ID" in result.output
        assert ".env" in result.output
