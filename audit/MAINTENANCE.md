# Maintenance completed — 0.2.0

Implemented the recommendations from the [September 15 audit](REPORT.md) in the local checkout. The historical audit describes `a739870`; its defects and failing-test results are not the status of this updated tree.

## Changes

- **Authentication:** retain raw ID-token JWT strings; validate refresh responses; use atomic owner-only credential writes; serialize refreshes between imagegen processes; reuse credentials refreshed by another process; refuse observed external auth changes, including logout. Refresh failures do not echo OAuth response bodies.
- **Dependencies/releases:** update all locked dependencies, require Pillow 12.3.0 or newer, update Build/Twine/Ruff/Pytest, constrain Hatchling to the tested 1.32 minor series, and update GitHub checkout/setup-python actions. Modernize imports/type annotations and make Ruff rules explicit.
- **Backend:** default to the current native `images/generations` and `images/edits` paths. Keep `gpt-image-2` as a fixed request-format compatibility field, without claiming it identifies the actual output model. Keep the legacy Responses path available explicitly, with reasoning-model selection only. The backend controls the image model.
- **Image options/output:** accept valid custom dimensions; report decoded dimensions; warn on size mismatch or reject with `--size-policy error`; reject known-unsupported transparency locally. Strictly decode/validate PNG results before atomic PNG/WebP publication, preserve existing files on failures and print successful paths immediately.
- **Input/configuration/batches:** apply EXIF orientation before resizing; reject corrupt images; parse TOML profiles correctly; normalize nullable image lists and require string prompts; handle per-job validation failures consistently; keep batch outputs under their output directory.
- **Transport/errors:** parse CRLF and split UTF-8 streams; accept terminal response results; close streams promptly; wrap incomplete reads; unify 401/429 and streamed rate-limit handling; bound Retry-After/backoff; reject non-positive/non-finite timeouts and report expected filesystem errors cleanly.
- **Tests/docs:** promote all audit probes into the normal suite, isolate offline tests from real credentials/configuration, add concurrency and native-backend tests, add opt-in live checks, update the README/migration instructions/changelog, and bump the package to 0.2.0.

## Validation

| Check | Result |
|---|---|
| Python 3.10.21 | 98 passed, 2 live tests skipped |
| Python 3.12.3 | 98 passed, 2 live tests skipped |
| Python 3.14.7 | 98 passed, 2 live tests skipped |
| Installed Codex 0.154.0 reads refreshed synthetic auth | Pass |
| Concurrent imagegen refresh / external auth change / failed write checks | Pass |
| Opt-in live compatibility | 2 tests passed, making 3 image requests |
| Native generation / edit to WebP | Pass |
| Legacy Responses generation | Pass |
| Ruff lint and formatting | Pass |
| Lockfile consistency | Pass |
| Clean wheel and source archive builds / Twine metadata validation | Pass |
| Isolated Python 3.10 wheel install and both command entry points | Pass |
| Locked dependency audit | No known vulnerabilities reported |

The live tests took approximately 52 seconds. All three generated files were 1254×1254 despite requests for 1024×1024; the new mismatch warning behaved correctly. See [live test log](evidence/maintenance-live-tests.txt), [image metadata](evidence/maintenance-live-images.json), [dependency scan summary](evidence/maintenance-dependency-scan.json), and generated samples in [output/maintenance](../output/maintenance).

The GitHub workflow configuration was updated; hosted CI has not run because these changes have not been pushed. Local checks exercise the configured Python versions and build/lint/test commands.

## Using the update

```bash
uv sync --frozen --dev
uv run codex-imagegen generate --prompt "A blue circle on white" --out output/circle.png
```

Use `--size-policy error` when a size mismatch must fail without writing output. Existing commands specifying a reasoning `--model` must add `--backend responses`. The backend controls the image model; the CLI does not expose an image-model selector.

```bash
uv run codex-imagegen generate --backend responses --model gpt-5.5 \
  --prompt "A blue circle on white" --out output/legacy-circle.png
```

To refresh a local user-command installation:

```bash
uv tool install --reinstall -e .
```

## Remaining platform limitations

These are documented constraints, rather than outstanding audit fixes:

- Backend access and exact image options remain controlled by an undocumented service and account entitlements. The tool does not silently resize or switch backends.
- Keyring/ephemeral/API-key/enterprise-token authentication is not implemented; file-based ChatGPT auth is required.
- The imagegen refresh lock cannot fully coordinate with Codex or other external writers that do not share it. Observable changes are checked before publication.
- The live check disables real credential refresh. Compatibility of the stored format is tested against actual Codex using fake tokens; real OAuth refresh was not exercised.
- Windows/macOS-specific behavior was not run on this Linux VPS. CI presently covers Linux with three Python versions.
- No hosted PRs were merged, no release was published, and no server/browser/watcher was started.

## Follow-up: remove unverified image-model selection

Subscription-backend probes with `gpt-image-2.5-sunburst`, `gpt-image-2.5-flare`, and a deliberately nonexistent model name all returned images without reporting model identity. These results cannot establish which model ran or whether the requested name was honored. Removed the experimental `--image-model` flag and `CODEX_IMAGEGEN_IMAGE_MODEL` override; retained the default native request format. Clarified reasoning-model help and transparency errors, and added regressions for rejected flags and an ineffective legacy environment override. No public API backend was added.

Follow-up validation: 98 offline tests passed on Python 3.10, 3.12, and 3.14, with 2 live cases skipped on each. Live generation was not repeated for this removal because the default request payload is unchanged.
