"""Command-line interface for frameio-mcp."""

from __future__ import annotations

import asyncio
import sys
import time
import webbrowser
from getpass import getpass

import click

from . import __version__
from .auth import (
    AuthError,
    build_authorize_url,
    clear_tokens,
    exchange_code_for_tokens,
    generate_state,
    load_tokens,
    save_tokens,
)
from .config import Config


@click.group()
@click.version_option(version=__version__, prog_name="frameio-mcp")
def cli() -> None:
    """Frame.io MCP — connect Claude to Frame.io."""


@cli.command()
def login() -> None:
    """Run OAuth login with Adobe IMS. Saves tokens to ~/.frameio-mcp/tokens.json."""
    try:
        config = Config.from_env()
    except EnvironmentError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    state = generate_state()
    auth_url = build_authorize_url(config, state)

    click.echo("Opening browser to Adobe login…")
    click.echo(f"\nIf the browser doesn't open, paste this URL manually:\n\n{auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    click.echo("After signing in with your Adobe ID, you'll land on the OAuth callback page.")
    click.echo("Copy the authorization code shown there and paste it below.\n")

    # getpass hides the input in the terminal — good for auth codes even though
    # they're one-time and not as sensitive as passwords
    code = getpass("Authorization code: ").strip()
    if not code:
        click.echo("No code entered. Aborting.", err=True)
        sys.exit(1)

    try:
        tokens = asyncio.run(exchange_code_for_tokens(config, code))
        save_tokens(tokens, config.tokens_path)
    except AuthError as e:
        click.echo(f"\n✗ Login failed: {e}", err=True)
        sys.exit(1)

    click.echo(f"\n✓ Saved tokens to {config.tokens_path}")
    remaining = int(tokens.expires_at - time.time())
    click.echo(f"✓ Access token valid for {remaining}s ({remaining // 60}min)")
    if tokens.refresh_token:
        click.echo("✓ Refresh token stored — server will auto-refresh in the background")
    else:
        click.echo(
            "⚠ No refresh token returned. Check that 'offline_access' is in your OAuth "
            "scopes (Adobe Developer Console → OAuth Web App → Scopes)."
        )


@cli.command()
def logout() -> None:
    """Delete the saved tokens file."""
    try:
        config = Config.from_env()
    except EnvironmentError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if clear_tokens(config.tokens_path):
        click.echo(f"✓ Removed tokens from {config.tokens_path}")
    else:
        click.echo(f"No tokens file at {config.tokens_path} — already logged out")


@cli.command()
def status() -> None:
    """Show current authentication status."""
    try:
        config = Config.from_env()
    except EnvironmentError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    try:
        tokens = load_tokens(config.tokens_path)
    except AuthError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    if tokens is None:
        click.echo("Not authenticated. Run `frameio-mcp login`.")
        return

    if tokens.is_expired:
        click.echo(
            f"Access token expired — will refresh automatically on next API call."
        )
    else:
        remaining = int(tokens.expires_at - time.time())
        click.echo(
            f"✓ Authenticated. Access token valid for another {remaining}s "
            f"(~{remaining // 60}min)."
        )

    if tokens.account_id:
        click.echo(f"  Default account_id: {tokens.account_id}")
    click.echo(f"  Tokens path: {config.tokens_path}")
    click.echo(f"  OAuth relay: {config.oauth_relay_url}")


@cli.command()
def serve() -> None:
    """Run the MCP server over stdio (for use with Claude Desktop, Code, Cowork)."""
    from .server import mcp
    mcp.run()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
