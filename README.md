# codex-imagegen-cli

Image generation CLI using Codex's integrated image gen abilities. The CLI uses your existing Codex ChatGPT login and does not require an OpenAI API key.

```text
$ codex-imagegen generate --prompt "A 32x32 retro RPG pixel icon of an iron helmet, no text" --out output/icons/iron-helmet.png --dry-run
```

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
  --prompt "Three 32x32 retro RPG equipment icons: a bronze shield, a silver ring, and a healing potion, no text" \
  --out output/icons/equipment-set.png \
  --n 3
```

Multiple outputs are written as `equipment-set-1.png`, `equipment-set-2.png`, and so on.

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
- `--model MODEL`: override the Codex reasoning model; defaults to your Codex config
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

This project relies on Codex's current authenticated app behavior, not a public OpenAI Images API contract. Codex internals and account policies can change, so the CLI may stop working or behave differently after Codex updates.

We will try to keep this project updated as long as this usage remains possible and allowed, but Codex could restrict or prevent this access in the future. Live generation uses your Codex account and counts against whatever Codex limits apply to that account.

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
