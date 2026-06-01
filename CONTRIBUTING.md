# Contributing

Contributions are welcome when they improve the CLI, Codex-auth image backend behavior, tests, docs, or launch packaging.

## Development Setup

```bash
uv sync --dev
uv run pytest
uv run ruff check .
```

## Pull Requests

Keep pull requests focused and include a short note about the behavior changed. Add or update tests when changing CLI parsing, auth handling, request payloads, output writes, or packaging metadata.

Before opening a PR, run:

```bash
uv run pytest
uv run ruff check .
uv run python -m build
uv run python -m twine check dist/*
```

## Project Boundaries

`codex-imagegen-cli` mirrors the stable Codex ChatGPT `/responses` hosted `image_generation` path from openai/codex. It should not require `OPENAI_API_KEY`, shell out to `codex exec`, print auth tokens, or depend on a locally installed Codex skill.
