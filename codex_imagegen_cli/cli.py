from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, List, Optional, Sequence


DEFAULT_SANDBOX = "workspace-write"
DEFAULT_APPROVAL = "never"


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def _read_prompt(prompt: Optional[str], prompt_file: Optional[str]) -> str:
    if prompt and prompt_file:
        _die("Use --prompt or --prompt-file, not both.")
    if prompt_file:
        path = Path(prompt_file)
        if not path.exists():
            _die(f"Prompt file not found: {path}")
        text = path.read_text(encoding="utf-8").strip()
    elif prompt:
        text = prompt.strip()
    else:
        _die("Missing prompt. Use --prompt or --prompt-file.")
        text = ""
    if not text:
        _die("Prompt is empty.")
    return text


def _resolve_cd(raw_cd: Optional[str]) -> Path:
    cd = Path(raw_cd or os.getcwd()).expanduser().resolve()
    if not cd.exists():
        _die(f"--cd directory does not exist: {cd}")
    if not cd.is_dir():
        _die(f"--cd is not a directory: {cd}")
    return cd


def _resolve_output(raw: str, cd: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cd / path
    return path.resolve()


def _resolve_input_image(raw: str, cd: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cd / path
    path = path.resolve()
    if not path.exists():
        _die(f"Image file not found: {path}")
    if not path.is_file():
        _die(f"Image path is not a file: {path}")
    return path


def _check_output(path: Path, force: bool) -> None:
    if path.exists() and path.is_dir():
        _die(f"Output path is a directory: {path}")
    if path.exists() and not force:
        _die(f"Output already exists: {path} (use --force to allow replacement)")


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:60] or "image"


def _load_jobs_jsonl(path: str) -> List[dict[str, Any]]:
    input_path = Path(path).expanduser()
    if not input_path.exists():
        _die(f"Batch input not found: {input_path}")
    jobs: List[dict[str, Any]] = []
    for line_no, raw in enumerate(input_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            _die(f"Invalid JSON on line {line_no}: {exc}")
        if isinstance(item, str):
            item = {"prompt": item}
        if not isinstance(item, dict):
            _die(f"Line {line_no} must be a JSON string or object.")
        prompt = str(item.get("prompt", "")).strip()
        if not prompt:
            _die(f"Line {line_no} is missing a non-empty prompt.")
        images = item.get("images", [])
        if images is None:
            images = []
        if not isinstance(images, list) or not all(isinstance(v, str) for v in images):
            _die(f"Line {line_no} images must be a list of strings.")
        jobs.append(item)
    if not jobs:
        _die("Batch input did not contain any jobs.")
    return jobs


def _build_agent_prompt(
    *,
    mode: str,
    prompt: str,
    output_path: Path,
    image_paths: Sequence[Path],
    force: bool,
    extra_context: Optional[str],
) -> str:
    lines = [
        "You are running as a non-interactive Codex subagent launched by codex-imagegen-cli.",
        "",
        "Use the installed imagegen skill for this task.",
        "Use the skill's default built-in image_gen tool mode for image creation or editing.",
        "Do not use scripts/image_gen.py, the OpenAI Image API fallback CLI, or a custom one-off API runner.",
        "",
        f"Mode: {mode}",
        f"Final output path: {output_path}",
        f"Overwrite existing output if needed: {'yes' if force else 'no'}",
    ]
    if image_paths:
        lines.append("Input images:")
        for idx, image in enumerate(image_paths, start=1):
            role = "edit target" if mode == "edit" and idx == 1 else "supporting/reference image"
            lines.append(f"- Image {idx}: {image} ({role})")
    if extra_context:
        lines.extend(["", "Additional constraints/context:", extra_context.strip()])
    lines.extend(
        [
            "",
            "Required workflow:",
            "1. Generate or edit the raster image requested below with imagegen.",
            "2. Select one final result.",
            "3. Create parent directories for the final output path if needed.",
            "4. Copy or move the selected final image to the exact final output path.",
            "5. Verify the final output path exists before finishing.",
            "6. Keep the final response short and include the saved path.",
            "",
            "User prompt:",
            prompt,
        ]
    )
    return "\n".join(lines)


def _build_codex_command(
    *,
    args: argparse.Namespace,
    cd: Path,
    output_path: Path,
    image_paths: Sequence[Path],
    prompt: str,
) -> List[str]:
    codex_bin = args.codex_bin
    if shutil.which(codex_bin) is None and not Path(codex_bin).exists():
        _die(f"Codex binary not found: {codex_bin}")

    cmd = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        args.sandbox,
        "--ask-for-approval",
        args.ask_for_approval,
        "--cd",
        str(cd),
    ]

    if args.model:
        cmd.extend(["--model", args.model])
    if args.profile:
        cmd.extend(["--profile", args.profile])
    for config in args.config or []:
        cmd.extend(["--config", config])

    output_parent = output_path.parent
    if not _is_relative_to(output_parent, cd):
        cmd.extend(["--add-dir", str(output_parent)])

    for image in image_paths:
        cmd.extend(["--image", str(image)])

    for passthrough in args.codex_arg or []:
        cmd.append(passthrough)

    cmd.append(prompt)
    return cmd


def _print_dry_run(cmd: Sequence[str], prompt: str) -> None:
    preview_cmd = list(cmd)
    preview_cmd[-1] = "<agent-prompt>"
    print(json.dumps({"command": preview_cmd, "agent_prompt": prompt}, indent=2))


def _run_codex(cmd: Sequence[str], *, show_output: bool) -> subprocess.CompletedProcess[str]:
    if show_output:
        return subprocess.run(cmd, text=True)
    return subprocess.run(cmd, text=True, capture_output=True)


def _run_one(
    *,
    args: argparse.Namespace,
    mode: str,
    prompt: str,
    output_path: Path,
    image_paths: Sequence[Path],
    cd: Path,
) -> bool:
    _check_output(output_path, args.force)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    agent_prompt = _build_agent_prompt(
        mode=mode,
        prompt=prompt,
        output_path=output_path,
        image_paths=image_paths,
        force=args.force,
        extra_context=args.context,
    )
    cmd = _build_codex_command(
        args=args,
        cd=cd,
        output_path=output_path,
        image_paths=image_paths,
        prompt=agent_prompt,
    )

    if args.dry_run:
        _print_dry_run(cmd, agent_prompt)
        return True

    print(f"Running Codex imagegen subagent for {output_path}", file=sys.stderr)
    result = _run_codex(cmd, show_output=args.show_codex_output)
    if result.returncode != 0:
        if not args.show_codex_output:
            if result.stdout:
                print(result.stdout, file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
        _warn(f"Codex exited with status {result.returncode}")
        return False

    if not args.no_verify_output and not output_path.exists():
        if not args.show_codex_output:
            if result.stdout:
                print(result.stdout, file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
        _warn(f"Codex finished but output was not created: {output_path}")
        return False

    print(str(output_path))
    return True


def _add_codex_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--codex-bin", default="codex", help="Codex executable to run.")
    parser.add_argument("--cd", help="Working directory for the Codex subagent.")
    parser.add_argument("--model", help="Codex agent model, not the image model.")
    parser.add_argument("--profile", help="Codex config profile.")
    parser.add_argument(
        "--config",
        action="append",
        help="Codex -c/--config override. May be repeated.",
    )
    parser.add_argument(
        "--codex-arg",
        action="append",
        help="Extra raw argument appended to codex exec before the prompt. May be repeated.",
    )
    parser.add_argument("--sandbox", default=DEFAULT_SANDBOX)
    parser.add_argument("--ask-for-approval", default=DEFAULT_APPROVAL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show-codex-output", action="store_true")
    parser.add_argument("--no-verify-output", action="store_true")
    parser.add_argument(
        "--context",
        help="Extra instructions passed to the Codex subagent.",
    )


def _add_prompt_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--out", required=True)
    parser.add_argument("--force", action="store_true")


def _cmd_generate(args: argparse.Namespace) -> int:
    cd = _resolve_cd(args.cd)
    prompt = _read_prompt(args.prompt, args.prompt_file)
    output_path = _resolve_output(args.out, cd)
    ok = _run_one(
        args=args,
        mode="generate",
        prompt=prompt,
        output_path=output_path,
        image_paths=[],
        cd=cd,
    )
    return 0 if ok else 1


def _cmd_edit(args: argparse.Namespace) -> int:
    cd = _resolve_cd(args.cd)
    prompt = _read_prompt(args.prompt, args.prompt_file)
    output_path = _resolve_output(args.out, cd)
    image_paths = [_resolve_input_image(raw, cd) for raw in args.image]
    ok = _run_one(
        args=args,
        mode="edit",
        prompt=prompt,
        output_path=output_path,
        image_paths=image_paths,
        cd=cd,
    )
    return 0 if ok else 1


def _cmd_batch(args: argparse.Namespace) -> int:
    cd = _resolve_cd(args.cd)
    jobs = _load_jobs_jsonl(args.input)
    out_dir = _resolve_output(args.out_dir, cd)
    failures = 0
    for idx, job in enumerate(jobs, start=1):
        prompt = str(job["prompt"]).strip()
        raw_out = job.get("out")
        if raw_out:
            output_path = _resolve_output(str(out_dir / str(raw_out)), cd)
        else:
            output_path = out_dir / f"{idx:03d}-{_slugify(prompt)}.png"
        images = [_resolve_input_image(raw, cd) for raw in job.get("images", [])]
        mode = "edit" if images and job.get("mode") == "edit" else "generate"
        print(f"[{idx}/{len(jobs)}] {output_path}", file=sys.stderr)
        ok = _run_one(
            args=args,
            mode=mode,
            prompt=prompt,
            output_path=output_path,
            image_paths=images,
            cd=cd,
        )
        if not ok:
            failures += 1
            if args.fail_fast:
                break
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-imagegen",
        description="Scriptable wrapper that delegates image generation to a Codex imagegen subagent.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser("generate", help="Generate one image with Codex imagegen.")
    _add_prompt_args(gen)
    _add_codex_args(gen)
    gen.set_defaults(func=_cmd_generate)

    edit = subparsers.add_parser("edit", help="Edit one image task with Codex imagegen.")
    _add_prompt_args(edit)
    edit.add_argument("--image", action="append", required=True)
    _add_codex_args(edit)
    edit.set_defaults(func=_cmd_edit)

    batch = subparsers.add_parser("batch", help="Run one Codex imagegen subagent per JSONL job.")
    batch.add_argument("--input", required=True)
    batch.add_argument("--out-dir", required=True)
    batch.add_argument("--force", action="store_true")
    batch.add_argument("--fail-fast", action="store_true")
    _add_codex_args(batch)
    batch.set_defaults(func=_cmd_batch)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
