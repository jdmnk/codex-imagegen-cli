# codex-imagegen-cli

Scriptable image generation for Codex users. The CLI uses your existing Codex ChatGPT login, calls the same hosted `image_generation` tool path used by openai/codex, and writes the returned image bytes to predictable local paths.

It does not require `OPENAI_API_KEY`, a locally installed Codex `imagegen` skill, or `codex exec`.

```text
$ codex-imagegen --version
codex-imagegen 0.1.0

$ codex-imagegen generate --prompt "A studio product photo of a ceramic mug" --out output/mug.png --dry-run
{
  "method": "POST",
  "url": "https://chatgpt.com/backend-api/codex/responses",
  "headers": {
    "Authorization": "Bearer <redacted>"
  },
  "payload": {
    "model": "gpt-5.5",
    "tools": [{"type": "image_generation", "output_format": "png"}],
    "tool_choice": "auto",
    "stream": true
  },
  "outputs": ["output/mug.png"]
}
```

## Requirements

- Python 3.9+
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
  --prompt "A studio product photo of a ceramic mug" \
  --out output/imagegen/mug.png \
  --dry-run
```

To install it as a user command from the checkout:

```bash
uv tool install -e .
```

PyPI publishing is not active for `0.1.0`; the supported first-run path is source checkout or a GitHub release artifact.

## Generate

```bash
codex-imagegen generate \
  --prompt "A clean product photo of a ceramic coffee mug on a white studio background" \
  --out output/imagegen/mug.png
```

Request multiple images:

```bash
codex-imagegen generate \
  --prompt "Three logo moodboard directions for a calm developer tool" \
  --out output/imagegen/moodboard.png \
  --n 3
```

Multiple outputs are written as `moodboard-1.png`, `moodboard-2.png`, and so on.

## Edit

```bash
codex-imagegen edit \
  --image input/product.png \
  --prompt "Replace only the background with a warm sunset gradient; keep the product unchanged" \
  --out output/imagegen/product-sunset.png
```

Edit accepts one to five `--image` inputs, matching the current Codex image tool limit.

## Batch

Batch mode runs JSONL jobs sequentially.

```bash
mkdir -p tmp/imagegen
cat > tmp/imagegen/jobs.jsonl <<'EOF'
{"prompt":"A compact shuttle parked in a cavernous hangar","out":"shuttle.png"}
{"prompt":"A product photo with a new background","images":["input/product.png"],"mode":"edit","out":"product.png"}
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
- `--base-url URL`: override the Codex backend base URL
- `--backend responses|direct`: choose the stable hosted-tool flow or the experimental direct image endpoint
- `--image-model MODEL`: image model preference; exact control is only available with `--backend direct`
- `--background auto|transparent|opaque`: background preference; exact control is only available with `--backend direct`
- `--quality auto|low|medium|high`: quality preference; exact control is only available with `--backend direct`
- `--size auto|1024x1024`: size preference; exact control is only available with `--backend direct`
- `--n COUNT`: request multiple output images; the stable backend runs one streamed response per image
- `--dry-run`: print the request shape without reading auth or contacting the backend

## How It Works

The stable openai/codex image flow uses the normal Codex `/responses` endpoint with a hosted tool spec:

```json
{"type": "image_generation", "output_format": "png"}
```

The relevant source paths are:

- `codex-rs/core/src/tools/spec_plan.rs`
- `codex-rs/core/src/tools/hosted_spec.rs`
- `codex-rs/codex-api/src/endpoint/responses.rs`
- `codex-rs/model-provider-info/src/lib.rs`
- `codex-rs/model-provider/src/bearer_auth_provider.rs`
- `codex-rs/login/src/auth/default_client.rs`
- `codex-rs/login/src/auth/manager.rs`

For ChatGPT-authenticated Codex sessions, that provider uses:

- base URL: `https://chatgpt.com/backend-api/codex`
- stable path: `/responses`
- hosted tool: `image_generation`
- bearer token: the ChatGPT access token from Codex auth
- account header: `ChatGPT-Account-ID` when present
- Codex default headers: `originator` and a Codex-shaped `User-Agent`

`codex-imagegen-cli` mirrors that stable path directly. It reads Codex auth locally, refreshes an expired ChatGPT token through the same OAuth refresh client ID used by Codex, streams the `/responses` request, finds the `image_generation_call` result, and decodes the base64 image into files.

There is also an under-development openai/codex extension that calls `/images/generations` and `/images/edits` directly. In current real-world testing that endpoint returned JSON 404 for this account/client path, so this CLI keeps it behind `--backend direct` instead of using it by default.

## Development

```bash
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
