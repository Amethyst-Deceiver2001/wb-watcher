# Working rules for wb_watch

## Exploration vs. crawling jobs

- **Inline web searches, Telegram post lookups (WebFetch/WebSearch), and one-off API
  requests (curl/python against WB endpoints) are fine to run directly** — this is
  for reconnaissance: checking what an endpoint returns, reading a single Telegram
  post, confirming a keyword hits, sanity-checking a schema change.
- **Full crawling/tracking jobs are never run directly from here.** Anything that
  does real work at scale — `wb-watch track --all`, `wb-watch discover`,
  `wb-watch tg-scan` (especially `--full` historical pulls), or any new bulk
  script — must instead be:
  1. Written or updated as a proper script/CLI command in this repo.
  2. Handed to the user as an exact terminal command to run themselves.
  3. Left for the user to execute and report back; do not run it in the
     background and self-report results.
- After the user runs a job and shares/points at the output, inspect and interpret
  it — that's the follow-up step, not the initial execution.

## Why

Crawling jobs hit live third-party services (Wildberries, Telegram) at volume and
can run long; the user wants visibility and control over when those actually fire,
not have them kicked off silently mid-conversation.

## Institutional buyer vs. dual-use market (military_class classification)

`military_class` (`wb_watch/analysis/military_class.py`) asks specifically whether
an item's demand is SVO/front-line driven — it is not "is this tactical/camo/
military-styled gear." Two lookalike cases must be told apart:

- **Wrong institution entirely → `other`.** Items branded for domestic Interior
  Ministry law enforcement (ОМОН, МВД, полиция, Росгвардия — the last also reports
  to the Interior Ministry, not the military) have a structurally distinct buyer
  with no plausible SVO connection, even when the listing uses "тактический"/
  "военный"/camo-colorway language (police tactical gear reuses that vocabulary
  freely). These get force-excluded to `other` unless a genuinely unambiguous
  SVO/army marker also co-occurs (армейск, для армии, военнослужащ, сво, штурмов
  — deliberately *not* военн/тактическ/баллист, which appear on police-branded
  listings too). See `_INTERIOR_MINISTRY`/`_GENUINE_MILITARY_MARK` in
  `military_class.py`.
- **Civilian market + real front-line demand → keep as `dual_use_demand`.**
  Items marketed "для охоты" (hunting) or "туристический" (camping/tourism) —
  night-vision scopes, camo netting, sleeping bags, sapper grappling hooks sold
  as travel gear — are the textbook case `dual_use_demand` exists for: a genuine
  civilian market riding alongside real SVO demand. Hunting/tourism marketing
  language does *not* disqualify an item the way police-institution branding
  does. Don't extend the interior-ministry-style exclusion to these.

The distinguishing question when a new lookalike cluster turns up: is this a
*different institutional buyer* (exclude), or a *civilian market covering the
same genuinely dual-use good* (keep)? `categorize.py`'s functional `category`
axis is unaffected either way — it classifies item type, not war-relevance, so
police-branded tactical wear still counts as `tactical_wear` there.

## Quotes: never paraphrase or invent

Any review, seller reply, or other quoted material used anywhere in this
project (site copy, exports, write-ups) must be the original text only —
verbatim, with the author's own punctuation and spelling preserved as-is
(typos included). Never clean up grammar, fix punctuation, drop or reword a
clause, or otherwise paraphrase a quote to make it read better or fit a
character limit. If a quote is too long, either use a shorter genuine quote
instead or clearly mark an ellipsis — never silently trim. If a quote can't be
verified against the database (or another primary source), don't use it;
flag it instead of leaving it in on the assumption it's accurate.
