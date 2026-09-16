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

This CLI fills that gap by calling Codex's backend directly, using your existing ChatGPT subscription auth. No `OPENAI_API_KEY` needed, as it's not API billing - just the same image generation Codex uses internally, exposed as a scriptable tool for `generate`, `edit`, and `batch` workflows with clear options for **reasoning model** (Responses backend), **size**, **quality**, **background**, and **output**.

## Requirements

- Python 3.10+
- `uv`
- Codex logged in with file-based ChatGPT auth

Run Codex login once if needed:

```bash
codex login
```

Choose the ChatGPT login flow. The CLI reads `$CODEX_HOME/auth.json` or `~/.codex/auth.json`, refreshes tokens when needed, and never prints token values. Keyring-only and ephemeral credentials are not supported; if needed, set `cli_auth_credentials_store = "file"` in Codex `config.toml` before logging in.

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

Multiple outputs are written as `bronze-shield-1.png`, `bronze-shield-2.png`, and so on. Each saved path is printed immediately; if a later request fails, earlier outputs remain available.

Write WebP directly by using a `.webp` output path:

```bash
codex-imagegen generate \
  --prompt "A 32x32 retro RPG pixel icon of a bronze shield with a red gem, no text" \
  --out output/icons/bronze-shield.webp \
  --webp-quality 82
```

## Edit

```bash
codex-imagegen edit \
  --image input/icons/iron-sword.png \
  --prompt "Turn this into a fire-enchanted sword icon while keeping the same retro RPG pixel-art style, silhouette, and no text" \
  --out output/icons/fire-sword.png
```

Edit accepts one to five `--image` inputs, matching the current Codex image tool limit.

### Style Reference Edits

Use `--style-image` when you want to keep the idea/content from one image but render it in the style of another image.

```bash
codex-imagegen edit \
  --image input/profile.png \
  --style-image input/style-reference.png \
  --out output/profile-in-style.webp
```

In this mode, `--image` is the content image and `--style-image` is only the style reference. The CLI writes a clear edit instruction for Codex: preserve the subject, identity, composition, proportions, and important details from `--image`; borrow the medium, rendering approach, palette, lighting, texture, finish, and mood from `--style-image`; do not copy the style reference's subject or scene.

`--prompt` is optional with `--style-image`. If you include it, it is treated as extra guidance:

```bash
codex-imagegen edit \
  --image input/profile.png \
  --style-image input/editorial-lighting.png \
  --prompt "keep the background bright and professional" \
  --out output/profile-editorial.webp
```

The style image counts toward the same one-to-five edit image limit.

## Input And Output WebP

The CLI uses WebP in two different places:

- **Input WebP for edits.** When you run `edit`, local input images are corrected for EXIF orientation, resized if needed, and converted to WebP before they are sent to Codex. This makes large PNG/JPEG files much smaller in the request, which helps avoid broken uploads and reduces pressure on Codex's input-image quota. Your original file is not modified.
- **Output WebP for saved files.** Codex returns PNG image bytes. If `--out` ends in `.webp`, or you set `--output-format webp`, the CLI converts the returned PNG to WebP locally before saving it.

Example:

```bash
codex-imagegen edit \
  --image input/profile.png \
  --prompt "make it more polished" \
  --out output/profile.webp
```

In that command, `input/profile.png` is compacted to WebP for the request, and `output/profile.webp` is saved as WebP. Invalid input images are rejected locally. Output images are validated and saved atomically, preserving existing files if validation or conversion fails.

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

- `prompt`: required non-empty text prompt
- `out`: optional output filename, resolved under `--out-dir`; paths escaping that directory are rejected
- `images`: optional list of image paths for edit jobs; `null` is treated as an empty list
- `mode`: optional `generate` or `edit`; defaults to `edit` when images are present

Per-job validation or backend failures are reported and later jobs continue unless `--fail-fast` is set. Any failed job produces exit status 1.

## Useful Flags

- `--prompt-file prompt.txt`: read the prompt from a file
- `--force`: allow replacing existing output files
- `--cd PATH`: base directory for resolving relative paths
- `--auth-file PATH`: read a specific Codex `auth.json`
- `--codex-home PATH`: read auth from another Codex home directory
- `--backend native|responses`: choose the backend; default: `native`.
- `--model MODEL` / `--reasoning-model MODEL`: override the reasoning model with `--backend responses` only; does not select the image generator.
- `--profile NAME`: select a Codex reasoning-model profile with `--backend responses` only.
- `--base-url URL`: override the Codex backend URL for development
- `--background auto|opaque`: direct `background` parameter. Explicit `transparent` is unsupported on the tested backend and rejected locally.
- `--quality auto|low|medium|high`: direct `quality` parameter.
- `--size auto|WIDTHxHEIGHT`: requested dimensions; see constraints below.
- `--size-policy warn|error`: on a dimension mismatch, warn and save (default), or fail without writing the output.
- `--style-image PATH`: edit mode only. Use this image as the style reference; `--image` stays the content image and `--prompt` becomes optional extra guidance.
- `--output-format auto|png|webp`: output file format. Default: infer from `--out`; `.webp` writes WebP, everything else writes PNG.
- `--webp-quality 1..100`: WebP encoder quality. Default: `85`.
- `--input-max-edge PIXELS`: resize edit input images before upload so the longest edge is at most this value. Default: `1536`; use `0` to disable resizing.
- `--input-webp-quality 1..100`: WebP quality for compacted edit input images. Default: `90`.
- `--n COUNT`: request multiple output images. The CLI runs one hosted image request per output.
- `--dry-run`: print the request shape without reading auth or contacting the backend

