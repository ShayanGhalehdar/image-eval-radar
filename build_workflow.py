#!/usr/bin/env python3
"""Generate workflow.json for the Image-Eval Radar n8n workflow.

Run:  python3 ~/image-eval-radar/build_workflow.py
Then: import ~/image-eval-radar/workflow.json in the n8n UI (Workflows -> Import from File).

Kept as a generator rather than hand-edited JSON so the JS code nodes stay readable
and the whole thing is reproducible after an n8n export mangles the formatting.
"""
import json, os

OUT = os.path.expanduser("~/image-eval-radar/workflow.json")

# Existing n8n credentials, bound by id so a re-import stays wired up.
NOTION_CRED   = {"httpHeaderAuth": {"id": "REPLACE_WITH_NOTION_CREDENTIAL_ID", "name": "Notion API"}}
TELEGRAM_CRED = {"telegramApi":    {"id": "REPLACE_WITH_TELEGRAM_CREDENTIAL_ID", "name": "Telegram \u2014 Image Eval Radar"}}

# ---------------------------------------------------------------- code nodes

WINDOW = r"""
// Lookback comes from the trigger path: 36h for the daily run (generous on purpose —
// dedupe makes overlap harmless, a gap is not), or whatever an on-demand command asked for.
const cfg = $input.first().json || {};
const hours = Math.min(Math.max(Number(cfg.lookbackHours) || 36, 1), 24 * 30);
const now = new Date();
const from = new Date(now.getTime() - hours * 3600 * 1000);
const pad = n => String(n).padStart(2, '0');
const stamp = d => `${d.getUTCFullYear()}${pad(d.getUTCMonth()+1)}${pad(d.getUTCDate())}${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}`;
const ymd = d => `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}`;
return [{ json: {
  arxivFrom: stamp(from),
  arxivTo: stamp(now),
  ghSince: ymd(from),
  today: ymd(now),
  yesterday: ymd(new Date(now.getTime() - 86400000)),
  runDate: ymd(now),
  lookbackHours: hours,
  mode: cfg.mode === 'ondemand' ? 'ondemand' : 'scheduled',
  commandText: cfg.commandText || '',
}}];
"""

ARXIV_Q = r"""
const w = $input.first().json;
const cats = ['cs.CV', 'cs.AI', 'cs.LG', 'cs.CL'];
const terms = '(abs:%22benchmark%22+OR+abs:%22evaluation%22+OR+abs:%22metric%22+OR+abs:%22judge%22+OR+abs:%22human+preference%22)';

// arXiv caps each response at max_results and sorts newest-first, so a single
// 100-result page only reaches back a day or two. Measured: a 10-day window in
// cs.CV alone matches 576 papers, of which one page covered just 2 days — so
// "past 10 days" silently behaved like "past 2 days". Page count scales with
// the requested window.
// NOTE: https is required (plain http 301s and returns zero bytes) and the
// date-range brackets must be percent-encoded as %5B / %5D.
const PAGE  = 200;
const pages = Math.min(Math.max(Math.ceil((w.lookbackHours || 36) / 48), 1), 5);

const out = [];
for (const c of cats) {
  for (let p = 0; p < pages; p++) {
    out.push({ json: {
      mode: w.mode, lookbackHours: w.lookbackHours, commandText: w.commandText,
      category: c, page: p,
      url: `https://export.arxiv.org/api/query?search_query=cat:${c}+AND+submittedDate:%5B${w.arxivFrom}+TO+${w.arxivTo}%5D+AND+${terms}`
         + `&sortBy=submittedDate&sortOrder=descending&start=${p * PAGE}&max_results=${PAGE}`,
    }});
  }
}
return out;
"""

