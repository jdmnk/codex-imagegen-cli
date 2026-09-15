# Codex Imagegen CLI audit — 2026-09-15

> Historical findings at `a739870`. Maintenance in 0.2.0 addresses these findings; see [maintenance validation](MAINTENANCE.md).

## Verdict

**Basic generation and editing still work, but the project needs updates before it is dependable for unattended use.** The most serious confirmed defect is token refresh writing a login-file format that current Codex cannot read. Other priorities are vulnerable locked dependencies, broken release metadata validation, and image options that do not match actual backend behavior.

Repository: [jdmnk/codex-imagegen-cli](https://github.com/jdmnk/codex-imagegen-cli). No local checkout was present; this audit cloned it to `/home/jure/codex-imagegen-cli`.

Audited `main` at `a739870aa9d600cfd0c382b6c06f38d0b1f5108b`, last source commit June 9, 2026, package version `0.1.0`. Installed Codex is `0.154.0`, also the [latest published stable release](https://github.com/openai/codex/releases/tag/rust-v0.154.0) when checked. Implementation files and the original lockfile were left unchanged. This directory contains audit tests and evidence only.

## Test results

| Check | Result |
|---|---|
| `uv lock --check` and frozen installation | Pass |
| Original tests on Python 3.10.21 | 23 passed |
| Original tests on Python 3.12.3 | 23 passed |
| Original tests on Python 3.14.7 | 23 passed |
| Locked Ruff 0.15.15 | Pass on all three runtimes |
| Added positive controls | 6 passed: SSE transport, numbered outputs, overwrite protection, batch continuation/fail-fast, auth-free dry run |
| Added regression probes | 14 failed, reproducing the behaviors described below |
| Source/wheel builds | Pass on complete Python runtimes |
| Locked Twine checks on freshly built artifacts | **Fail: metadata version 2.5 is rejected** |
| Wheel installed in a separate environment | Pass; both command entry points and dry run work |
| Dependency scan, original lock | Known advisories in Pillow and development-only Cryptography |
| Refreshed dependencies in separate copy | 23 tests pass, artifact checks pass, no known dependency advisories; new Ruff reports 38 lint findings |

The combined run contains **29 passes and 14 failures**. The failures are audit probes, not failures in the original 23-test suite. See [full output](evidence/pytest.txt), [JUnit results](evidence/pytest.xml), [regression probes](../tests/test_regressions.py), and [positive controls](../tests/test_controls.py). Audit probes are outside the repository's default `testpaths`, so a plain `uv run pytest` continues to run its original suite.

### Live backend tests

Tests used existing ChatGPT access credentials with token refresh disabled. Temporary credential files were deleted after use. No real login refresh was performed.

| Request | Result |
|---|---|
| Existing CLI, configured `gpt-6-astra`, blue circle, requested 1024×1024 | Succeeded in 18.9s; PNG **1254×1254** |
| Existing CLI, explicit fallback `gpt-5.5`, green triangle, requested 1024×1024 | Succeeded in 17.2s; PNG **1347×1167** |
| Existing CLI, edit blue circle to red, WebP output | Succeeded in 20.6s; valid WebP **1254×1254**, visually correct |
| Existing CLI, explicit transparent background | **HTTP 400:** `Transparent background is not supported for this model.` |
| Current Codex native generation endpoint, `gpt-image-2`, requested 1024×1024 | Succeeded in 17.1s; JSON response and decoded PNG both say **1254×1254** |

Generated/edit previews were visually inspected. Samples are in [output/audit](../output/audit); [image measurements](evidence/live-images.json), [transparent-request evidence](evidence/request-event-summary.json), and [native endpoint evidence](evidence/native-request-summary.json) contain no credentials or base64 image bodies.

**Scope:** one account on Linux; these tests establish present access here, not access for every subscription. Live multi-image batch/style-transfer, every quality/size combination, native edits, macOS/Windows keyrings, and real token-refresh/concurrency were not exercised. Offline tests cover multiple outputs, style-reference request construction and batch behavior. No server, browser or watcher was started.

## Priority 1 — Fix shared Codex login corruption on refresh

Location: [`_refresh_auth`, cli.py:428](../codex_imagegen_cli/cli.py#L428), particularly the assignment to `tokens["id_token"]`.

When OAuth returns a new ID token, the CLI stores a decoded dictionary in `auth.json`. Current Codex requires that field to remain the raw JWT **string**. This can break ordinary Codex authentication after imagegen refreshes its shared credentials.

Reproduced against the actual installed Codex with synthetic credentials in an isolated temporary home:

```text
Before imagegen refresh: codex login status -> exit 0, Logged in using ChatGPT
After imagegen refresh:  codex login status -> exit 1
Error checking login status: invalid type: map, expected a string at line 4 column 16
```

**Update:** persist the original returned JWT string; decode claims only in memory. Add a compatibility test that loads the resulting file with current Codex. The existing refresh test does not return `id_token`, and its auth fixture already uses the wrong dictionary representation, so it misses this defect. Also improve credential persistence with restricted permissions and atomic writes, and consider concurrent refresh coordination; those latter failure modes were inspected but not reproduced.

Upstream contract: [Codex 0.154.0 token serialization](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/login/src/token_data.rs#L163).

## Priority 1 — Refresh dependencies and repair release validation

Locations: [pyproject.toml](../pyproject.toml), [uv.lock](../uv.lock).

The locked runtime dependency **Pillow 12.2.0** has **13 distinct advisory groups**, fixed by 12.3.0. One relevant example affects opening crafted image files by filename, a path this CLI uses: [Pillow McIdas memory-read advisory](https://github.com/python-pillow/Pillow/security/advisories/GHSA-62p4-gmf7-7g93). Not every flagged Pillow issue is reachable through this CLI, and exploitability was not tested.

The development dependency chain also includes **Cryptography 48.0.0**, with **4 distinct advisory groups**. It is pulled in by publishing/keyring tooling, not the CLI's runtime HTTPS implementation. [Maintainer advisory for bundled OpenSSL](https://github.com/pyca/cryptography/security/advisories/GHSA-537c-gmf6-5ccf). The scanner emitted 32 raw records; duplicates reduce this to **17 distinct advisories across two packages**. See [deduplicated evidence](evidence/vulnerability-summary.json).

Fresh isolated builds resolve the unbounded build requirement to **Hatchling 1.32.0**, which emits `Metadata-Version: 2.5`. Locked **Twine 6.2.0** rejects both the wheel and source archive. Updating the development toolchain, including **Twine 7.0.0 / Packaging 26.3**, makes both checks pass. A passing historic workflow does not prove a fresh build will pass today because build isolation resolves new Hatchling releases independently of `uv.lock`.

Main tested updates:

| Package | Locked | Tested current |
|---|---|---|
| Pillow | 12.2.0 | 12.3.0 |
| Cryptography, dev only | 48.0.0 | 50.0.1 |
| Twine | 6.2.0 | 7.0.0 |
| Packaging | 26.2 | 26.3 |
| Build | 1.5.0 | 1.6.1 |
| Pytest | 9.0.3 | 9.1.1 |
| Ruff | 0.15.15 | 0.16.7 |

**Update:** refresh the lockfile, raise the runtime Pillow minimum to a patched version, and establish compatible minimum publishing-tool versions. In `/tmp/codex-imagegen-upgrade-check`, a full lock refresh passed all 23 tests on Python 3.10, built valid artifacts and produced [zero known dependency findings](evidence/upgraded-scan-summary.json). New Ruff defaults produce 38 import/type-modernization findings; clean them up or explicitly retain the existing `E4,E7,E9,F` rule set, which was tested and passes. Do not treat a blind lock refresh as fully green CI.

Seven [open dependency PRs](https://github.com/jdmnk/codex-imagegen-cli/pulls) were found: #9 checkout v7, #10 pytest, #13 Pillow, #14 Hatchling requirement, #15 Ruff, #16 Build, #17 setup-python v7. Some target versions are already behind today's releases. Review/update them together with a fresh CI run.

## Priority 1 — Make size/background behavior accurate

Locations: [`_responses_payload`](../codex_imagegen_cli/cli.py#L518), [`_write_response_image`](../codex_imagegen_cli/cli.py#L565), [README backend/options](../README.md#backend-path).

The CLI sends explicit sizes but silently saves differently sized images and reports success. Two independent CLI generations reproduced this. The native endpoint also returned different dimensions, so **switching endpoints alone does not fix exact-size behavior**.

Transparent background is accepted by the parser and advertised without a model caveat, but the tested backend rejects it with a user-error response.

**Update:** verify decoded image dimensions, report the actual size, and provide a clear mismatch policy. If exact-size postprocessing is offered, make resizing/cropping explicit. Make option validation/documentation reflect the selected backend and image model. Preserve useful backend error details. Do not claim all quality flags are ignored: the native response reported the requested `low` quality; image quality itself was not benchmarked.

## Priority 2 — Align the backend with current Codex

The project forces an `image_generation` tool through `/backend-api/codex/responses`, selected by a reasoning-model name. That path still works here.

Current Codex 0.154.0's native image extension instead uses:

```text
POST /backend-api/codex/images/generations
POST /backend-api/codex/images/edits
model: gpt-image-2
```

Its responses are JSON containing `data[].b64_json`, image dimensions and quality. The native generation request was successfully tested in this audit. Native edit behavior was inspected in source, not live-tested. Sources: [current backend](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/image-generation/src/backend.rs), [endpoint/schema implementation](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/codex-api/src/endpoint/images.rs), [tool model and five-image limit](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/image-generation/src/tool.rs#L58).

**Update:** evaluate making this native path primary, preserving the existing path deliberately if needed, and separate image-model selection from reasoning-model selection. It removes an unnecessary reasoning-model dependency and the need to extract image output from SSE. Keep the private-backend compatibility caveat.

Official documentation still describes Codex's built-in model as `gpt-image-2`; the separate public Images API now documents GPT Image 2.5. API availability does **not** establish subscription-backend availability. Do not blindly substitute a public API model. [Codex image-generation docs](https://learn.chatgpt.com/docs/image-generation), [public API image guide](https://developers.openai.com/api/docs/guides/image-generation).

The existing five-reference-image limit still matches current Codex. The `gpt-5.5` fallback worked in this audit, although [issue #18](https://github.com/jdmnk/codex-imagegen-cli/issues/18) reports an account-specific `model_not_found`. It is not proven globally broken.

## Priority 2 — Correct configuration, batch and image handling

| Confirmed behavior | Location | Update |
|---|---|---|
| Regex model lookup reads a model from an inactive TOML profile | `_default_model`, line 502 | Parse TOML and respect top-level/active-profile precedence; use a Python 3.10-compatible parser strategy |
| `"images": null` is accepted in batch validation, then crashes with `TypeError` | `_load_jobs_jsonl`, line 154; `_cmd_batch`, line 985 | Store normalized validated jobs |
| `"prompt": null` becomes the literal prompt `"None"` | Same | Require actual non-empty strings |
| Invalid mode aborts all later batch jobs even without `--fail-fast` | `_cmd_batch`, line 985 | Validate before starting the batch, or put per-job validation inside the existing failure-handling block |
| EXIF-rotated input loses its intended orientation during WebP compaction | `_encoded_input_image`, line 474 | Apply EXIF transpose before resizing and encoding |
| Invalid base64 `!!!!` creates an empty PNG file and reports success | `_write_response_image`, line 565 | Strict base64 decoding plus image validation before replacing output |

Codex can now store credentials in file, keyring, automatic or ephemeral modes. This CLI only reads files. That is an explicit limitation to document and diagnose clearly; do not imply that any successful `codex login` guarantees a readable `auth.json`. [Official authentication documentation](https://learn.chatgpt.com/docs/auth#credential-storage).

## Priority 2/3 — Harden transport and user-facing errors

If the Responses backend is retained, the audit reproduces:

- **CRLF SSE separators fail:** `_post_sse` only splits on `\n\n` and merges multiple CRLF-delimited events into invalid JSON. LF-delimited events pass the positive control.
- **Terminal-response fallback is missing:** a result in `response.completed.response.output` is ignored when no per-item result was emitted. Current live streams worked; this is a robustness gap, not a live outage observed today.
- **Incomplete HTTP reads leak a traceback:** `http.client.IncompleteRead` is not wrapped as `TransportError`.
- **HTTP 429 is not retried:** only streamed `response.failed` rate-limit events are handled.
- **401 followed by a streamed rate limit bypasses retries:** the post-refresh request executes inside the exception handler, outside the common retry loop.
- **Bad timeout values and filesystem input errors leak through:** negative timeouts pass validation, and passing a directory as `--prompt-file` raises `IsADirectoryError` instead of a concise CLI error.

Update the stream parser, unify bounded retry handling, retain `Retry-After` information, validate positive finite timeouts and normalize expected filesystem/HTTP errors. Avoid automatic retries after ambiguous successful generation unless duplicate image usage is accounted for.

## Reproduction and suggested update order

```bash
cd /home/jure/codex-imagegen-cli
uv sync --frozen --dev
uv run pytest                         # original 23 tests
uv run pytest audit/test_controls.py # six passing controls
uv run pytest audit/test_regressions.py  # 14 expected failures on audited commit
uv run ruff check .
uv run python -m build
uv run python -m twine check dist/*   # currently fails on metadata 2.5
```

On this VPS, `uv` was absent and system Python lacked `ensurepip`. Audit tooling was installed separately; use `/tmp/codex-imagegen-audit-tools/uv` here. The system-Python build works with `PATH=/tmp/codex-imagegen-audit-tools:$PATH` and `uv run python -m build --installer uv`. Complete Python 3.10/3.14 runtimes also build with the documented command. Missing `ensurepip` is an environment issue, separate from the reproducible Twine incompatibility.

Recommended sequence:

1. Fix auth serialization; add the real Codex compatibility regression.
2. Update Pillow and publishing dependencies; resolve new Ruff findings/configuration and rerun CI.
3. Correct size/background promises and validate actual output.
4. Evaluate the current native image endpoints and clarify model selection.
5. Fix batch normalization, TOML handling, EXIF orientation, output validation and remaining error paths.
6. Promote the audit probes into the normal suite as fixes land; add an opt-in live compatibility check and update the changelog/version for a release.

No commits, pushes, dependency merges, deployments, or implementation fixes were performed.
