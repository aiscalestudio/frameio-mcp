# Phase 0 Step 2 spike findings

Date: 2026-07-29
FastMCP 3.4.5, mcp 1.29.0, py-key-value-aio 0.4.5, Python 3.12.13

Ran locally against `spike/server.py` using the real Adobe IMS discovery document and
the real Adobe client credentials. No Adobe login was needed, because everything below
is testable before the user-consent step.

---

## Q1. Does OIDCProxy wire up against Adobe IMS? YES

`OIDCProxy(config_url="https://ims-na1.adobelogin.com/.well-known/openid-configuration", ...)`
constructs and serves without custom endpoint configuration. Adobe's discovery document
supplies the authorize, token, and JWKS endpoints, so the hand-rolled constants in
`config.py` are not needed by the hosted server.

## Q2. Does it publish what Claude needs to self-register? YES

All four checks passed:

```
PASS  protected resource metadata
      resource: http://localhost:8000/mcp
      auth servers: ['http://localhost:8000/']
PASS  authorization server metadata
      authorize:    http://localhost:8000/authorize
      token:        http://localhost:8000/token
      registration: http://localhost:8000/register
PASS  unauthenticated /mcp rejected
      HTTP 401
      WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource/mcp"
PASS  dynamic client registration
      client_id: 3f3e997e-0e30-46cc-8a69-ec8eb8235a3d
```

A `registration_endpoint` is advertised and Dynamic Client Registration works, which is
what lets a Cowork user paste only a URL and leave both Advanced settings fields blank.

Unauthenticated calls to `/mcp` are rejected with a correct `WWW-Authenticate` challenge,
so the endpoint is not publicly callable.

### Route note

The protected-resource metadata is served at
`/.well-known/oauth-protected-resource/mcp`, scoped to the resource path, **not** at
`/.well-known/oauth-protected-resource`. Worth knowing when debugging.

## Q3. Does OAuth state survive a process restart? ONLY WITH PERSISTENT STORAGE

This is the finding that matters for the hosting decision. Both arms were run.

| Storage | After restart | Result |
|---|---|---|
| `MemoryStore` | `HTTP 401 {"error":"invalid_client","error_description":"Invalid client_id"}` | **state LOST** |
| `DiskStore` | `HTTP 400 invalid_request: code_verifier required` | **state SURVIVED** |

The disk arm returning `invalid_request` rather than `invalid_client` is the signal: the
server still recognised the client and rejected only the deliberately fake authorization
code.

The memory arm is the negative control. It proves the probe can actually detect failure,
so the disk PASS is meaningful rather than vacuous.

### Consequence for Vercel

RED risk 2 in the plan is **confirmed real**. Vercel Functions have an ephemeral
filesystem and run multiple instances, so both `MemoryStore` and `DiskStore` behave like
the failing arm in production. Users would be silently disconnected on every cold start
or scale-out, presenting as "it worked yesterday" rather than as an error.

Redis-backed `client_storage` is **mandatory**, not optional, for the Vercel target.
`jwt_signing_key` must likewise be a fixed environment variable, or a token minted by one
instance is rejected by the next.

Redis itself was not exercised (no local Redis available). The persistence *mechanism* is
proven and the storage backends share one interface, so Redis verification moves to the
first deployment.

---

## Bonus finding: how tools get the Frame.io token

Traced through `OAuthProxy.load_access_token` in the installed package:

1. The FastMCP JWT presented by Claude is a reference token
2. Its `jti` maps to a stored `UpstreamTokenSet`
3. `_get_verification_token()` returns `upstream_token_set.access_token`, the **Adobe**
   access token
4. `JWTVerifier.verify_token()` returns `AccessToken(token=token, ...)` where `token` is
   that same Adobe access token
5. Upstream refresh is handled automatically, under an advisory lock that prevents
   concurrent requests racing to refresh the same token

**So in Phase 2, a tool calls `get_access_token().token` and receives the Adobe access
token directly, usable as `Authorization: Bearer <token>` against Frame.io v4.** No manual
exchange, no token storage in our code, and refresh is handled for us.

This makes the `FrameIOClient` refactor simpler than planned: it takes a token string per
request, and `auth.py` can be deleted rather than partially retained.

---

## API changes vs the plan

`FastMCP(..., stateless_http=True)` is rejected in 3.x:

```
TypeError: FastMCP() no longer accepts `stateless_http`. Pass `stateless_http` to
`run_http_async()` or `http_app()`, or set FASTMCP_STATELESS_HTTP.
```

Section 7.4 of the plan needs updating to pass it at `http_app()` time.

---

## Still unproven

1. **Redis specifically.** Mechanism proven, backend not exercised.
2. **A full end-to-end OAuth consent flow.** Requires a redirect URI registered in Adobe
   for whatever host the server runs on. Deferred to the first deployment rather than
   registering a throwaway `localhost` URI.
3. **Vercel's serverless request lifecycle with Streamable HTTP.** The local run uses a
   long-lived uvicorn process. Cold starts and per-invocation isolation are only
   observable on Vercel itself.

Item 3 is the remaining reason Phase 3 keeps a stateful-host fallback (Fly.io or Render).

## Reproducing

```bash
set -a && . ./.env && set +a
export JWT_SIGNING_KEY="anything-fixed" BASE_URL="http://localhost:8000"

STORAGE=disk .venv/bin/python spike/server.py &
.venv/bin/python spike/probe.py
# restart the server, then:
.venv/bin/python spike/probe.py --client-id <printed-id>
```

Swap `STORAGE=memory` to see the failing arm.
