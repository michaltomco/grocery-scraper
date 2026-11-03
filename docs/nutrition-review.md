# Opt-in nutrition review cascade

`nutrition_review.py` is an **advisory investigation tool**, not a nutrition cache
writer, website change, or scheduled job. It consumes canonical local Laya
`matches.json` (`records`, with `id`, `product_text`, `source_product`, `values`,
`source_url`, and `prob`). The Codex model researches identity through the
**Hermes Python API**, not standalone Codex CLI or Hermes CLI/gateway.

## Stages and calibration policy

1. Preserve the complete original retailer/source pair; do not approximate-join
   product names or regenerate Laya scores.
2. Reuse `nutrition.nutrient_issues` and `nutrition.identity_issues`. Additional
   checks reject malformed/boolean/nonfinite/negative nutrients, grams over 100
   per 100 g, protein + fat + carbohydrates over **102 g** (2 g rounding
   tolerance), and energy over **1,000 kcal** or its kJ equivalent. Macro
   subcomponents such as sugars and saturated fat are not counted again.
   Missing source metadata/usable URL is flagged, never silently repaired.
3. Treat Laya as an identity signal, not nutrition measurement validation.
4. Investigate **every candidate**, including near-1.0 scores. More deterministic
   issues come first, then lower Laya probability, then candidate ID.

Explicitly raw Czech/English profiles paired with passata, dried/canned foods,
dairy or high-protein variants receive conservative lexical conflict flags.
These are review signals, not exhaustive product research. New source evidence
must explain conflicts; it cannot delete the recorded issues. There is no score
threshold, automatic approval, nutrition estimation, or cache promotion.

**Policy version 2** separates the new transport from older review artifacts.
Re-run `prepare` for old queues. Candidate fingerprints include semantic identity,
source, nutrition and policy fields, not changing prices, checkout paths, Laya
probabilities or usage telemetry. The complete original pair remains in artifacts.

## Commands

Use the project's existing environment for the runner and validators. Configure
`HERMES_REVIEW_PYTHON` separately to the absolute interpreter of an installed
Hermes environment that can `import run_agent` from any working directory.
Do not assume the project's `.venv` or an old Hermes checkout has that package.
Existing Hermes OAuth authentication resolves through its normal home/auth store;
this adapter neither copies credentials nor changes global configuration.

```bash
export HERMES_REVIEW_PYTHON=/absolute/path/to/installed/hermes/venv/bin/python

.venv/bin/python nutrition_review.py prepare \
  --scores /absolute/path/to/canonical-laya/matches.json \
  --out-dir /absolute/path/to/review-prepared

# No model call: list the next three uncached candidates.
.venv/bin/python nutrition_review.py review \
  --queue /absolute/path/to/review-prepared/queue.json \
  --out-dir /absolute/path/to/review-results --dry-run --limit 3

# Actual Codex-model research, web-only Hermes Python API.
.venv/bin/python nutrition_review.py review \
  --queue /absolute/path/to/review-prepared/queue.json \
  --out-dir /absolute/path/to/review-results \
  --limit 1 --timeout 180 --model gpt-6.1-sol
```

`--model` defaults explicitly to `gpt-6.1-sol`; provider is `openai-codex`.
`prepare` also exports `result-schema.json`. `--limit 0` makes no attempts;
negative limits and nonpositive/nonfinite timeouts are errors. Limits count
**attempts**, including failures. Successful structurally revalidated decisions
are skipped on resumption; failures remain retryable.

## Web-only API boundary

`run_codex` retains its historical Python name for callers, but launches only
`[HERMES_REVIEW_PYTHON, nutrition_web_reviewer.py, --model, ..., --timeout, ...]`.
The untrusted prompt travels on stdin, never in shell code or command argv.
The subprocess uses `shell=False`, an empty temporary cwd beneath the active
Hermes home's `cache/scratch`, and a bounded wall-clock timeout. Its inherited
environment is restricted to `HOME`, `PATH`, optional `HERMES_HOME`, and a new
scratch `TMPDIR`. No project tokens or arbitrary parent environment variables
are forwarded. Missing/relative interpreter configuration fails closed, with
no fallback to either CLI.

