# Phase 0 Runbook: prove Frame.io v4 access

This is the gate before any hosting work. It answers one question: **does an Adobe IMS
token actually authorize this app for the Frame.io v4 API?**

It matters because v4 fails in a misleading way. Adobe issues a perfectly valid token,
v2 endpoints keep working, and every v4 endpoint returns a bare `401` with no indication
that anything is misconfigured. Three unrelated causes produce that identical 401:

1. the token was never granted the `additional_info.roles` scope
2. the Adobe ID is not linked to a Frame.io account
3. the user is not assigned a Frame.io v4 product profile

`frameio-mcp verify` runs the checks in dependency order and names which one is failing.

---

## Step 0: local setup (do this first)

The CLI has never been run successfully on this machine. Two prerequisites are missing.

### 0a. Create the `.env` file

`login` cannot work without Adobe credentials: it needs `client_id` to build the
authorize URL and `client_secret` to exchange the returned code. `logout` and `status`
no longer require them.

Get both values from Adobe Developer Console → your project → **OAuth Web App**
credential, then:

```bash
cd /Users/danlacurezeanu/Documents/aidevelopment/frameio-mcp
cp .env.example .env
```

Edit `.env` and fill in:

```
FRAMEIO_CLIENT_ID=<from Adobe Developer Console>
FRAMEIO_CLIENT_SECRET=<from Adobe Developer Console>
FRAMEIO_OAUTH_RELAY_URL=https://aiscalestudio.github.io/frameio-mcp/callback.html
```

`.env` is gitignored. Never commit it.

### 0b. Publish the OAuth callback page

`docs/callback.html` is committed, but GitHub Pages is not enabled on the repository, so
`https://aiscalestudio.github.io/frameio-mcp/callback.html` currently returns **404**.
Without it, Adobe redirects to a dead page after sign-in and there is no authorization
code to paste back.

Enable Pages to serve `/docs` from `main`:

```bash
gh api -X POST repos/aiscalestudio/frameio-mcp/pages \
  -f 'source[branch]=main' -f 'source[path]=/docs'
```

Wait a minute, then confirm it returns 200:

```bash
curl -o /dev/null -w "%{http_code}\n" \
  https://aiscalestudio.github.io/frameio-mcp/callback.html
```

### 0c. Register the redirect URI in Adobe

In the OAuth Web App credential, the redirect URI must match the relay URL exactly:

- **Default Redirect URI:** `https://aiscalestudio.github.io/frameio-mcp/callback.html`
- **Redirect URI pattern:** `https://aiscalestudio\.github\.io/frameio-mcp/callback\.html`

A mismatch here produces an Adobe error page instead of a code.

> This whole relay mechanism disappears in Phase 2. The hosted server handles its own
> callback at `{BASE_URL}/auth/callback`, and this GitHub Pages relay gets retired.

---

## Step 1: fix the OAuth scopes in Adobe Developer Console

The repo previously requested `openid,AdobeID,offline_access`. That set is missing
`additional_info.roles`, `email`, and `profile`, which v4 authorization depends on. The
code now requests the full set, but **Adobe will only grant what the credential is
configured to allow**, so the console has to be updated to match.

1. Go to [developer.adobe.com/console](https://developer.adobe.com/console)
2. Sign in with the Adobe ID linked to your Frame.io account
3. Open the project that has the **Frame.io API** added
4. Open the **OAuth Web App** credential
5. Confirm the scope list includes all five:

   ```
   openid
   email
   profile
   offline_access
   additional_info.roles
   ```

6. Save

If `additional_info.roles` is not offered as a selectable scope, that usually means the
Frame.io API has not been added to the project. Add it via **+ Add API** first.

## Step 2: confirm the Frame.io side

Two things that live outside the Developer Console and are the most commonly missed:

- **Adobe ID linked to Frame.io.** Sign in to Frame.io with the same Adobe ID and confirm
  it opens your account.
- **v4 product profile assigned.** In the Adobe Admin Console, confirm your user is
  assigned to a Frame.io product profile. This is admin-only and is the single most common
  cause of a 401 that survives a correct scope list.

## Step 3: re-authenticate

An existing token keeps whatever scopes it was issued with. Widening the credential does
nothing until you get a new token.

```bash
cd /Users/danlacurezeanu/Documents/aidevelopment/frameio-mcp
.venv/bin/python -m frameio_mcp logout
.venv/bin/python -m frameio_mcp login
```

The login flow opens Adobe in your browser, then asks you to paste back the authorization
code shown on the callback page.

## Step 4: run the checks

Account level first. This proves scopes, identity, and entitlement:

```bash
.venv/bin/python -m frameio_mcp verify
```

Then per-file reads. Use any Frame.io video URL you can open in the UI:

```bash
.venv/bin/python -m frameio_mcp verify --url "https://next.frame.io/project/PROJECT_ID/view/FILE_ID"
```

Then the definitive check, a real write. This posts a comment at 00:00 titled
"frameio-mcp entitlement check. Safe to delete." Delete it in the Frame.io UI afterwards:

```bash
.venv/bin/python -m frameio_mcp verify --url "https://next.frame.io/project/PROJECT_ID/view/FILE_ID" --write
```

## Reading the output

Each stage prints `✓` or `✗`. On failure it stops and prints a specific remedy, because
every later check would fail for the same reason and only add noise.

```
✓ Granted scopes
    All 5 required scopes granted
✓ v4 identity (GET /me)
    Authenticated as dan@example.com
✓ v4 accounts
    1 account(s): AI Scale Studio
✓ v4 read (list comments)
    Read 3 comment(s) from the file
✓ v4 write (post comment)
    Created comment abc-123. Delete it in the Frame.io UI when done.

All checks passed. Frame.io v4 read and write both work.
```

### If "Granted scopes" fails

Adobe issued a narrower token than requested. Step 1 was not saved, or Step 3 was skipped.

### If "v4 identity" fails

The token is not authorized for v4 at all. Work through Step 2. In practice this is the
product profile assignment nine times out of ten.

### If "v4 read" fails but identity passed

Authentication is fine; that specific file is not readable by this user. Confirm the URL
points at something you can open in the Frame.io UI.

### If "v4 write" fails but read passed

Not an OAuth problem. The Frame.io role on that project grants view but not comment.

---

## What happens next

- **All checks pass:** Phase 0 Step 1 is cleared. Report back and Step 2 (the serverless
  Streamable HTTP spike) starts.
- **Any check fails:** stop. Do not start the hosting work. The failure is an Adobe
  entitlement problem, and the hosted architecture cannot route around it, because the
  hosted server will request exactly the same scopes against exactly the same API.

---

## Notes

- The diagnostics deliberately disable the client's automatic token refresh on 401. The
  refresh path rewrites an entitlement failure into a misleading "re-authenticate" error,
  which is what makes this class of problem so hard to read.
- Scope inspection decodes the token payload without verifying its signature. That is
  intentional and safe here: it is reporting what Adobe granted, not making an
  authorization decision.