ARXIV_PARSE = r"""
const out = [];
for (const item of $input.all()) {
  const xml = typeof item.json.data === 'string' ? item.json.data : (item.json.body || '');
  for (const e of xml.split('<entry>').slice(1)) {
    const g = re => { const m = e.match(re); return m ? m[1].trim().replace(/\s+/g, ' ') : ''; };
    const id = g(/<id>([\s\S]*?)<\/id>/);
    const bare = id.split('/abs/')[1];
    if (!bare) continue;
    out.push({ json: {
      key: 'arxiv:' + bare.replace(/v\d+$/, ''),
      source: 'arXiv',
      title: g(/<title>([\s\S]*?)<\/title>/),
      abstract: g(/<summary>([\s\S]*?)<\/summary>/),
      url: id,
      date: g(/<published>([\s\S]*?)<\/published>/).slice(0, 10),
    }});
  }
}
return out;
"""

HF_Q = r"""
const w = $input.first().json;
// Curation date lags publication, so always cover one extra day. For a wide
// on-demand window poll every day in it — only ever polling two days was the
// other reason "past 10 days" returned almost nothing.
const days = Math.min(Math.max(Math.ceil((w.lookbackHours || 36) / 24) + 1, 2), 14);
const pad = n => String(n).padStart(2, '0');

const out = [];
for (let i = 0; i < days; i++) {
  const d = new Date(Date.now() - i * 86400000);
  out.push({ json: {
    url: `https://huggingface.co/api/daily_papers?date=${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}`,
  }});
}
return out;
"""

HF_PARSE = r"""
const out = [];
for (const item of $input.all()) {
  const j = item.json;
  const list = Array.isArray(j) ? j : (Array.isArray(j.data) ? j.data : [j]);
  for (const p of list) {
    const pa = (p && p.paper) || p;
    if (!pa || !pa.id) continue;
    out.push({ json: {
      key: 'arxiv:' + String(pa.id).replace(/v\d+$/, ''),
      source: 'HF',
      title: String(pa.title || '').replace(/\s+/g, ' ').trim(),
      abstract: String(pa.summary || '').replace(/\s+/g, ' ').trim(),
      url: 'https://huggingface.co/papers/' + pa.id,
      date: String(pa.publishedAt || '').slice(0, 10),
    }});
  }
}
return out;
"""

GH_Q = r"""
const w = $input.first().json;
// GitHub ANDs every term, so one broad query returns almost nothing.
// Five narrow queries instead. Unauthenticated search allows 10 req/min.
const qs = [
  'text-to-image evaluation',
  'image generation benchmark',
  'topic:text-to-image topic:benchmark',
  'image editing benchmark',
  'VLM judge image',
];
return qs.map(q => ({ json: {
  url: 'https://api.github.com/search/repositories?q='
     + encodeURIComponent(q + ' created:>' + w.ghSince)
     + '&sort=stars&order=desc&per_page=20',
}}));
"""

GH_PARSE = r"""
const out = [];
for (const item of $input.all()) {
  for (const r of (item.json && item.json.items) || []) {
    out.push({ json: {
      key: 'gh:' + r.full_name,
      source: 'GitHub',
      title: r.full_name,
      abstract: r.description || '',
      url: r.html_url,
      date: String(r.created_at || '').slice(0, 10),
    }});
  }
}
return out;
"""

PREFILTER = r"""
// Tuned against a real 4-day arXiv window + one HF day (132 records):
//   bare "image" + loose eval terms   -> 66/132 kept (robotics, surgical segmentation survive)
//   this pair                         ->  9/132 kept, real hits retained
//   requiring the eval term in title  ->  2/132 kept (too aggressive)
// GEN deliberately excludes bare "image" — it matches every vision paper.
// The classifier is still the precision stage; this just stops paying for obvious noise.
const IMG  = /(text-to-image|\bt2i\b|image generation|image editing|image synthesis|image generative|diffusion model|inpaint|visual generation|generated image|synthesized image|image generator)/i;
const EVAL = /(benchmark|metric|leaderboard|judge|human preference|rubric|evaluation (suite|protocol|framework|benchmark)|evaluating|we evaluate the)/i;
const seen = new Set();
const out = [];
for (const it of $input.all()) {
  const j = it.json;
  if (!j || !j.key || seen.has(j.key)) continue;
  seen.add(j.key);
  const blob = `${j.title || ''} ${j.abstract || ''}`;
  if (!IMG.test(blob) || !EVAL.test(blob)) continue;
  out.push({ json: j });
}
return out;
"""

