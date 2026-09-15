# 🎨 codex-imagegen-cli

Generate and edit images from your terminal using your existing **Codex ChatGPT login**. No OpenAI API key is required.

The CLI supports `generate`, `edit`, style references, sequential JSONL batches, and PNG/WebP output. It calls Codex's authenticated backend directly. This is an **unofficial, private backend integration**, whose availability and behavior can change.

## Install

Requirements: Python 3.10+, [uv](https://docs.astral.sh/uv/), and file-based Codex ChatGPT authentication.

```bash
git clone https://github.com/jdmnk/codex-imagegen-cli.git
cd codex-imagegen-cli
uv sync --frozen --dev
uv run codex-imagegen --help
```

To install a user command from this checkout:

```bash
uv tool install -e .
```

Both `codex-imagegen` and `codex-imagegen-cli` are entry points for the same command.

### Authentication

The CLI reads `$CODEX_HOME/auth.json` or `~/.codex/auth.json`. Override this with `--codex-home PATH` or `--auth-file PATH`.

Codex may store credentials in an OS keyring or only in memory. This CLI supports **file-based ChatGPT credentials only**. If needed, set this in your Codex `config.toml` before logging in:

```toml
cli_auth_credentials_store = "file"
```

Then run:

```bash
codex login
```

For a headless machine, Codex also supports `codex login --device-auth`. See [Codex authentication](https://learn.chatgpt.com/docs/auth).

API-key-only sessions, keyrings, ephemeral credentials, and enterprise access-token authentication are not implemented. An API key in your environment does not change this CLI to API billing. Treat `auth.json` as a secret; never commit it or include it in bug reports.

Token refresh preserves Codex's raw JWT format and writes the file atomically with owner-only permissions on POSIX. A separate lock coordinates imagegen processes. Codex itself does not share that lock: the CLI detects observed external auth changes and refuses to overwrite them, but cannot guarantee coordination with every external writer.

## Quick start

```bash
uv run codex-imagegen generate \
  --prompt "A retro RPG pixel icon of an iron helmet, no text" \
  --out output/iron-helmet.png
```

Inspect the planned request without reading credentials or contacting the backend:

```bash
uv run codex-imagegen generate \
  --prompt "A studio photo of a mug" \
  --out output/mug.png \
  --dry-run
```

## Generate

```bash
codex-imagegen generate \
  --prompt "A bronze shield icon with a red gem, no text" \
  --out output/shield.webp \
  --n 3 \
  --webp-quality 82
```

This makes one request per output, sequentially, writing `shield-1.webp`, `shield-2.webp`, and `shield-3.webp`.

Successful paths are printed to stdout as soon as each file is saved. Progress, actual dimensions and warnings go to stderr. If a later output fails, earlier files remain and their paths have already been printed. Existing files are protected unless `--force` is set.

## Edit and style references

```bash
codex-imagegen edit \
  --image input/sword.png \
  --prompt "Make this a fire-enchanted sword; preserve its silhouette" \
  --out output/fire-sword.png
```

Use one to five `--image` inputs. For a style reference:

```bash
codex-imagegen edit \
  --image input/profile.png \
  --style-image input/editorial-style.png \
  --prompt "Keep the background bright" \
  --out output/profile.webp
```

With `--style-image`, the prompt is optional extra guidance. Content images are sent first, and the style reference is sent last. The generated instruction preserves the first image's subject and composition and borrows visual style from the final image. The style reference counts toward the five-image limit.

Input images are decoded, corrected for EXIF orientation, resized to a maximum edge of 1536 pixels and encoded as WebP at quality 90. Original files are not changed. Use `--input-max-edge 0` to disable resizing; WebP encoding still occurs. Invalid input images fail locally instead of uploading undecodable original bytes.

PNG output is validated before saving. WebP output is converted locally with Pillow. Files are published atomically after validation; a failed conversion or rejected size cannot truncate an existing output.

## Size, quality and background

- `--size auto` is the default. Explicit `WIDTHxHEIGHT` values require dimensions divisible by 16, no edge above 3840, an aspect ratio between 1:3 and 3:1, and 655,360–8,294,400 pixels. Examples: `1024x1024`, `1536x1024`, `2048x2048`, `3840x2160`.
- **Requested dimensions are not guaranteed.** The authenticated backend has returned different sizes even for explicit requests. The CLI reports the decoded dimensions.
- `--size-policy warn` (default) warns and saves the backend's original dimensions.
- `--size-policy error` fails without writing that output if explicit dimensions differ. The generation has already occurred and may have consumed usage. Existing output is preserved even with `--force`. No automatic resizing or cropping occurs.
- `--quality auto|low|medium|high` is passed to the backend.
- `--background auto|opaque` works with the current default path. Explicit transparent backgrounds are unsupported on the tested Codex image backend, so the CLI rejects `--background transparent` locally.
- `--output-format auto|png|webp` controls local output. `auto` chooses WebP for `.webp` paths, otherwise PNG. An explicit format overrides the extension.

For a script that requires exact dimensions:

```bash
codex-imagegen generate \
  --prompt "A clean landscape illustration" \
  --size 1536x1024 \
  --size-policy error \
  --out output/landscape.png
```

## Batch

Create a JSONL file with one prompt string or job object per line:

```jsonl
"An iron helmet icon"
{"prompt":"A bronze shield icon","out":"shield.webp"}
{"prompt":"Make this shield icy","images":["input/shield.png"],"mode":"edit","out":"ice-shield.png"}
```

```bash
codex-imagegen batch --input jobs.jsonl --out-dir output/batch
```

Job fields:

- `prompt`: required non-empty string.
- `out`: optional non-empty filename/path under `--out-dir`; paths escaping that directory are rejected.
- `images`: optional list of paths; `null` is treated as an empty list.
- `mode`: `generate` or `edit`; inferred from whether images are provided when omitted.

Relative input-image paths are resolved against `--cd` or the working directory. Blank lines and lines starting with `#` are ignored. Whole-file JSON and basic schema errors are rejected before execution. Per-job mode/path/backend failures are reported and later jobs continue, unless `--fail-fast` is set. Any failed job produces exit status 1. Batch-wide image settings apply to all jobs.

## Backends and reasoning-model selection

### Native images — default in 0.2.0

`--backend native` follows the native image request format used by Codex 0.154.0:

```text
POST https://chatgpt.com/backend-api/codex/images/generations
POST https://chatgpt.com/backend-api/codex/images/edits
```

Generation request:

```json
{
  "model": "gpt-image-2",
  "prompt": "A studio photo of a mug",
  "size": "auto",
  "quality": "auto",
  "background": "auto"
}
```

Edits add `images: [{"image_url": "data:image/webp;base64,..."}]`. Responses contain `data[].b64_json`. The fixed `model: "gpt-image-2"` field follows the Codex request format; it does not verify which image model produced the output. The backend controls the image model, and the CLI exposes no image-model selector. Reasoning-model settings in Codex config are not used for native images.

In September 15, 2026 subscription-backend probes, both GPT Image 2.5 names and a deliberately nonexistent model name returned images without reporting model identity. Successful requests therefore do not establish model selection or GPT Image 2.5 availability. This CLI uses Codex subscription access only.

### Responses — explicit compatibility mode

The original route remains available:

```bash
codex-imagegen generate \
  --backend responses \
  --model gpt-5.5 \
  --prompt "A studio photo of a mug" \
  --out output/mug.png
```

It posts a forced `image_generation` tool request to `/backend-api/codex/responses` and reads streamed results. `--model` (alias `--reasoning-model`) selects the reasoning model on this backend; it does not select the image generator. Precedence is the flag, `CODEX_IMAGEGEN_MODEL`, the selected Codex profile's model, the top-level Codex model, then `gpt-5.5`. Use `--profile NAME` to override the top-level `profile` selection in `config.toml`.

There is no automatic fallback between backends: retrying a request through another route could generate a duplicate image. Model access varies by account.

### Migration from 0.1.0

Existing basic generate/edit/batch commands now use native images. Commands specifying a reasoning `--model` or `--profile` must add `--backend responses`. The experimental `--image-model` flag has been removed, and `CODEX_IMAGEGEN_IMAGE_MODEL` has no effect; the backend controls the image model. Explicit transparency on the default path now fails locally. Corrupt images, invalid batch fields and escaped output paths are rejected. Size mismatches now produce warnings, with optional strict failure.

## Other options and errors

- `--prompt-file PATH`: UTF-8 prompt file, mutually exclusive with `--prompt`.
- `--cd PATH`: base directory for relative input/output paths.
- `--auth-file PATH`, `--codex-home PATH`: credential location overrides.
- `--timeout SECONDS`: positive finite HTTP socket timeout, default 300. This is not a whole-command deadline; retry waits and multiple images add time.
- `--input-max-edge PIXELS`, `--input-webp-quality 1..100`: edit input compaction settings.
- `--webp-quality 1..100`: local WebP output quality, default 85.
- `--base-url URL` / `CODEX_IMAGEGEN_BASE_URL`: backend override for development. Credentials are sent to that URL; use only a trusted backend.
- `CODEX_IMAGEGEN_REFRESH_URL`: trusted development override for the OAuth endpoint.
- `CODEX_IMAGEGEN_CODEX_VERSION`: override the version header when Codex is not available locally.

HTTP 429 and streamed rate limits use bounded retries (at most four), including bounded `Retry-After` delays and longer input-image quota waits. One 401 refresh is allowed per output. Known billing/quota/user errors are not retried. Transport interruptions and ambiguous server failures are surfaced without automatic replay because an image may already have been generated. Exit codes are 0 for success, 1 for runtime/job failure, 2 for argument parsing errors, and 130 for interruption.

Generation consumes the account's Codex usage limits. Account access and quota policies can change. This tool is not a supported public OpenAI API contract.

## Development and compatibility checks

```bash
uv lock --check
uv sync --frozen --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python -m build --installer uv
uv run python -m twine check dist/*
```

The build requires `uv` on PATH. Build dependencies are constrained to the tested Hatchling minor release, and the publishing toolchain supports its metadata format. CI covers Python 3.10, 3.12 and 3.14. Offline tests use synthetic credentials. If a Codex binary is installed, a test also checks that the refreshed synthetic login file is readable by Codex.

Opt-in live compatibility checks make **three image requests** (native generation/edit and Responses generation), consume account usage and never refresh your shared credentials:

```bash
CODEX_IMAGEGEN_LIVE_TEST=1 uv run pytest -m live -v
```

Outputs use pytest's temporary directory. Without the environment variable, live tests are skipped. No server or browser is started. See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## Update, uninstall and security

```bash
git pull
uv tool install --reinstall -e .
```

```bash
uv tool uninstall codex-imagegen-cli
```

Report security issues privately as described in [SECURITY.md](SECURITY.md). Version `0.2.0` remains an alpha source-install release. See [LICENSE](LICENSE).