Before any Hermes imports the helper sets `HERMES_SAFE_MODE=1`,
`HERMES_IGNORE_USER_CONFIG=1` and `HERMES_IGNORE_RULES=1`. It constructs `AIAgent`
with `enabled_toolsets=['web']`, `max_iterations=4`, `quiet_mode=True`,
`skip_context_files=True`, `skip_memory=True`, `skip_background_review=True`,
`load_soul_identity=False`, and a run-time budget. User/plugin discovery, generic
MCP discovery, hooks, repository rules, memory and Soul context are not enabled.

**Important backend detail:** this Hermes build implements web providers in its
bundled `plugins.web` package, and safe mode skips their discovery too. The
helper explicitly instantiates only the installed, bundled
`plugins.web.firecrawl.provider.FirecrawlWebSearchProvider` and registers that
web backend with the core registry. It does not invoke plugin registration
hooks or scan/load user plugins. The backend's normal web HTTP/gateway behavior
and auth resolution remain in use. This is fixed trusted backend code, not an
additional model tool or arbitrary MCP server registration. Without it a tool
schema can exist while retrieval fails with “provider not registered”.

The helper inspects **actual model-visible function schemas** and
`agent.valid_tool_names` before the first model turn. Both must be exactly
`{'web_search', 'web_extract'}`. Missing tools, additions, duplicate schemas or
non-function schemas fail closed. This is not based on a model's self-report.
A subclass also checks the catalog and every requested name at tool dispatch;
a tool-start callback terminates the isolated helper on forbidden names or
catalog drift (ordinary progress callback exceptions can otherwise be swallowed
by Hermes). The catalog is checked again after conversation completion.
There are no exposed terminal, file, patch, browser, skills or execution tools.
Hermes `web_extract` retains its public-URL/SSRF and website-policy checks.

Only a nonempty assistant `final_response` from a result with `completed=True`
and `failed=False`, `partial=False`, `interrupted=False` is emitted on stdout.
Library diagnostics and tool names go to stderr/`engine.log`, not the result.
The prompt includes the complete result schema; the parent then performs strict
JSON parsing and existing `validate_decision` validation. There is no promise
of a provider-native structured-output API. Malformed output is a retryable
error, not repaired or silently accepted.

### Standalone Codex CLI remains disabled

The previous standalone CLI transport was removed because feature flags and a
read-only shell sandbox did not establish an authoritative web-only tool
catalog. Upstream `spec_plan.rs` can register `apply_patch` based on model
metadata independently of shell-tool settings. A prior installed CLI probe did
not prove isolation. No standalone CLI fallback or override is restored here.
The Hermes CLI/gateway is also deliberately bypassed: its safe-mode web-tool
selection was observed to expose no tools in this environment.

### Recorded real smoke test

The installed Hermes interpreter was verified with `sys.executable` as:

```text
/home/arch/.hermes/installs/31dca0a9f6c507df/environments/59f233b54ae340f19806eadf649c65e3/venv/bin/python
```

A real bounded helper subprocess using `gpt-6.1-sol` verified the exact two-tool
catalog, called `web_extract` on `https://www.nutridata.cz/`, and returned:

```json
{"title":"NutriData.cz - pomáháme vám porozumět co jíte","quote":"Motivujte sebe i přátele sdílením svých jídelníčků."}
```

Its log contained `verified_tools: web_extract,web_search` and
`tool_start: web_extract`. This is a transport/retrieval smoke test, **not** a
nutrition decision or independently verified nutrient label. The first safe-mode
probe exposed the missing-provider problem described above rather than a real
retrieval; the explicit bundled backend corrected it without enabling discovery.

## External result ingestion