DROP_SEEN = r"""
// Main input is the single Notion query result; candidates come from the
// prefilter node by reference.
const res = ($input.first().json && $input.first().json.results) || [];
const seen = new Set();
for (const page of res) {
  const t = page.properties && page.properties.Key
         && page.properties.Key.rich_text && page.properties.Key.rich_text[0];
  if (t && t.plain_text) seen.add(t.plain_text);
}

const mode = ($('Window').first().json || {}).mode || 'scheduled';
const cands = $('Keyword prefilter').all();

// Scheduled: only unseen items are worth classifying at all.
// On-demand ("past 3 days"): the user asked what is IN that window, so show
// everything and just remember which rows are already archived.
const out = [];
for (const c of cands) {
  const already = seen.has(c.json.key);
  if (mode !== 'ondemand' && already) continue;
  out.push({ json: { ...c.json, alreadySeen: already, mode } });
}
// Bound the cost of a wide on-demand query.
return mode === 'ondemand' ? out.slice(0, 60) : out;
"""

BATCH = r"""
// Batches of 10. After the tuned prefilter this is ~1 call/day, and it stays
// bounded if a busy day pushes the candidate count up.
const items = $input.all().map(i => i.json);
const SIZE = 10;
const out = [];
for (let i = 0; i < items.length; i += SIZE) {
  const batch = items.slice(i, i + SIZE).map(r => ({
    key: r.key,
    title: r.title,
    abstract: String(r.abstract || '').slice(0, 1200),
  }));
  out.push({ json: {
    batchIndex: out.length,
    count: batch.length,
    payload: Buffer.from(JSON.stringify(batch, null, 1)).toString('base64'),
  }});
}
return out;
"""

PARSE_CLASS = r"""
// The container authenticates the Claude CLI over OAuth, and OAuth tokens can be
// revoked out from under it (this has already happened once). A revoked token would
// otherwise look identical to "quiet news day", so detect it and shout instead.
const AUTH_FAIL = /failed to authenticate|oauth .*(revoked|expired)|invalid api key|authentication_error|401/i;

const out = [];
let authError = null;
for (const it of $input.all()) {
  const j = it.json || {};
  const txt = String(j.stdout || '').trim();
  const err = String(j.stderr || '').trim();

  if (AUTH_FAIL.test(txt) || AUTH_FAIL.test(err)) {
    authError = (err || txt).split('\n')[0].slice(0, 300);
    continue;
  }

  const s = txt.indexOf('[');
  const e = txt.lastIndexOf(']');
  if (s === -1 || e === -1 || e <= s) continue;   // batch-level skip, not a run failure
  let arr;
  try { arr = JSON.parse(txt.slice(s, e + 1)); } catch (err2) { continue; }
  if (!Array.isArray(arr)) continue;
  for (const r of arr) if (r && r.key) out.push({ json: r });
}

if (authError && !out.length) return [{ json: { __authError: authError } }];
return out;
"""

KEEP = r"""
// Propagate an auth failure straight through to the digest.
const first = $input.first();
if (first && first.json && first.json.__authError) {
  return [{ json: { authError: first.json.__authError, empty: true } }];
}

const byKey = {};
for (const c of $('Drop already sent').all()) byKey[c.json.key] = c.json;

const out = [];
for (const it of $input.all()) {
  const r = it.json;
  if (!r || r.relevant !== true) continue;
  const src = byKey[r.key] || {};
  const nd = r.new_dimension && r.new_dimension.name ? r.new_dimension : null;
  out.push({ json: {
    key: r.key,
    title: src.title || r.key,
    url: src.url || '',
    date: src.date || '',
    source: src.source || '',
    type: r.type || 'metric',
    alreadySeen: src.alreadySeen === true,
    dimensions: Array.isArray(r.dimensions) ? r.dimensions.filter(Boolean) : [],
    newDimension: nd,
    oneLine: r.one_line || '',
    why: r.why_it_matters || '',
    confidence: typeof r.confidence === 'number' ? r.confidence : 0,
  }});
}
// Always emit something so the digest still fires (and silence always means breakage).
return out.length ? out : [{ json: { empty: true } }];
"""

