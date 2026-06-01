# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses semantic versioning before its first stable release.

## [0.1.0] - 2026-06-01

### Added

- Initial `codex-imagegen` and `codex-imagegen-cli` commands.
- `generate`, `edit`, and `batch` workflows that call the stable Codex `/responses` image-generation path.
- Codex `auth.json` loading, ChatGPT token refresh, Codex-style default headers, and backend auth headers.
- Experimental `--backend direct` mode for the under-development `/images/generations` and `/images/edits` endpoint.
- Dry-run mode for inspecting the backend request shape without reading auth or contacting Codex.
- Source distribution, wheel build, lockfile check, pytest coverage, and README quickstart.
