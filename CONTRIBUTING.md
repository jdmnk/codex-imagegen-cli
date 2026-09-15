# Contributing

Contributions are welcome when they improve the CLI, Codex-auth image backend behavior, tests, docs, or launch packaging.

## Development Setup

```bash
uv lock --check
uv sync --frozen --dev
uv run pytest
uv run ruff check .
```

## Pull Requests

Keep pull requests focused and include a short note about the behavior changed. Add or update tests when changing CLI parsing, auth handling, request payloads, output writes, or packaging metadata.

Before opening a PR, run:

```bash
uv lock --check
uv run pytest
uv run ruff check .
uv run python -m build --installer uv
uv run python -m twine check dist/*
```

## Project Boundaries

`codex-imagegen-cli` tracks the private Codex ChatGPT image-generation paths from openai/codex. It should not require `OPENAI_API_KEY`, shell out to `codex exec`, print auth tokens, or depend on a locally installed Codex skill.

## Compatibility coverage

Use Python 3.10, 3.12 and 3.14 for release checks. Normal tests must never read real credentials or perform network requests. Codex login compatibility tests use synthetic tokens in a temporary Codex home.

Run `CODEX_IMAGEGEN_LIVE_TEST=1 uv run pytest -m live -v` only when you intend to spend account usage on three image requests. The check disables credential refresh and backend retries. No server is needed.

When updating dependencies, run `uv lock --upgrade`, the tests, Ruff lint/format checks, and both artifact checks. The build-system Hatchling range is deliberate: update it alongside a compatible Twine/Packaging toolchain. Review published advisories for locked runtime and development dependencies.

`--model`/`--reasoning-model` controls only the Responses reasoning model. Do not expose an image-model selector without evidence that the backend honors it and reports model identity. Do not silently retry a failed image through a different backend or resize an output to hide a backend mismatch.