DIGEST = r"""
// HTML, not MarkdownV2: MarkdownV2 needs 18 characters escaped and paper titles
// are full of them. HTML needs only & < >.
const esc = s => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const all = $input.all().map(i => i.json).filter(Boolean);
const today = new Date().toISOString().slice(0, 10);

const auth = all.find(r => r.authError);
if (auth) {
  return [{ json: { text:
      `⚠️ <b>Image-Eval Radar — ${today}</b>\n`
    + `<b>The Claude CLI in the container is not authenticated.</b>\n`
    + `<code>${esc(auth.authError)}</code>\n\n`
    + `Nothing was classified today. Fix with:\n`
    + `<code>docker exec -it n8n-claude-n8n-1 claude</code>  then  <code>/login</code>\n\n`
    + `<i>Note: this also breaks the job-scoring and yellow card workflows.</i>` } }];
}

const rows = all.filter(r => !r.empty);

// Label the window so an on-demand answer is self-describing.
let win;
try {
  const w = $('Window').first().json || {};
  if (w.mode === 'ondemand') {
    const h = Number(w.lookbackHours) || 24;
    win = h % 24 === 0 && h >= 24 ? `past ${h / 24} day${h === 24 ? '' : 's'}` : `past ${h}h`;
  }
} catch (e) { /* Window not reachable in a partial run */ }
const title = win ? `Image-Eval Radar — ${win}` : `Image-Eval Radar — ${today}`;

if (!rows.length) {
  return [{ json: { text: `<b>Image-Eval Radar — ${today}</b>\nNo new eval methods today.` } }];
}

const msgs = [];
let header = `<b>${title}</b>\n${rows.length} item${rows.length > 1 ? 's' : ''}`;

const newDims = rows.filter(r => r.newDimension);
if (newDims.length) {
  header += `\n\n🆕 <b>NEW DIMENSION PROPOSED</b>`;
  for (const r of newDims) {
    header += `\n• <b>${esc(r.newDimension.name)}</b> — ${esc(r.newDimension.definition)}`
            + `\n  from <a href="${esc(r.url)}">${esc(r.title)}</a>`;
  }
  header += `\n<i>Not added to the taxonomy — approve it in dimensions.md first.</i>`;
}
const groups = {};
for (const r of rows) {
  const dims = r.dimensions.length ? r.dimensions : ['unmapped'];
  for (const d of dims) (groups[d] = groups[d] || []).push(r);
}

// A paper is cross-listed under every dimension it touches, which is the point —
// but printing it in full each time triples the digest. Full entry on first
// appearance, one-liner back-reference after that.
const shown = new Set();
const blocks = [];
for (const dim of Object.keys(groups)) {
  let block = `\n\n<b>${esc(dim)}</b>`;
  for (const r of groups[dim]) {
    const link = `<a href="${esc(r.url)}">${esc(r.title)}</a>`;
    if (shown.has(r.key)) {
      block += `\n• ${link} <i>(above)</i>`;
    } else {
      shown.add(r.key);
      block += `\n• ${link}  <i>[${esc(r.type)} · ${esc(r.source)}]</i>`
             + `\n  ${esc(r.oneLine)}`
             + `\n  <i>${esc(r.why)}</i>`;
    }
  }
  blocks.push(block);
}

// Pack blocks into as few messages as Telegram's 4096 cap allows.
const LIMIT = 3800;
let cur = header;
for (const b of blocks) {
  if (cur.length + b.length > LIMIT) { msgs.push(cur); cur = b.replace(/^\n\n/, ''); }
  else { cur += b; }
}
msgs.push(cur);

// Last resort: a single block bigger than the cap still has to be split.
const final = [];
for (let m of msgs) {
  while (m.length > LIMIT) { final.push(m.slice(0, LIMIT)); m = m.slice(LIMIT); }
  if (m.trim()) final.push(m);
}
return final.map(t => ({ json: { text: t } }));
"""