## Backend Path

The default `--backend native` uses Codex's native image endpoints:

```text
POST https://chatgpt.com/backend-api/codex/images/generations
POST https://chatgpt.com/backend-api/codex/images/edits
```

Generation sends `model`, `prompt`, `size`, `quality`, and `background`; edits also send `images: [{"image_url": "data:image/webp;base64,..."}]`. Results contain `data[].b64_json`. The fixed request field `model: "gpt-image-2"` follows Codex's request format; it does not verify the actual output model. The backend controls the image generator, and GPT Image 2.5 availability cannot be confirmed. There is no `--image-model` option; `CODEX_IMAGEGEN_IMAGE_MODEL` has no effect.

Use `--backend responses` for the original hosted image-generation path:

`POST https://chatgpt.com/backend-api/codex/responses`

On this backend, the request forces the hosted `image_generation` tool and sends image options as tool parameters:

```json
{
  "model": "gpt-5.5",
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [{"type": "input_text", "text": "A studio photo of a mug"}]
    }
  ],
  "tools": [
    {
      "type": "image_generation",
      "output_format": "png",
      "size": "1536x1024",
      "quality": "high",
      "background": "opaque"
    }
  ],
  "tool_choice": {"type": "image_generation"},
  "stream": true
}
```

Responses edit requests add input images to the user message:

```json
{
  "content": [
    {"type": "input_text", "text": "Make it blue"},
    {"type": "input_image", "image_url": "data:image/webp;base64,..."}
  ],
  "tools": [{"type": "image_generation"}]
}
```

With `--style-image`, the content image is sent first and the style image is sent last. The text instruction labels that final image as style-only, so Codex has a clearer separation between "what to keep" and "what visual style to borrow."

With `--backend responses`, reasoning-model precedence is `--model`, `CODEX_IMAGEGEN_MODEL`, the selected Codex profile, the top-level Codex model, then `gpt-5.5`. `--profile` overrides the profile selected in `config.toml`. Native requests do not use reasoning-model settings. Existing commands with `--model` must add `--backend responses`; there is no automatic fallback between backends.

The accepted image preference values are:

- `size`: `auto` or `WIDTHxHEIGHT`, including `1024x1024` square, `1536x1024` landscape, or `1024x1536` portrait. Explicit dimensions must be divisible by 16, with no edge above 3840, an aspect ratio between 1:3 and 3:1, and 655,360–8,294,400 total pixels.
- `quality`: `auto`, `low`, `medium`, or `high`
- `background`: `auto` or `opaque`

Requested dimensions are not guaranteed. The CLI reports actual dimensions and warns on mismatches by default. `--size-policy error` rejects a mismatched output without saving it, even with `--force`; the generation has already occurred and may have consumed usage. The CLI does not resize or crop output to match the request.

`n` is handled by the CLI by running one hosted image request per output path.
For edit jobs, repeated outputs may wait for the per-minute input-image quota window before retrying; if the bucket stays full, retries back off progressively.
Edit input images are compacted locally to WebP before upload by default. This keeps request bodies smaller and reduces pressure on the input-image quota.
Codex returns PNG bytes; when the requested output is WebP, the CLI converts the PNG locally with Pillow.

## How It Works

`codex-imagegen-cli` reuses your normal Codex login. On each run it:

- reads Codex auth from `$CODEX_HOME/auth.json` or `~/.codex/auth.json`
- refreshes the ChatGPT access token when needed
- compacts edit input images to WebP before upload
- sends a native Codex image request by default, or a hosted `image_generation` tool request with `--backend responses`
- decodes PNG bytes from the native JSON response or streamed Responses result
- validates and atomically saves PNG output, or converts locally to WebP when requested

Dry runs only print the planned request and output paths; they do not read auth or contact Codex.

## Important Note

This project relies on Codex's current authenticated app behavior, not a public OpenAI Images API contract.

A few things worth knowing:

- **Subscription only.** This CLI requires file-based ChatGPT auth and account access to Codex image generation. It does not support API-key sessions or route requests to the public Images API. Account eligibility can change.
- **Usage limits apply.** Image generations consume your Codex account's included limits. Edit jobs also consume an input-image quota; if that per-minute bucket is full, the CLI waits and retries.
- **No stable API contract.** Codex internals and account policies can change. The CLI may stop working or behave differently after a Codex update, and OpenAI has not documented this as a supported automation surface.

We will try to keep this project updated as long as this usage remains possible and allowed.

## Development

```bash
uv lock --check
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Build and validate release artifacts:

```bash
uv run python -m build --installer uv
uv run python -m twine check dist/*
```

Offline tests use synthetic credentials. Opt-in live checks make three image requests (native generation/edit and Responses generation), consume account usage, and disable credential refresh:

```bash
CODEX_IMAGEGEN_LIVE_TEST=1 uv run pytest -m live -v
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

`0.2.0` is an alpha source-install release. The CLI command shape, JSONL batch format, and Codex backend behavior may change before a stable release.

See [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md), and [LICENSE](LICENSE).
