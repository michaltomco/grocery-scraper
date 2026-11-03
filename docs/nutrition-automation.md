# Automatic advisory nutrition investigation

`Nutrition advisory review` runs independently after every completed **Refresh grocery data** run. It never blocks extraction, price commits, or Pages, never commits reports, and never promotes nutrition. Main-branch manual dispatch is also available; its optional `dry_run=true` runs real cached Laya and prepares the queue but makes no Codex calls.

## Trigger and trust boundary

The self-hosted job accepts only upstream `head_branch == main` with `head_repository.full_name == github.repository`, or manual `refs/heads/main`. It always checks out trusted `main`, never the triggering SHA or an untrusted ref. Permissions are `contents: read`, `actions: read`. Dedicated concurrency `grocery-nutrition-review`, without cancellation, is separate from price refresh. A nonblocking filesystem lock additionally protects persistent state from simultaneous local runs.

`download-trigger` lists artifacts on the **exact triggering run ID** and downloads `refresh-status` and `nutrition-review-inputs` with `gh run download THAT_ID`. No cross-run latest-artifact lookup occurs. Missing status is an intentional skip (hourly schedule no-op). Merged status without immutable inputs explicitly skips as unpublished/no immutable snapshot; it never substitutes current main data. Expired/ambiguous artifacts, API errors, missing downloaded files, malformed JSON, or download errors fail visibly. The gate accepts partial retailer failures when `merge.status == merged` and at least one retailer is `refreshed`; all-failed extraction or failed merge skips. Upstream overall conclusion is deliberately not a success gate. Manual dispatch gates on the checked-out committed `refresh_status.json` instead and outputs the checkout data directory.

The refresh workflow creates and uploads `nutrition-review-inputs` **after Commit refreshed data**, only when merge is true, dashboard build succeeded, and the commit step succeeded. `snapshot` compares each input byte-for-byte with `git show HEAD:FILE`, rejecting missing or dirty/unpublished inputs. It freezes `refresh_status.json`, `nutrition_cache.json`, and `all_discounts.csv` with `manifest.json`: schema version 1, `source_commit` equal to that committed data HEAD, and SHA-256 per file. Snapshot/upload errors may mark refresh failed but do not block the existing `always()` website dispatch; Report refresh failures remains last.

Downloaded inputs live under `nutrition-review-output/inputs`. Validation requires the fixed file set (no arbitrary manifest paths), supported schema, hexadecimal commit ID, matching hashes, and snapshot status equal to the triggering status. Gate artifacts retain upstream artifact metadata and output an absolute `data_dir`. Trusted current main supplies execution code only; a newer checkout cannot replace upstream data or provenance. The scorer uses snapshot data while prepare/review execute from `--code-dir`; input HEAD comes from the verified manifest, not a Git lookup in the snapshot.

## Runner configuration

Runner labels: `[self-hosted, linux, x64, grocery-scraper]`; job timeout: 45 minutes. Requires `gh`, `uv`, a Hermes-installed Python environment with configured OpenAI Codex/ChatGPT OAuth, cached model weights, and the installed canonical scorer environment. Review uses the Codex model (default `gpt-6.1-sol`) through a restricted Hermes web-only adapter, not standalone Codex CLI; the adapter must expose only `web_search`/`web_extract` and fail closed otherwise. The job installs **only locked repository dependencies** with `uv sync --locked`; it never installs Torch/Laya or downloads model weights in the checkout.

Optional repository variables (same names are local environment overrides):

| Variable | Default |
| --- | --- |
| `NUTRITION_REVIEW_LIMIT` | `3`, hard-clamped to 0–3 new attempts/run |
| `NUTRITION_REVIEW_STATE_DIR` | `$HOME/.local/share/grocery-nutrition-review` |
| `LAYA_PYTHON` | `$HOME/Projects/laya-nutrition-correlation/.venv/bin/python` |
| `HERMES_REVIEW_PYTHON` | Set to the host's Hermes-installed Python interpreter; passed unchanged to the restricted review adapter |
| `LAYA_SCORER` | `$HOME/.hermes/skills/laya-record-correlation-scoring/scripts/laya_record_correlation.py` |

The scorer is invoked via a subprocess argument list (`shell=False`) using `--data-dir VERIFIED_INPUTS --subfolder english --lang cs --device cpu --batch-size 8` (current checkout for manual dispatch). Model cache-only behavior is enforced by the canonical loader and workflow offline environment flags. **English checkpoint on Czech text is unvalidated identity triage, not nutrition verification or a calibrated acceptance threshold.** Every candidate remains advisory and requires evidence verification.

Laya has a 1,800-second subprocess timeout. The queue preparation timeout is 120 seconds; each Codex review has 180 seconds, with an outer 660-second review-process timeout. Successful semantic candidate+policy+engine fingerprints resume from the persistent state directory; volatile usage/cache paths/price aliases do not invalidate decisions. Failed investigations remain retryable and fail the nutrition job, not the price pipeline. A Laya failure cannot silently reuse old scores: it fails visibly; queue creation requires a fresh successful scorer output.

## CLI and artifacts

Run using the repository environment:

```sh
uv run python nutrition_post_refresh.py snapshot --data-dir "$PWD" --out-dir nutrition-review-inputs
uv run python nutrition_post_refresh.py download-trigger --repository OWNER/REPO --run-id 123 --github-output "$GITHUB_OUTPUT"
uv run python nutrition_post_refresh.py gate --status refresh_status.json
uv run python nutrition_post_refresh.py run --data-dir "$PWD" --limit 1 --dry-run
uv run python nutrition_post_refresh.py run --data-dir "$PWD/nutrition-review-output/inputs" --code-dir "$PWD" --dry-run
```

`run` accepts `--code-dir` (defaults to the helper's directory), `--out-dir`, `--state-dir`, `--laya-python`, `--scorer`, and `--limit`. It delegates to the canonical scorer and `nutrition_review.py prepare` / `review` CLIs. Persisted attempts are located using the public `nutrition_review.review_cache_key(candidate)` so artifact copying follows the review engine's cache policy. `--dry-run` still performs real Laya inference; tests inject only the expensive scorer subprocess and exercise the real prepare/review CLI offline.

Artifacts named `nutrition-review-RUN_ID` upload with `always()` and retain for 14 days. Output stays in checkout-local `nutrition-review-output/` and is never committed. Successful runs include `automation.log`, `gate.json`, upstream `trigger-status.json`, Laya reports, `queue.json`, `result-schema.json`, `review-result.json`, relevant persisted review attempts/decisions, and `summary.json`. Summary includes candidate/attempted/reviewed/skipped/error and `pending_remaining` counts, input HEAD, input file SHA-256 fingerprints, and English-checkpoint limitation. Pending includes unattempted candidates and failed attempts, but not valid saved decisions. A dry-run consumes no reviews and therefore leaves candidates pending. Skips/failures publish the available log/gate/error summary, without pretending absent score/queue results exist.

Independent workflow permissions intentionally cannot modify production data. Do not add this workflow as a dependency of extraction or deployment.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_nutrition_post_refresh.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_nutrition*.py' -v
```

Tests cover partial/all-failed merge gating, exact upstream run artifact lookup, transport-error propagation, bounded dry-run with persistent state and real queue CLI, scorer failure, and trusted independent workflow wiring. Live workflow dispatch and model/Codex inference must be verified separately on the configured host before reporting automation as enabled.
