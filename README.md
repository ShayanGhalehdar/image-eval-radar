# Image-Eval Radar

Finds newly published **methods for evaluating image models** — metrics, benchmarks,
judge models, eval protocols — and pushes a digest to Telegram every day at 8am,
grouped by evaluation dimension.

Runs as one workflow inside the existing `n8n-claude` container. Not a separate stack.

## Layout

| File | Purpose |
|---|---|
| `dimensions.md` | The taxonomy (13 seed dimensions). Injected as system context on every classifier call. |
| `prompts/classify.txt` | Classification prompt. Takes a batch of 10 records, returns a strict JSON array. |
| `build_workflow.py` | Generates `workflow.json`. Edit this, not the JSON. |
| `workflow.json` | Importable n8n workflow (23 nodes). |

Mounted read-only into the container at `/image-eval-radar`. Read-only is correct:
the taxonomy only changes when *you* approve a new dimension, which happens on the host.

## Pipeline

```
Schedule 0 8 * * *  (America/Toronto)
  ├─ arXiv API        cs.CV/cs.AI/cs.LG/cs.CL, 36h submittedDate window
  ├─ HF daily papers  today + yesterday (curation lags publication)
  └─ GitHub search    5 narrow queries, created:>36h ago
        ↓  normalise to a common record shape
        ↓  keyword prefilter          measured: 132 raw -> 9
        ↓  dedupe against Notion on unique Key
        ↓  Claude classify, batches of 10, base64-piped to `claude -p`
        ↓  keep relevant, map to dimensions, flag new dimensions
        ↓  write rows to Notion  +  Telegram digest grouped by dimension
```

## Quick start — making it run every morning

**Both workflows must be published**, because n8n 2.x refuses to publish a workflow whose
`Execute Workflow` target is unpublished — the listener cannot call an unpublished radar.

That would normally also arm the 8am cron, so the `Daily 8am` trigger node **ships disabled**.
The result: both workflows published, on-demand commands working, and no scheduled run.

| Want | Do this |
|---|---|
| On-demand commands only | Publish both. Leave `Daily 8am` disabled. (default) |
| Add the 8am digest | Right-click `Daily 8am` -> Activate, then Save |
| Stop the 8am digest | Right-click `Daily 8am` -> Deactivate, then Save |

Publishing is also what makes the listener's schedule fire *and* what makes n8n persist
workflow static data, where the consumed-update offset lives. An unpublished listener
re-reads the same Telegram updates on every manual run.

### Two things that silently stop it

The container has `restart: unless-stopped`, so it comes back on its own — but only if
Docker is running and the machine is awake.

1. **Docker Desktop does not start on login** (`AutoStart = false` as of 2026-08-18).
   After any reboot, nothing runs until you open Docker Desktop. Fix once:
   Docker Desktop → Settings → General → tick *Start Docker Desktop when you sign in*.
2. **A sleeping Mac misses the trigger.** n8n does not backfill missed schedules, so if the
   machine is asleep at 8:00 there is no digest that day.

Neither loses data permanently: the lookback window is 36h and dedupe is keyed on the
Notion `Key` property, so the next successful run picks up most of what a missed day
would have caught, without re-sending anything you already received.

### What happens at 8am

Sources are polled, ~230 raw records get cut to ~10 by the prefilter, one Claude call
classifies them, relevant items are written to Notion, and a single grouped message goes
to the channel. On a quiet day you get `No new eval methods today.` — silence always
means something is broken, never "nothing happened".

### Approving a proposed dimension

When a digest opens with `🆕 NEW DIMENSION PROPOSED`, decide whether it is genuinely new
or a synonym of something in the taxonomy. To accept it, add a line to `dimensions.md`
under the right section. No restart needed — the file is read fresh on every run.

### If no message arrives

| Check | How |
|---|---|
| Was the machine awake and Docker running? | `docker ps` should list `n8n-claude-n8n-1` |
| Did the run fail? | n8n → Image-Eval Radar → Executions tab |
| Claude auth expired? | You would get a Telegram alert saying so, with the fix command |
| Nothing to report? | You would get the explicit `No new eval methods today.` message |

An auth failure is the most likely long-run breakage, and it also takes out the
job-scoring and yellow card workflows. The digest tells you rather than going quiet.

## On-demand queries from Telegram

Post one of these **in the channel** and the bot re-runs the pipeline over that window:

```
past 24h
past 3 days
past 2 weeks
```

Case-insensitive, singular or plural, tolerant of extra spaces. Units: `h`/`hours`,
`d`/`days`, `w`/`weeks`. Anything longer than 30 days is clamped to 30. Text that is not
a command is ignored, so you can chat in the channel normally.

