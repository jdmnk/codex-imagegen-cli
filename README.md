<div align="center">

# 🎨 codex-imagegen-cli

**Generate and edit images from your terminal using [Codex](https://github.com/openai/codex)'s built-in image tools — no API key needed.**

Codex (OpenAI's CLI coding agent) has image generation capabilities built in. This CLI exposes them as a standalone scriptable tool, reusing your existing Codex ChatGPT login. Supports `generate`, `edit`, and `batch` modes out of the box.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Status](https://img.shields.io/badge/status-alpha-orange)](CHANGELOG.md)
[![uv](https://img.shields.io/badge/uv-ready-purple?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZmlsbD0id2hpdGUiIGQ9Ik0xMiAyQzYuNDggMiAyIDYuNDggMiAxMnM0LjQ4IDEwIDEwIDEwIDEwLTQuNDggMTAtMTBTMTcuNTIgMiAxMiAyem0tMSAxNHYtNEg3bDUtOHY0aDRsLTUgOHoiLz48L3N2Zz4=)](https://github.com/astral-sh/uv)

</div>

---

```text
$ codex-imagegen generate \
    --prompt "A 32x32 retro RPG pixel icon of an iron helmet, no text" \
    --out output/icons/iron-helmet.png
```

Uses your existing **Codex ChatGPT login** — no `OPENAI_API_KEY` required.

## Why this exists

[Codex](https://github.com/openai/codex) (OpenAI's CLI coding agent) has image generation built in — but it's prompt-driven and agentic, not a scriptable command. There is no `codex image generate` subcommand, no `--size` or `--quality` flags, and the documented automation primitive (`codex exec`) emits assistant text, not image bytes.

This CLI fills that gap by calling Codex's backend directly, using your existing ChatGPT subscription auth. No `OPENAI_API_KEY` neede, as it's not API billing — just the same image generation Codex uses internally, exposed as a proper scriptable tool with explicit control over **model**, **size**, **quality**, **background**, and **output** for `generate`, `edit`, and `batch` workflows.

## Requirements

- Python 3.10+
- `uv`
- Codex logged in with ChatGPT auth

Run Codex login once if needed:

```bash
codex login
```

Choose the ChatGPT login flow. The CLI reads `$CODEX_HOME/auth.json` or `~/.codex/auth.json`, refreshes tokens when needed, and never prints token values.

## First Run

Use the CLI from a local checkout:

```bash
git clone https://github.com/jdmnk/codex-imagegen-cli.git
cd codex-imagegen-cli
uv sync --dev
```

Verify the command:

```bash
uv run codex-imagegen --help
uv run codex-imagegen --version
```

Run a dry run:

```bash
uv run codex-imagegen generate \
  --prompt "A 32x32 retro RPG pixel icon of an iron helmet, no text" \
  --out output/icons/iron-helmet.png \
  --dry-run
```

To install it as a user command from the checkout:

```bash
uv tool install -e .
```

## Generate

```bash
codex-imagegen generate \
  --prompt "A 32x32 retro RPG pixel icon of a steel longsword with a blue grip, no text" \
  --out output/icons/steel-longsword.png
```

Request multiple images:

```bash
codex-imagegen generate \
  --prompt "A 32x32 retro RPG pixel icon of a bronze shield with a red gem, no text" \
  --out output/icons/bronze-shield.png \
  --n 3
```

Multiple outputs are written as `bronze-shield-1.png`, `bronze-shield-2.png`, and so on.

## Edit

```bash
codex-imagegen edit \
  --image input/icons/iron-sword.png \
  --prompt "Turn this into a fire-enchanted sword icon while keeping the same retro RPG pixel-art style, silhouette, and no text" \
  --out output/icons/fire-sword.png
```

Edit accepts one to five `--image` inputs, matching the current Codex image tool limit.

## Batch

Batch mode runs JSONL jobs sequentially.

```bash
mkdir -p tmp/imagegen
cat > tmp/imagegen/jobs.jsonl <<'EOF'
{"prompt":"A 32x32 retro RPG pixel icon of an iron helmet, no text","out":"iron-helmet.png"}
{"prompt":"A 32x32 retro RPG pixel icon of leather boots with tiny silver buckles, no text","out":"leather-boots.png"}
{"prompt":"Make this shield look ice-enchanted while keeping the same retro RPG pixel-art style","images":["input/icons/wooden-shield.png"],"mode":"edit","out":"ice-shield.png"}
EOF

codex-imagegen batch \
  --input tmp/imagegen/jobs.jsonl \
  --out-dir output/imagegen/batch
```

Each line can be either a JSON string prompt or an object with:

- `prompt`: required text prompt
- `out`: optional output filename, resolved under `--out-dir`
- `images`: optional list of image paths for edit jobs
- `mode`: optional `generate` or `edit`; defaults to `edit` when images are present

## Useful Flags

- `--prompt-file prompt.txt`: read the prompt from a file
- `--force`: allow replacing existing output files
- `--cd PATH`: base directory for resolving relative paths
- `--auth-file PATH`: read a specific Codex `auth.json`
- `--codex-home PATH`: read auth from another Codex home directory
- `--model MODEL`: override the Codex reasoning model used to run the Codex turn. This is separate from `--image-model`.
- `--base-url URL`: override the Codex backend URL for development
- `--backend responses|direct`: choose the Codex request path; keep the default unless debugging compatibility
- `--image-model MODEL`: image model preference; with the default backend this is added to the prompt as guidance
- `--background auto|transparent|opaque`: background preference; with the default backend this is added to the prompt as guidance
- `--quality auto|low|medium|high`: quality preference; with the default backend this is added to the prompt as guidance
- `--size auto|1024x1024`: size preference; with the default backend this is added to the prompt as guidance
- `--n COUNT`: request multiple output images; the stable backend runs one streamed response per image
- `--dry-run`: print the request shape without reading auth or contacting the backend

## How It Works

`codex-imagegen-cli` reuses your normal Codex login. On each run it:

- reads Codex auth from `$CODEX_HOME/auth.json` or `~/.codex/auth.json`
- refreshes the ChatGPT access token when needed
- sends a Codex image-generation turn with your prompt and optional input images
- streams the result and writes the final PNG to `--out`

Dry runs only print the planned request and output paths; they do not read auth or contact Codex.

## Important Note

This project relies on Codex's current authenticated app behavior, not a public OpenAI Images API contract.

A few things worth knowing:

- **Subscription only.** The built-in Codex image generation path is gated to ChatGPT auth (Plus, Pro, Business, Edu, Enterprise). It is not available on the Free plan and does not work with an `OPENAI_API_KEY` session — that key routes to the Images API instead, under separate billing.
- **Usage limits apply.** Image generations consume your Codex account's included limits, roughly 3–5× faster than a comparable non-image turn. OpenAI documents approximate credit costs per image size (e.g. ~5–6 credits for 1024×1024).
- **No stable API contract.** Codex internals and account policies can change. The CLI may stop working or behave differently after a Codex update, and OpenAI has not documented this as a supported automation surface.

We will try to keep this project updated as long as this usage remains possible and allowed.

## Development

```bash
uv lock --check
uv sync --dev
uv run pytest
uv run ruff check .
```

Build and validate release artifacts:

```bash
uv run python -m build
uv run python -m twine check dist/*
```

## Update And Uninstall

Update a source install from the checkout:

```bash
git pull
uv tool install --reinstall -e .
```

Uninstall the CLI:

```bash
uv tool uninstall codex-imagegen-cli
```

## Security

Report security issues privately through GitHub Security Advisories when available. See [SECURITY.md](SECURITY.md) for scope and response expectations.

## Project Status

`0.1.0` is an alpha source-install release. The CLI command shape, JSONL batch format, and Codex backend behavior may change before a stable release.

See [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md), and [LICENSE](LICENSE).