An independently restricted evidence reviewer may supply one raw object using
the exported schema, without any model call by this runner:

```bash
.venv/bin/python nutrition_review.py review \
  --queue /absolute/path/to/review-prepared/queue.json \
  --out-dir /absolute/path/to/review-results \
  --input-result /absolute/path/to/external-result.json --limit 1
```

The candidate ID must exist in the queue. Do not supply a validated
`decision.json`: its added local safety fields are not part of the raw schema.

## Result contract and evidence limitations

Raw fields must be exactly:

- `candidate_id`: the queued pair ID;
- `classification`: `exact_match`, `compatible_generic`, `mismatch`, or
  `insufficient_evidence`;
- `evidence`: array of `{ "url": "https://...", "quote": "verbatim passage" }`;
- `explanation`: nonempty identity/preparation/uncertainty explanation;
- `nutrition_values`: array, normally empty; optional proposals contain
  `nutrient`, numeric `value`, `unit` (`g`, `mg`, `ug`, `µg`, `kcal`, `kJ`),
  `basis` (`per_100g`), `source_url`, and `quote`.

Exact/generic classifications require evidence. Evidence URLs must be syntactic
credential-free HTTP(S) without whitespace. Every numeric proposal must refer to
the identical URL+quote in `evidence`, quote its number and unit, and pass the
deterministic guards. No serving conversions, calculations, invented quotations,
model-memory nutrition, discount-aggregator evidence or missing-value filling.
Unsourced or invalid proposals reject the entire decision. Duplicate JSON keys
and JSON NaN/Infinity constants are rejected.

**Quote linkage is not verification.** Actual tool execution does not prove every
subsequent model quotation or attribution. The runner is not an independent HTTP
verification client. Every accepted *structural* result is stamped:

```json
{
  "approval_allowed": false,
  "final_acceptance": false,
  "evidence_status": "requires_verification",
  "nutrition_status": "unverified_proposal"
}
```

A human must independently retrieve cited pages, verify quotes and nutrition
attribution/basis, and resolve food/preparation/flavour conflicts. There is no
accept/promote command and no write to production nutrition caches.

## Persistence, resumption and failures

`review_cache_key(candidate, model=None, input_result=None)` is the shared public
cache-identity function for runner and artifact collection. It uses candidate
fingerprint, effective model, engine `hermes-web-v1` (or `external`) and external
result content when present. Implicit and explicit default model names reuse
the same success. Changed semantic evidence or policy invalidates it.
Each unique attempt contains:

- `input.json`, `prompt.txt`, `schema.json`;
- `command.json` for a configured helper launch, recording model, provider,
  engine, argv, tool allowlist, stdin transport and timeout (never credentials);
- `engine.log` (or an explicit no-call ingestion log);
- `raw-final.json` when final output exists;
- `validated-decision.json` on success, or retryable `error.json`.

A timeout kills/waits for the helper through `subprocess.run`, preserves partial
stderr and a timeout marker, and never creates a success decision. Nonzero
helper exits likewise do not cache success. The parent directory's
`decision.json` is the structurally revalidated skip marker; `report.json`
summarizes the current invocation, not lifetime totals. Dry runs write
`dry-run.json`. Artifacts use same-filesystem atomic replace. Errors produce
stderr diagnostics and CLI exit status 1. Avoid concurrent runs against the same
output directory if duplicate model costs matter.

## Tests and TDD

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_nutrition_*review*.py' -v
.venv/bin/python -m unittest discover -s tests -v
```

Development used RED/GREEN slices for API construction, catalog rejection,
runtime dispatch/callback rejection, incomplete-result handling, stdin/final-only
output, clean bounded subprocess transport, default-model cache identity,
shared cache keys, timeout artifacts and the explicitly registered bundled web
backend. Fake AIAgents exercise policy failures without model calls. Workflow
responses are test-only fixtures; the real smoke test is recorded separately.
Existing semantic-fingerprint and advisory-validation regressions are retained.
