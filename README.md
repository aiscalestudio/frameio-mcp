# Frame.io MCP

Connect Claude to Frame.io. Pull transcripts, post frame-accurate comments, walk your own comment list, upload attachments — all from inside Claude Desktop, Claude Code, Cowork, or any MCP-compatible client.

Built by [AI Scale Studio](https://github.com/aiscalestudio) as the companion tool for the [AI Founders HQ](https://youtube.com/@aifoundershq) Higgsfield workflow.

---

## What it does

Five tools, all mapped to the Frame.io v4 REST API:

| Tool | What it does |
|---|---|
| `frameio_get_asset_from_url` | Resolve a Frame.io project or file URL to an `account_id`, `project_id`, and `file_id`. |
| `frameio_get_transcript_from_sibling` | Find an SRT/VTT transcript file next to a video in the same Frame.io folder, download and parse it into timestamped lines. |
| `frameio_post_comment` | Post a frame-accurate SMPTE-timestamped comment on a file. |
| `frameio_list_comments` | List comments on a file, with a filter for "only mine." Fable 5 uses this to walk its own comment list back. |
| `frameio_upload_attachment` | Attach a file (MP4, PNG, PDF, ...) to a specific comment. |

**Why "sibling" transcript?** Frame.io hasn't shipped a public transcript API yet (on their roadmap, no ETA). The workaround: export the transcript from Frame.io's UI (one click → SRT), drop it in the same folder as the video, and this MCP grabs it. When Frame.io ships the native transcript API, the `frameio_get_transcript_from_sibling` tool swaps to it internally and the editor's export step disappears.

---

## Quickstart

### Requirements

- Python 3.11+
- A Frame.io v4 account
- An Adobe Developer Console project with the Frame.io API added and OAuth Web App credentials generated ([setup guide](#adobe-developer-console-setup) below)

### Install

```bash
pipx install frameio-mcp
```

Or, if you're grabbing the wheel directly:

```bash
pipx install /path/to/frameio_mcp-*.whl
```

### Configure

Create a `.env` file in your home directory or wherever you'll run the MCP from:

```
FRAMEIO_CLIENT_ID=your_client_id_here
FRAMEIO_CLIENT_SECRET=your_client_secret_here
FRAMEIO_OAUTH_RELAY_URL=https://aiscalestudio.github.io/frameio-mcp/callback.html
```

Alternatively, set these as environment variables.

### Authenticate

```bash
frameio-mcp login
```

This opens your browser to Adobe's OAuth flow. After you sign in with your Adobe ID, you'll be redirected to a callback page that displays an authorization code. Copy the code, paste it back into your terminal, and `frameio-mcp` will exchange it for a set of tokens and save them to `~/.frameio-mcp/tokens.json` with `0600` permissions.

Tokens auto-refresh in the background. If your refresh token expires (typically after 90+ days of no use), just run `frameio-mcp login` again.

### Wire into your MCP client

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "frameio": {
      "command": "frameio-mcp",
      "args": ["serve"]
    }
  }
}
```

Restart Claude Desktop. The tools appear under the plug icon.

**Claude Code / Cowork** — add the same entry to the local MCP config.

### Verify

Ask Claude something like:

> Given this Frame.io URL, pull the transcript and tell me the top three moments where the visual should carry emotion:
> https://next.frame.io/project/PROJECT_ID/view/FILE_ID

Claude will use `frameio_get_asset_from_url` → `frameio_get_transcript_from_sibling` → return a summary of what it found.

---

## Adobe Developer Console setup

If you haven't set up the Adobe side yet:

1. Go to [developer.adobe.com/console](https://developer.adobe.com/console).
2. Sign in with the Adobe ID linked to your Frame.io account.
3. Click **Create new project**.
4. Inside the project → **+ Add API** → select **Frame.io API**.
5. Choose **User Authentication** (Server-to-Server is Enterprise-only).
6. On the OAuth Web App configuration screen:
   - **Default Redirect URI**: `https://aiscalestudio.github.io/frameio-mcp/callback.html`
   - **Redirect URI pattern**: `https://aiscalestudio\.github\.io/frameio-mcp/callback\.html`
7. Confirm scopes include: `openid`, `AdobeID`, `offline_access`, and any Frame.io-specific scopes offered. `offline_access` is critical — it's what gets you a refresh token so you don't re-authenticate every day.
8. Save. Adobe generates a **Client ID** and **Client Secret** — paste them into your local `.env` file (see above).

---

## Editor workflow

For the MCP to read transcripts today, editors need to export the SRT from Frame.io once per video and drop it in the same folder as the video:

1. Upload the video to Frame.io as usual. Wait for auto-transcription to finish (~1 min per 10 min of video).
2. Open the video → three-dot menu → **Export Transcript** → choose **SRT** → download.
3. Upload the downloaded SRT file back to Frame.io, in the **same folder** as the video.
4. Done. The MCP will find it via the video's `parent_id` when Claude asks.

Once Frame.io ships their public transcript API, this manual step disappears.

---

## Tool reference

### `frameio_get_asset_from_url`

Resolve a Frame.io URL to identifiers.

- **Input**: `url` (str) — any Frame.io project or file URL
- **Output**: `{ account_id, workspace_id, project_id, file_id, file_name, media_type, duration_seconds }`

### `frameio_get_transcript_from_sibling`

Find the SRT/VTT next to a video and parse it.

- **Input**: `account_id`, `file_id`
- **Output**: `{ transcript: [{ start_seconds, end_seconds, text, speaker }], source_file_name, source_file_id }`

### `frameio_post_comment`

Post a frame-accurate comment.

- **Input**: `account_id`, `file_id`, `text`, `timestamp_seconds`, optional `duration_seconds`
- **Output**: `{ comment_id, text, timestamp_microseconds, created_at, url }`

### `frameio_list_comments`

List comments on a file, with pagination and an optional "mine only" filter.

- **Input**: `account_id`, `file_id`, optional `page_size` (default 50), optional `after` (cursor), optional `only_mine` (default false)
- **Output**: `{ comments: [{ comment_id, text, timestamp_seconds, duration_seconds, creator_id, created_at, attachments }], next_cursor, total_count }`

### `frameio_upload_attachment`

Attach a local file to a comment.

- **Input**: `account_id`, `comment_id`, `file_path` (absolute local path)
- **Output**: `{ attachment_id, comment_id, file_name, media_type, file_size_bytes, url }`

---

## Troubleshooting

**"Adobe redirects me to a page that shows the code — what now?"**
Copy the code, paste it in the terminal where `frameio-mcp login` is running. The terminal is waiting on stdin.

**"401 Invalid or missing authorization token"**
Your access token expired and refresh failed. Run `frameio-mcp login` again.

**"No transcript file found in folder"**
The editor hasn't uploaded the SRT yet, or it's in a different folder than the video. Frame.io UI → open the video → three-dot menu → Export Transcript → SRT → upload the file to the same folder.

**"License required" when adding Frame.io API in Adobe Console**
Your Frame.io account isn't provisioned for V4 API access yet. Email support@frame.io asking them to enable V4 API entitlement on your account.

**Windows / WSL support**
Untested. Should work in principle (pure Python + httpx), but the pipx path assumes Unix-style `~`. Report issues.

---

## Roadmap

- **v0.2**: multi-account support via `frameio-mcp login --profile <name>`, tokens saved per-profile
- **v0.3**: native transcript API when Frame.io ships it (drop the sibling workaround)
- **v0.4**: webhook helper for auto-processing on upload
- **v0.5**: batch operations (bulk comment creation from a list)

---

## License

MIT. See [LICENSE](./LICENSE).

## Credits

Built by (https://github.com/aiscalestudio) at AI Scale Studio, for the [AI Founders HQ](https://youtube.com/@aifoundershq) YouTube channel and the AI Business Trailblazers Hive community.

Frame.io is a trademark of Adobe. This project is not affiliated with or endorsed by Adobe.
