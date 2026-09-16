# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses semantic versioning before its first stable release.

## [0.2.0] - 2026-09-15

### Changed

- Native Codex image generation/edit endpoints are now the default, following the Codex request format. The backend controls the actual image model.
- The original streamed backend remains available with `--backend responses`. Reasoning `--model`/`--profile` apply only to that backend and do not select the image generator.
- Remove the experimental `--image-model` flag and `CODEX_IMAGEGEN_IMAGE_MODEL` override: live requests also accepted a nonexistent model name without returning model identity, so model selection could not be verified.
- Explicit dimensions are validated against image-size constraints. Actual dimensions are reported; `--size-policy warn|error` controls mismatches without resizing.
- Unsupported transparency on the default path is rejected before contacting the backend.
- Refresh Pillow, the publishing toolchain and all locked dependencies; constrain Hatchling to the tested minor release and update CI actions/Python coverage.

### Fixed

- Preserve raw JWT ID tokens during refresh; write credentials atomically with restricted POSIX permissions, coordinate imagegen refreshes and detect external credential changes.
- Validate and atomically save complete PNG/WebP outputs; protect existing files on failures and publish each successful path immediately.
- Apply EXIF orientation before edit-input compaction and reject corrupt inputs.
- Parse TOML model/profile settings correctly; explain file-only credential support.
- Normalize batch inputs, reject non-string prompts and escaped outputs, and honor continuation for per-job validation failures.
- Handle CRLF and split UTF-8 in SSE, terminal response results, truncated transport reads, HTTP 429/Retry-After and rate limits after authentication refresh.
- Validate positive finite timeouts and present expected filesystem failures as CLI errors.

### Added

- Promote audit regressions to the normal suite and add native-backend, concurrency, persistence, output and retry coverage.
- Add opt-in live generation/edit compatibility tests that never refresh shared credentials.

## [0.1.0] - 2026-06-01

### Added

- Initial `codex-imagegen` and `codex-imagegen-cli` commands.
- `generate`, `edit`, and `batch` workflows that call the direct Codex hosted image-generation backend by default.
- Codex `auth.json` loading, ChatGPT token refresh, Codex-style default headers, and backend auth headers.
- Exact direct hosted image tool parameters for `size`, `quality`, and `background`.
- PNG and WebP output support, including extension inference and configurable `--webp-quality`.
- Local WebP compaction for edit input images before upload.
- Bounded retries for streamed hosted-image rate-limit failures, including per-minute input-image edit limits.
- Dry-run mode for inspecting the backend request shape without reading auth or contacting Codex.
- Source distribution, wheel build, lockfile check, pytest coverage, and README quickstart.
