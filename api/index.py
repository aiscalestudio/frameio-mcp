"""Vercel entrypoint. Vercel discovers the ASGI app exported as `app`.

Construction happens at import time on purpose. Every setting in ServerConfig is
required, so an incomplete environment fails the cold start with a message naming the
missing variables, rather than serving traffic that 500s on the first tool call.
"""

from frameio_mcp.app import create_app

app = create_app()
