# Fetching source video

## The problem, stated honestly

```
ERROR: [youtube] xxx: Sign in to confirm you're not a bot.
```

This is **not** a bug in yt-dlp, the code, or the video. YouTube is refusing the
IP address. Datacenter ranges — Railway, Render, Fly, AWS, GCP — are pre-flagged
as bot traffic; a home connection usually is not. That is why link downloads work
on your laptop and fail on the server running the same code.

Nothing in the request can fix this, because the thing being judged is the
network the packets came from. There is no header, user-agent, client or
retry that changes that. **Anyone claiming a code-only fix for this is wrong.**

So "reduce failures to near zero" means buying a better IP or sidestepping the
download. Ranked by how well they actually hold up:

| | Reliability | Cost | Notes |
|---|---|---|---|
| Residential proxy | High | ~$1–5/GB | What commercial clippers actually use |
| Fresh cookies | Medium | Free | Decays; see below |
| Client fallback | Low | Free | Already automatic here. Buys weeks |
| User uploads a file | Total | Free | No YouTube involved at all |

## What this codebase does automatically

`app/pipeline/downloader.py` tries each player client in turn — `default`,
`android`, `ios`, `tv` — because the bot challenge is applied per client and the
one that fails is often not the one that would have worked. It stops early on
errors that are not IP blocks (private, deleted, unsupported), since retrying
those is pointless.

Treat it as buying time. Which clients work changes every few weeks.

## Configuration

```
CLIPPER_PROXY=http://user:pass@residential-proxy.example.com:8000
CLIPPER_COOKIES_FILE=cookies.txt
```

`CLIPPER_PROXY` is the one that actually solves it. Any residential provider
speaking HTTP/SOCKS works — this is a standard yt-dlp `--proxy`.

### On cookies

Cookies help, but understand the trade before relying on them:

- YouTube invalidates them **faster when it sees them used from a datacenter**,
  which is exactly where you need them. Expect to refresh them.
- They authenticate a real account. Heavy automated use can get that account
  restricted, so never use your main one.
- `CLIPPER_COOKIES_BROWSER` cannot work on a server — it reads a local browser
  profile and prompts for a keychain password. Dev machines only.

## Failure economics

The pipeline charges credits *after* choosing clips but *before* rendering, and
the full source download happens after that. A download refused at that point
meant the user had paid for a transcript, an analysis, and clips they never got.

`run_pipeline` now refunds on any failure after the charge, and the ledger keeps
both rows so history stays honest rather than being rewritten.

The audio download happens *before* transcription, so a blocked IP normally
fails there — cheaply, before Deepgram is called. The expensive case is a link
that passes the audio fetch and is then refused for the video, which is why the
refund exists.

## Metadata is not bytes

These are two problems, and treating them as one is what makes a blocked IP
look worse than it is:

| | Blocked by IP? | Cost | How |
|---|---|---|---|
| Metadata (title, length, thumbnail) | **No** | Free to 10k/day | YouTube Data API |
| Media bytes | **Yes** | Proxy, ~$1–5/GB | yt-dlp |

Set `YOUTUBE_API_KEY` and the preview stops going through yt-dlp entirely. It
answers in ~200ms instead of ~8s, and it answers *from a server whose downloads
are refused* — because the Data API authenticates by key and never looks at the
caller's address.

That is worth doing on its own merits. Pasting a link and seeing the video
appear instantly is the reason links beat uploads; routing that through a
scraper that a datacenter IP gets throttled on gave the slowest, least reliable
version of the product's best moment. Get a key from the Google Cloud console
(enable "YouTube Data API v3"). Without one, nothing changes — yt-dlp handles
metadata as before.

It does **not** fix downloads. Only the IP does that.

## Recommendation

Links are the product — paste-and-go is the pitch, and a two-hour podcast is a
multi-GB upload nobody will sit through. So:

1. **Set `YOUTUBE_API_KEY`.** Free, and it makes the preview instant and
   IP-proof. Do this first.
2. **Budget for a residential proxy** if link downloads matter, which they do.
   This is the actual fix for the actual problem, and it costs money. Every
   commercial clipper is paying it.
3. **Keep upload** as the escape hatch for when a link is refused anyway —
   which will still happen, to everyone, including the competition.
