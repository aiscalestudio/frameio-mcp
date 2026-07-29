# Connect Frame.io to Claude Cowork

This lets Claude read your Frame.io videos and post timestamped review comments for you.

Setup takes about two minutes. You do not need any keys, credentials, or developer
accounts. You sign in with the same Adobe ID you already use for Frame.io, and Claude
acts as you: comments appear under your name, and you only ever see the projects you
already have access to.

---

## Before you start

You need a Frame.io account that you can already sign in to at
[next.frame.io](https://next.frame.io) with an Adobe ID.

That is the only requirement. If you can open your videos in Frame.io in a browser,
you are ready.

---

## Step 1: Sign in to claude.ai in your browser first

Do this before anything else. It is easy to miss and it is the most common reason setup
fails.

When you connect, Claude opens your **web browser** to complete the sign-in. If that
browser is not already signed in to the same Claude account as the app, you land on the
Claude login page and nothing else happens.

So: open [claude.ai](https://claude.ai) in the browser you normally use, and check you are
signed in.

## Step 2: Open the connector settings

1. Open Claude
2. Click your name or the settings icon at the bottom left
3. Go to **Settings**
4. In the left sidebar, click **Connectors**

## Step 3: Add the connector

1. Click **Add** in the top right, then **Add custom connector**
2. Fill in exactly these two fields:

   | Field | Value |
   |---|---|
   | **Name** | `Frame.io` |
   | **Remote MCP server URL** | `https://frameio-mcp-jade.vercel.app/mcp` |

3. **Leave everything under "Advanced settings" empty.** There are fields for an OAuth
   Client ID and Secret. You do not need them, and filling them in will break the
   connection.
4. Click **Add**

## Step 4: Sign in with Adobe

1. Frame.io now appears in your connector list. Click **Connect**
2. A page appears titled **"Application Access Request"**. This is the Frame.io
   connector asking your permission. Click the button to approve.
3. Adobe's sign-in page opens. Sign in with the Adobe ID you use for Frame.io.
4. Adobe asks you to allow access. Approve it.
5. You are returned to Claude and the connector shows as connected.

That's it. You're done.

---

## Step 5: Check that it works

Start a new conversation in Cowork and paste something like this, using a real URL from
your own Frame.io account:

> Look at this Frame.io video and tell me the file name and how long it is:
> https://next.frame.io/project/YOUR_PROJECT_ID/view/YOUR_FILE_ID

To get that URL, open any video in Frame.io and copy the address from your browser's
address bar. Do not create a share link; the normal URL is what you want.

If Claude comes back with the correct file name, everything is working.

---

## What you can ask for

| What you want | Example |
|---|---|
| Review a video | "Pull the transcript for this video and post comments on the three weakest moments" |
| Read existing feedback | "Summarise all the comments on this video" |
| Review your own notes | "List the comments I posted on this video" |
| Attach a reference | "Post a comment at 1:32 and attach this image: [URL]" |

### Say times the normal way

"Post a comment at 2:30" or "at 90 seconds" both work. Claude reads the video's frame rate
and works out the exact frame itself, so you never need to convert anything.

If you ask for a time past the end of the video, it will tell you the real length rather
than guessing.

### One thing to know about transcripts

Frame.io does not yet let other apps read transcripts directly. So for anything involving
a transcript, the video's SRT file has to be sitting in the same Frame.io folder as the
video.

To put it there, once per video:

1. Open the video in Frame.io
2. Click the three-dot menu → **Export Transcript** → choose **SRT** → download
3. Upload that SRT file back into Frame.io, into the **same folder** as the video

Claude will find it automatically after that. If it can't, it will tell you exactly this.

---

## If something goes wrong

### "Not authorized" or a 401 error from Frame.io

Your Adobe sign-in worked but that Adobe ID is not authorised for the Frame.io API.
Almost always one of these:

- **You signed in with the wrong Adobe ID.** Use the same one that opens your Frame.io
  account. If your organisation has several, this is the usual cause.
- **Your Frame.io access is managed by an administrator** and your account has not been
  given API access. Ask whoever administers your Frame.io account to check that you are
  assigned to the Frame.io product profile in the Adobe Admin Console.

Disconnecting and reconnecting will not fix this. It needs the right account or the right
permission.

### The connector says it needs reconnecting, or a tool says your token expired

Click **Disconnect**, then **Connect**, and sign in again.

Disconnecting first matters. Simply reconnecting can reuse the old sign-in, and if that is
the problem you will see the same error again.

### "No transcript file found"

The SRT hasn't been uploaded next to the video yet. See the transcript note above.

### Claude can see the video but cannot comment

You have view access to that project but not comment access. Ask the project owner to
give you commenting permission in Frame.io.

### Something worked before and now behaves oddly

Start a new conversation. If Claude has already seen an error in a chat, it sometimes keeps
reasoning from it even after the underlying problem is gone.

### Attaching a file fails

Attachments are the least tested part of this connector. If it fails, report it rather
than retrying: the other features are unaffected.

---

## Questions worth answering up front

**Can other people see my Frame.io projects through this?**
No. Each person signs in with their own Adobe ID and sees only what they already have
access to in Frame.io. The connector holds no shared account.

**Will comments be attributed to me?**
Yes. They appear under your name, exactly as if you had typed them.

**Can Claude delete things?**
No. It can read files and comments, post comments, and attach files to comments. It
cannot delete or modify anything.

**Do I need to install anything?**
No. Nothing runs on your computer.
