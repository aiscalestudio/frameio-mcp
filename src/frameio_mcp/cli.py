"""Command-line interface for frameio-mcp."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
import webbrowser

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
from .config import Config, resolve_tokens_path


@click.group()
@click.version_option(version=__version__, prog_name="frameio-mcp")
def cli() -> None:
    """Frame.io MCP — connect Claude to Frame.io."""


@cli.command()
@click.option(
    "--code",
    "auth_code",
    default=None,
    help="Authorization code from the callback page. Skips the interactive prompt.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    default=False,
    help="Print the authorize URL without opening a browser.",
)
def login(auth_code: str | None, no_browser: bool) -> None:
    """Run OAuth login with Adobe IMS. Saves tokens to ~/.frameio-mcp/tokens.json."""
    try:
        config = Config.from_env()
    except OSError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not auth_code:
        state = generate_state()
        auth_url = build_authorize_url(config, state)

        if not no_browser:
            click.echo("Opening browser to Adobe login…")
            # Headless environments have no browser; the URL is printed either way.
            with contextlib.suppress(Exception):
                webbrowser.open(auth_url)

        click.echo(f"\nIf the browser doesn't open, paste this URL manually:\n\n{auth_url}\n")
        click.echo("Sign in with your Adobe ID, then copy the code from the callback page.")
        click.echo("If pasting into this prompt misbehaves, cancel and run:")
        click.echo("  frameio-mcp login --code <paste-the-code-here>\n")

        # Echo is deliberately on. The code is single-use, already visible in the
        # browser and its URL bar, and hiding it breaks paste in several terminals.
        auth_code = click.prompt("Authorization code", type=str, default="", show_default=False)

    auth_code = auth_code.strip()
    if not auth_code:
        click.echo("No code entered. Aborting.", err=True)
        sys.exit(1)

    code = auth_code

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
    tokens_path = resolve_tokens_path()

    if clear_tokens(tokens_path):
        click.echo(f"✓ Removed tokens from {tokens_path}")
    else:
        click.echo(f"No tokens file at {tokens_path} — already logged out")


@cli.command()
def status() -> None:
    """Show current authentication status."""
    tokens_path = resolve_tokens_path()

    try:
        tokens = load_tokens(tokens_path)
    except AuthError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    if tokens is None:
        click.echo("Not authenticated. Run `frameio-mcp login`.")
        return

    if tokens.is_expired:
        click.echo(
            "Access token expired — will refresh automatically on next API call."
        )
    else:
        remaining = int(tokens.expires_at - time.time())
        click.echo(
            f"✓ Authenticated. Access token valid for another {remaining}s "
            f"(~{remaining // 60}min)."
        )

    if tokens.account_id:
        click.echo(f"  Default account_id: {tokens.account_id}")
    click.echo(f"  Tokens path: {tokens_path}")


@cli.command()
@click.option(
    "--url",
    "frameio_url",
    default=None,
    help="A Frame.io file URL to run per-file read checks against.",
)
@click.option(
    "--write",
    is_flag=True,
    default=False,
    help="Also post a real comment. Requires --url. This is the definitive v4 check.",
)
def verify(frameio_url: str | None, write: bool) -> None:
    """Check whether this token is actually authorized for the Frame.io v4 API.

    Frame.io v4 returns a bare 401 for three unrelated causes (missing scope, unlinked
    Adobe ID, missing product profile). This runs them in order and names the one that
    is failing.
    """
    from .diagnostics import run_entitlement_checks
    from .tools.get_asset_from_url import parse_frameio_url

    if write and not frameio_url:
        click.echo("Error: --write requires --url.", err=True)
        sys.exit(1)

    try:
        config = Config.from_env()
        tokens = load_tokens(config.tokens_path)
    except (OSError, AuthError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if tokens is None:
        click.echo("Not authenticated. Run `frameio-mcp login` first.", err=True)
        sys.exit(1)

    file_id = parse_frameio_url(frameio_url).get("file_id") if frameio_url else None
    if frameio_url and not file_id:
        click.echo(f"Error: could not extract a file_id from {frameio_url}", err=True)
        sys.exit(1)

    probe_text = (
        "frameio-mcp entitlement check. Safe to delete." if write else None
    )

    click.echo(f"Checking Frame.io v4 access (scopes requested: {config.scopes})\n")
    results = asyncio.run(
        run_entitlement_checks(
            tokens, file_id=file_id, write_probe_text=probe_text
        )
    )

    for result in results:
        marker = "✓" if result.passed else "✗"
        click.echo(f"{marker} {result.name}")
        click.echo(f"    {result.detail}")
        if result.remedy:
            click.echo(f"\n    How to fix:\n    {result.remedy}\n")

    failed = [r for r in results if not r.passed]
    if failed:
        click.echo(f"\nFAILED at: {failed[0].name}", err=True)
        sys.exit(1)

    if not file_id:
        click.echo("\nAccount-level access confirmed. Re-run with --url for file checks.")
    elif not write:
        click.echo("\nRead access confirmed. Re-run with --write to prove writes.")
    else:
        click.echo("\nAll checks passed. Frame.io v4 read and write both work.")


@cli.command()
def serve() -> None:
    """Run the MCP server over stdio (for use with Claude Desktop, Code, Cowork)."""
    from .server import mcp
    mcp.run()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