# ---------------------------------------------------------------- node helpers

def code(name, js, x, y, once=False):
    n = {"parameters": {"jsCode": js.strip()}, "id": name, "name": name,
         "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [x, y]}
    if once:
        n["executeOnce"] = True
    return n


def http(name, x, y, **params):
    once = params.pop("_once", False)
    creds = params.pop("_creds", None)
    n = {"parameters": params, "id": name, "name": name,
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [x, y]}
    if once:
        n["executeOnce"] = True
    if creds:
        n["credentials"] = creds
    return n


NOTION_HEADERS = {"parameters": [
    {"name": "Notion-Version", "value": "2022-06-28"},
    {"name": "Content-Type", "value": "application/json"},
]}

nodes = [
    # Ships DISABLED. n8n 2.x refuses to publish a workflow whose Execute Workflow
    # target is unpublished, so the radar has to be published for the command
    # listener to call it — but publishing also arms this cron. Disabling the node
    # keeps the workflow publishable while on-demand-only.
    # Enable this node (right-click -> Activate) when you want the 8am digest.
    {"parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "0 8 * * *"}]}},
     "id": "Daily 8am", "name": "Daily 8am", "type": "n8n-nodes-base.scheduleTrigger",
     "typeVersion": 1.1, "position": [-880, 400], "disabled": True},

    # Called by the "Image-Eval Radar — Commands" workflow with {lookbackHours, mode}.
    {"parameters": {"inputSource": "passthrough"},
     "id": "On demand", "name": "On demand",
     "type": "n8n-nodes-base.executeWorkflowTrigger", "typeVersion": 1.1,
     "position": [-880, 560]},

    code("Mode: scheduled",
         "return [{ json: { lookbackHours: 36, mode: 'scheduled' } }];", -660, 240),

    code("Window", WINDOW, -440, 400),

    # --- arXiv branch
    code("arXiv queries", ARXIV_Q, -220, 100),
    http("arXiv fetch", -220, 100, url="={{ $json.url }}",
         options={"response": {"response": {"responseFormat": "text"}},
                  "batching": {"batch": {"batchSize": 1, "batchInterval": 3000}}}),
    code("arXiv parse", ARXIV_PARSE, 0, 100),

    # --- HuggingFace branch
    code("HF queries", HF_Q, -220, 280),
    http("HF fetch", -220, 280, url="={{ $json.url }}", options={}),
    code("HF parse", HF_PARSE, 0, 280),

    # --- GitHub branch
    code("GitHub queries", GH_Q, -220, 460),
    http("GitHub fetch", -220, 460, url="={{ $json.url }}",
         sendHeaders=True,
         headerParameters={"parameters": [
             {"name": "Accept", "value": "application/vnd.github+json"},
             {"name": "User-Agent", "value": "image-eval-radar"},
         ]},
         options={"batching": {"batch": {"batchSize": 1, "batchInterval": 7000}}}),
    code("GitHub parse", GH_PARSE, 0, 460),

    # --- funnel
    {"parameters": {"numberInputs": 3}, "id": "Merge sources", "name": "Merge sources",
     "type": "n8n-nodes-base.merge", "typeVersion": 3, "position": [220, 280]},

    code("Keyword prefilter", PREFILTER, 440, 280),

    http("Notion: recent keys", 660, 280, _once=True, _creds=NOTION_CRED, method="POST",
         url="=https://api.notion.com/v1/databases/{{ $env.NOTION_EVAL_DB_ID }}/query",
         authentication="genericCredentialType", genericAuthType="httpHeaderAuth",
         sendHeaders=True, headerParameters=NOTION_HEADERS,
         sendBody=True, specifyBody="json",
         jsonBody='={"page_size": 100, "sorts": [{"property": "Date", "direction": "descending"}]}',
         options={}),

    code("Drop already sent", DROP_SEEN, 880, 280),
    code("Batch for Claude", BATCH, 1100, 280),

    # executeOnce is a PARAMETER of this node (distinct from the generic node-level
    # setting) and it DEFAULTS TO TRUE, silently truncating input to items[0].
    # With 6 batches queued that classified only the first 10 candidates and threw
    # the rest away. Invisible on the 36h run, which only ever makes one batch.
    {"parameters": {"executeOnce": False,
                    "command":
        "=TAX=$(cat /image-eval-radar/dimensions.md); "
        "PROMPT=$(cat /image-eval-radar/prompts/classify.txt); "
        "printf '%s' \"{{ $json.payload }}\" | base64 -d > /tmp/eval-batch.json; "
        "{ printf '%s\\n' \"$PROMPT\"; cat /tmp/eval-batch.json; } | "
        "/usr/local/bin/claude -p --model claude-haiku-4-5 --append-system-prompt \"$TAX\""},
     "id": "Claude: classify", "name": "Claude: classify",
     "type": "n8n-nodes-base.executeCommand", "typeVersion": 1, "position": [1320, 280],
     "onError": "continueRegularOutput"},

    code("Parse classifications", PARSE_CLASS, 1540, 280),
    code("Keep relevant", KEEP, 1760, 280),

    # An IF node applies strict type validation to its rightValue and rejects
    # undefined/'' against a boolean operator. A Code node has no such checks and
    # returning [] simply means the downstream Notion write does not execute.
    code("Only real hits",
         "// Never re-write a row that is already archived — an on-demand query\n"
         "// re-surfaces old items on purpose, but must not duplicate them.\n"
         "return $input.all().filter(i => !i.json.empty && !i.json.alreadySeen);",
         1980, 160),

    http("Notion: write row", 2200, 160, _creds=NOTION_CRED, method="POST",
         url="https://api.notion.com/v1/pages",
         authentication="genericCredentialType", genericAuthType="httpHeaderAuth",
         sendHeaders=True, headerParameters=NOTION_HEADERS,
         sendBody=True, specifyBody="json",
         jsonBody="={{ JSON.stringify({\n"
                  "  parent: { database_id: $env.NOTION_EVAL_DB_ID },\n"
                  "  properties: {\n"
                  "    Name:        { title: [{ text: { content: $json.title.slice(0,200) } }] },\n"
                  "    Key:         { rich_text: [{ text: { content: $json.key } }] },\n"
                  "    Source:      { select: { name: $json.source || 'arXiv' } },\n"
                  "    Date:        $json.date ? { date: { start: $json.date } } : { date: null },\n"
                  "    Type:        { select: { name: $json.type } },\n"
                  "    Dimensions:  { multi_select: $json.dimensions.map(d => ({ name: d })) },\n"
                  "    'Proposed Dimension': { rich_text: $json.newDimension\n"
                  "        ? [{ text: { content: $json.newDimension.name + ' — ' + $json.newDimension.definition } }] : [] },\n"
                  "    Summary:     { rich_text: [{ text: { content: $json.oneLine.slice(0,1900) } }] },\n"
                  "    'Why it matters': { rich_text: [{ text: { content: $json.why.slice(0,1900) } }] },\n"
                  "    Confidence:  { number: $json.confidence },\n"
                  "    URL:         { url: $json.url || null },\n"
                  "    Status:      { select: { name: $json.newDimension ? 'proposed-dimension' : 'sent' } }\n"
                  "  }\n"
                  "}) }}",
         options={}),

    # NOTE: no executeOnce here — it would truncate input to the first item.
    # Code nodes already run once over all items by default.
    code("Format digest", DIGEST, 1980, 400),

    {"parameters": {"chatId": "={{ $env.TELEGRAM_EVAL_CHAT_ID }}",
                    "text": "={{ $json.text }}",
                    "additionalFields": {"parse_mode": "HTML",
                                         "disable_web_page_preview": True,
                                         "appendAttribution": False}},
     "credentials": TELEGRAM_CRED,
     "id": "Telegram: digest", "name": "Telegram: digest",
     "type": "n8n-nodes-base.telegram", "typeVersion": 1.2, "position": [2200, 400]},
]

connections = {
    "Daily 8am":            {"main": [[{"node": "Mode: scheduled", "type": "main", "index": 0}]]},
    "Mode: scheduled":      {"main": [[{"node": "Window", "type": "main", "index": 0}]]},
    "On demand":            {"main": [[{"node": "Window", "type": "main", "index": 0}]]},
    "Window":               {"main": [[{"node": "arXiv queries", "type": "main", "index": 0},
                                       {"node": "HF queries", "type": "main", "index": 0},
                                       {"node": "GitHub queries", "type": "main", "index": 0}]]},
    "arXiv queries":        {"main": [[{"node": "arXiv fetch", "type": "main", "index": 0}]]},
    "arXiv fetch":          {"main": [[{"node": "arXiv parse", "type": "main", "index": 0}]]},
    "arXiv parse":          {"main": [[{"node": "Merge sources", "type": "main", "index": 0}]]},
    "HF queries":           {"main": [[{"node": "HF fetch", "type": "main", "index": 0}]]},
    "HF fetch":             {"main": [[{"node": "HF parse", "type": "main", "index": 0}]]},
    "HF parse":             {"main": [[{"node": "Merge sources", "type": "main", "index": 1}]]},
    "GitHub queries":       {"main": [[{"node": "GitHub fetch", "type": "main", "index": 0}]]},
    "GitHub fetch":         {"main": [[{"node": "GitHub parse", "type": "main", "index": 0}]]},
    "GitHub parse":         {"main": [[{"node": "Merge sources", "type": "main", "index": 2}]]},
    "Merge sources":        {"main": [[{"node": "Keyword prefilter", "type": "main", "index": 0}]]},
    "Keyword prefilter":    {"main": [[{"node": "Notion: recent keys", "type": "main", "index": 0}]]},
    "Notion: recent keys":  {"main": [[{"node": "Drop already sent", "type": "main", "index": 0}]]},
    "Drop already sent":    {"main": [[{"node": "Batch for Claude", "type": "main", "index": 0}]]},
    "Batch for Claude":     {"main": [[{"node": "Claude: classify", "type": "main", "index": 0}]]},
    "Claude: classify":     {"main": [[{"node": "Parse classifications", "type": "main", "index": 0}]]},
    "Parse classifications":{"main": [[{"node": "Keep relevant", "type": "main", "index": 0}]]},
    "Keep relevant":        {"main": [[{"node": "Only real hits", "type": "main", "index": 0},
                                       {"node": "Format digest", "type": "main", "index": 0}]]},
    "Only real hits":       {"main": [[{"node": "Notion: write row", "type": "main", "index": 0}]]},
    "Format digest":        {"main": [[{"node": "Telegram: digest", "type": "main", "index": 0}]]},
}

workflow = {
    "name": "Image-Eval Radar",
    "nodes": nodes,
    "connections": connections,
    "active": False,
    "settings": {"executionOrder": "v1", "timezone": "America/Toronto"},
    "tags": [],
}

with open(OUT, "w") as f:
    json.dump(workflow, f, indent=2)
print(f"wrote {OUT}  ({len(nodes)} nodes)")


# =====================================================================
# Second workflow: the Telegram command listener.
#
# It has to be a SEPARATE workflow because n8n only fires schedule triggers on
# ACTIVE workflows — this one stays active and polls, while the radar itself can
# stay inactive and be run by hand.
#
# Polling getUpdates rather than the Telegram Trigger node: that node is
# webhook-based and this n8n has no public URL.
# =====================================================================

CMD_OUT = os.path.expanduser("~/image-eval-radar/workflow-commands.json")
# NOTE: this is the *radar* workflow id. Importing a workflow as new reassigns ids —
# after any re-import, re-check this against the actual "Image-Eval Radar" id or the
# listener can end up calling itself in an infinite loop.
MAIN_WORKFLOW_ID = "REPLACE_WITH_RADAR_WORKFLOW_ID"

NEXT_OFFSET = r"""
// getUpdates is consume-once: acknowledge everything we have already read.
// Static data persists only on active workflows — which this one is.
const sd = $getWorkflowStaticData('global');
return [{ json: { offset: (sd.tgOffset || 0) + 1 } }];
"""

PARSE_CMD = r"""
const sd = $getWorkflowStaticData('global');
const res = ($input.first().json && $input.first().json.result) || [];

// "past 24h" | "past 3 days" | "past 2 weeks"
const RE = /^\s*past\s+(\d+)\s*(h|hr|hrs|hour|hours|d|day|days|w|week|weeks)\s*$/i;

let maxId = sd.tgOffset || 0;
const found = [];
for (const u of res) {
  if (u.update_id > maxId) maxId = u.update_id;
  const post = u.channel_post || u.message;
  const text = post && post.text;
  if (!text) continue;
  const m = String(text).match(RE);
  if (!m) continue;
  const n = parseInt(m[1], 10);
  const unit = m[2].toLowerCase()[0];
  const hours = unit === 'h' ? n : unit === 'd' ? n * 24 : n * 24 * 7;
  found.push({ json: {
    lookbackHours: Math.min(Math.max(hours, 1), 24 * 30),   // clamp 1h .. 30d
    mode: 'ondemand',
    commandText: String(text).trim(),
  }});
}

// Mark everything consumed even when no command matched, so a chatty channel
// does not make us re-scan the same updates forever.
sd.tgOffset = maxId;

// One run per poll: if several commands arrived, honour the newest.
return found.slice(-1);
"""

cmd_nodes = [
    {"parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "* * * * *"}]}},
     "id": "Every minute", "name": "Every minute",
     "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.1, "position": [-600, 300]},

    code("Next offset", NEXT_OFFSET, -380, 300),

    http("Telegram: getUpdates", -160, 300,
         url="=https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/getUpdates"
             "?offset={{ $json.offset }}&timeout=0&allowed_updates=[\"channel_post\",\"message\"]",
         options={}),

    code("Parse command", PARSE_CMD, 60, 300),

    {"parameters": {"chatId": "={{ $env.TELEGRAM_EVAL_CHAT_ID }}",
                    "text": "=\U0001F50E Working on <b>{{ $json.commandText }}</b>\u2026\n<i>A day or two takes about a minute; a wide window can take several, since it classifies in batches.</i>",
                    "additionalFields": {"parse_mode": "HTML",
                                         "disable_web_page_preview": True,
                                         "appendAttribution": False}},
     "credentials": TELEGRAM_CRED,
     "id": "Ack", "name": "Ack",
     "type": "n8n-nodes-base.telegram", "typeVersion": 1.2, "position": [280, 300]},

    {"parameters": {"workflowId": {"__rl": True, "value": MAIN_WORKFLOW_ID, "mode": "id"},
                    "options": {"waitForSubWorkflow": False}},
     "id": "Run radar", "name": "Run radar",
     "type": "n8n-nodes-base.executeWorkflow", "typeVersion": 1.2, "position": [500, 300]},
]

cmd_connections = {
    "Every minute":         {"main": [[{"node": "Next offset", "type": "main", "index": 0}]]},
    "Next offset":          {"main": [[{"node": "Telegram: getUpdates", "type": "main", "index": 0}]]},
    "Telegram: getUpdates": {"main": [[{"node": "Parse command", "type": "main", "index": 0}]]},
    # BOTH branch off Parse command. Chaining Run radar after Ack loses the command
    # payload: a Telegram node outputs its API response, not the item it received,
    # so lookbackHours/mode never reached the radar and it silently ran in
    # scheduled mode with the default 36h window.
    "Parse command":        {"main": [[{"node": "Ack", "type": "main", "index": 0},
                                       {"node": "Run radar", "type": "main", "index": 0}]]},
}

cmd_workflow = {
    "name": "Image-Eval Radar — Commands",
    "nodes": cmd_nodes,
    "connections": cmd_connections,
    "active": False,
    "settings": {"executionOrder": "v1", "timezone": "America/Toronto"},
    "tags": [],
}

with open(CMD_OUT, "w") as f:
    json.dump(cmd_workflow, f, indent=2)
print(f"wrote {CMD_OUT}  ({len(cmd_nodes)} nodes)")
