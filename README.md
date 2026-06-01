# codex-imagegen-cli

`codex-imagegen-cli` is a local, scriptable wrapper around the Codex `imagegen` skill.

It is intentionally not a direct Image API client. It starts a `codex exec` subagent, tells that subagent to use the installed `imagegen` skill in built-in tool mode, and requires it to save the final image to the path you requested.

## Install

From this directory:

```bash
python -m pip install -e .
```

This installs two equivalent commands:

```bash
codex-imagegen --help
codex-imagegen-cli --help
```

You also need a working `codex` command in `PATH` and an installed Codex `imagegen` skill. The built-in image generation path uses your Codex setup; it does not require `OPENAI_API_KEY`.

## Generate

```bash
codex-imagegen generate \
  --prompt "A clean product photo of a ceramic coffee mug on a white studio background" \
  --out output/imagegen/mug.png
```

Useful script flags:

```bash
codex-imagegen generate \
  --prompt-file prompt.txt \
  --out output/imagegen/hero.png \
  --force
```

## Edit

```bash
codex-imagegen edit \
  --image input/product.png \
  --prompt "Replace only the background with a warm sunset gradient; keep the product unchanged" \
  --out output/imagegen/product-sunset.png
```

The wrapper passes each `--image` to `codex exec --image`, so the Codex subagent can see it in context.

## Batch

Batch mode runs one Codex subagent per JSONL line.

```bash
mkdir -p tmp/imagegen
cat > tmp/imagegen/jobs.jsonl <<'EOF'
{"prompt":"A compact shuttle parked in a cavernous hangar","out":"shuttle.png"}
{"prompt":"A gray wolf in profile in a snowy forest","out":"wolf.png"}
EOF

codex-imagegen batch \
  --input tmp/imagegen/jobs.jsonl \
  --out-dir output/imagegen/batch
```

Each line can be either a JSON string prompt or an object with:

- `prompt`: required text prompt
- `out`: optional output filename, resolved under `--out-dir`
- `images`: optional list of image paths to attach to the Codex subagent

## How It Works

The wrapper calls roughly this shape of command:

```bash
codex exec \
  --skip-git-repo-check \
  --sandbox workspace-write \
  --ask-for-approval never \
  --cd "$PWD" \
  --add-dir "$(dirname "$OUT")" \
  "Use the imagegen skill with the built-in image_gen tool..."
```

The subagent is instructed to:

- use the installed `imagegen` skill
- use built-in `image_gen` mode
- avoid the fallback `scripts/image_gen.py` API client
- copy or move the selected final asset to your exact `--out` path
- verify the path exists before finishing

For debugging, use:

```bash
codex-imagegen generate --prompt "Test image" --out output/imagegen/test.png --dry-run
codex-imagegen generate --prompt "Test image" --out output/imagegen/test.png --show-codex-output
```