You get an acknowledgement immediately (the run takes a minute or two), then the digest,
headed `past 3 days` instead of a date.

### How it is wired, and why it is two workflows

n8n only fires schedule triggers on **active** workflows. Keeping the radar itself
inactive means it cannot listen for anything — so the listener is a second, always-on
workflow:

| Workflow | Active? | Role |
|---|---|---|
| `Image-Eval Radar` | your choice | The pipeline. Two entry points: the 8am schedule, and an Execute Workflow trigger for on-demand runs. |
| `Image-Eval Radar — Commands` | **must be active** | Polls Telegram every minute, parses commands, calls the radar. |

Polling `getUpdates` rather than n8n's Telegram Trigger node, because that node is
webhook-based and this n8n has no public URL. The consumed-update offset is kept in
workflow static data, which n8n only persists for active workflows — another reason the
listener must be published.

### On-demand differs from the 8am run in two deliberate ways

- **Dedupe is not applied to the digest.** You asked what is *in* that window, so
  everything in it is shown, including items already sent on a previous day.
- **Only genuinely new rows are written to Notion.** Re-querying a window never creates
  duplicate archive rows.

Candidates are capped at 40 per on-demand query to bound the number of Claude calls a
wide window can trigger.

### Extra environment variable

The listener needs the bot token to call `getUpdates`:

| Var | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token for `shayan_image_eval_radar_bot`, used only for polling |

## Source notes (learned the hard way)

- **arXiv needs `https`.** Plain `http` 301s and returns zero bytes. Date-range brackets
  must be percent-encoded as `%5B` / `%5D` or curl rejects the URL outright.
- **GitHub ANDs every search term.** One broad query returned exactly 1 result; five
  narrow queries return 9–13 each per month-window. Unauthenticated search allows
  10 req/min, so 5 queries once daily is comfortably inside it.
- **HF `daily_papers` is keyed on curation date, not publication date** — hence polling
  two days. Its ids are arXiv ids, so its records dedupe against the arXiv branch for free.
- **Artificial Analysis returns 401.** The API is real but needs a free key. It is the
  only source that covers **latency and cost**, the two dimensions academia never
  reports. Not wired up yet — see "Extending" below.

## Prefilter tuning

Measured against a real 4-day arXiv window plus one HF day (132 records):

| Filter | Kept | Notes |
|---|---|---|
| bare `image` + loose eval terms | 66/132 | robot manipulation, surgical segmentation survive |
| **current: generative term + strong eval term** | **9/132** | real hits retained |
| eval term must appear in the title | 2/132 | too aggressive |

`IMG` deliberately excludes bare `"image"` — it matches essentially every vision paper.
The classifier is still the precision stage; the prefilter just stops paying to
classify obvious noise.

## New dimensions

The classifier may *propose* a dimension outside the 13, but it is **never auto-added**.
It surfaces in a `🆕 NEW DIMENSION PROPOSED` block pinned to the top of the digest and
is written to Notion with `Status = proposed-dimension`.

To approve one: add it to `dimensions.md` under the right section, then
`docker compose restart n8n` is *not* needed — the file is read fresh on every run.

This is what keeps the taxonomy from drifting into near-duplicates like
"compositionality" vs the existing "layout/spatial control".

## Re-running by hand

Open the workflow in n8n at <http://localhost:5678> and hit **Execute Workflow**.
To test one stage, use **Execute Node** on that node alone.

To regenerate the workflow after editing `build_workflow.py`:

```sh
python3 ~/image-eval-radar/build_workflow.py
```

then re-import `workflow.json` in the n8n UI.

## Environment

Set in `~/n8n-claude/docker-compose.yml`:

| Var | Purpose |
|---|---|
| `NOTION_EVAL_DB_ID` | Target Notion database id |
| `TELEGRAM_EVAL_CHAT_ID` | Channel chat id (`-100…`) |
| `AA_API_KEY` | Optional, Artificial Analysis |

Never hardcode these into node parameters — that is exactly what made the other
workflows need scrubbing before they could be published.

## Extending

- **Artificial Analysis / latency + cost.** Add a fourth source branch once you have a
  key. Its records slot into the same normalised shape.
- **LMArena ELO.** Skipped for now: it is a JS app behind a redirect, so scraping is
  brittle. Revisit via their HF dataset.
- **Backfill.** The system starts from the day it is switched on. Seeding the archive
  is a separate one-off run with a wider date window.

## Do not export raw

Same failure mode as the other workflows: an export will contain the Telegram chat id
and any key typed into an HTTP node as plain node parameters. Scrub before publishing.
