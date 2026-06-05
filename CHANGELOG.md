# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses semantic versioning before its first stable release.

## [0.1.0] - 2026-06-01

### Added

- Initial `codex-imagegen` and `codex-imagegen-cli` commands.
- `generate`, `edit`, and `batch` workflows that call the direct Codex hosted image-generation backend by default.
- Codex `auth.json` loading, ChatGPT token refresh, Codex-style default headers, and backend auth headers.
- Exact direct hosted image tool parameters for `size`, `quality`, and `background`.
- Bounded retries for streamed hosted-image rate-limit failures, including per-minute input-image edit limits.
- Dry-run mode for inspecting the backend request shape without reading auth or contacting Codex.
- Source distribution, wheel build, lockfile check, pytest coverage, and README quickstart.
