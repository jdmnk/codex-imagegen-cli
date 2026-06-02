from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import mimetypes
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, List, Optional, Sequence, Tuple
from urllib import error, request

from codex_imagegen_cli import __version__


DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_REFRESH_URL = "https://auth.openai.com/oauth/token"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_CODEX_MODEL = "gpt-5.5"
MAX_EDIT_IMAGES = 5
DEFAULT_ORIGINATOR = "codex_cli_rs"


class CliError(Exception):
    """Expected user-facing CLI failure."""


class HttpError(CliError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:800]}")
        self.status = status
        self.body = body


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _read_prompt(prompt: Optional[str], prompt_file: Optional[str], cd: Path) -> str:
    if prompt and prompt_file:
        raise CliError("Use --prompt or --prompt-file, not both.")
    if prompt_file:
        path = _resolve_path(prompt_file, cd)
        if not path.exists():
            raise CliError(f"Prompt file not found: {path}")
        text = path.read_text(encoding="utf-8").strip()
    elif prompt:
        text = prompt.strip()
    else:
        raise CliError("Missing prompt. Use --prompt or --prompt-file.")
    if not text:
        raise CliError("Prompt is empty.")
    return text


def _resolve_cd(raw_cd: Optional[str]) -> Path:
    cd = Path(raw_cd or os.getcwd()).expanduser().resolve()
    if not cd.exists():
        raise CliError(f"--cd directory does not exist: {cd}")
    if not cd.is_dir():
        raise CliError(f"--cd is not a directory: {cd}")
    return cd


def _resolve_path(raw: str, cd: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cd / path
    return path.resolve()


def _check_output(path: Path, force: bool) -> None:
    if path.exists() and path.is_dir():
        raise CliError(f"Output path is a directory: {path}")
    if path.exists() and not force:
        raise CliError(f"Output already exists: {path} (use --force to allow replacement)")


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:60] or "image"


def _load_jobs_jsonl(path: str, cd: Path) -> List[dict[str, Any]]:
    input_path = _resolve_path(path, cd)
    if not input_path.exists():
        raise CliError(f"Batch input not found: {input_path}")
    jobs: List[dict[str, Any]] = []
    for line_no, raw in enumerate(input_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CliError(f"Invalid JSON on line {line_no}: {exc}") from exc
        if isinstance(item, str):
            item = {"prompt": item}
        if not isinstance(item, dict):
            raise CliError(f"Line {line_no} must be a JSON string or object.")
        prompt = str(item.get("prompt", "")).strip()
        if not prompt:
            raise CliError(f"Line {line_no} is missing a non-empty prompt.")
        images = item.get("images", [])
        if images is None:
            images = []
        if not isinstance(images, list) or not all(isinstance(v, str) for v in images):
            raise CliError(f"Line {line_no} images must be a list of strings.")
        jobs.append(item)
    if not jobs:
        raise CliError("Batch input did not contain any jobs.")
    return jobs


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _token_is_expiring(access_token: str, *, leeway_seconds: int = 60) -> bool:
    exp = _jwt_payload(access_token).get("exp")
    if not isinstance(exp, (int, float)):
        return False
    return exp <= datetime.now(timezone.utc).timestamp() + leeway_seconds


def _codex_home(args: argparse.Namespace) -> Path:
    if args.codex_home:
        return Path(args.codex_home).expanduser().resolve()
    if os.environ.get("CODEX_HOME"):
        return Path(os.environ["CODEX_HOME"]).expanduser().resolve()
    return Path.home() / ".codex"


def _auth_file(args: argparse.Namespace) -> Path:
    if args.auth_file:
        return Path(args.auth_file).expanduser().resolve()
    return _codex_home(args) / "auth.json"


def _load_auth(auth_file: Path) -> dict[str, Any]:
    if not auth_file.exists():
        raise CliError(
            f"Codex auth file not found: {auth_file}. Run `codex login` and choose ChatGPT."
        )
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(f"Invalid Codex auth file JSON: {auth_file}") from exc
    if not isinstance(data, dict):
        raise CliError(f"Codex auth file must contain a JSON object: {auth_file}")
    return data


def _extract_id_token(auth: dict[str, Any]) -> dict[str, Any]:
    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        return {}
    id_token = tokens.get("id_token")
    if isinstance(id_token, dict):
        return id_token
    if isinstance(id_token, str):
        parsed = _id_token_from_jwt(id_token)
        return parsed
    return {}


def _access_token(auth: dict[str, Any]) -> str:
    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        raise CliError("Codex ChatGPT auth not found. Run `codex login` and choose ChatGPT.")
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise CliError("Codex ChatGPT access token not found. Run `codex login` and choose ChatGPT.")
    return access_token


def _account_id(auth: dict[str, Any]) -> Optional[str]:
    tokens = auth.get("tokens")
    if isinstance(tokens, dict):
        account_id = tokens.get("account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    id_token = _extract_id_token(auth)
    account_id = id_token.get("chatgpt_account_id")
    return account_id if isinstance(account_id, str) and account_id else None


def _is_fedramp(auth: dict[str, Any]) -> bool:
    return bool(_extract_id_token(auth).get("chatgpt_account_is_fedramp"))


def _auth_headers(auth: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {_access_token(auth)}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "originator": _originator(),
        "User-Agent": _codex_user_agent(),
        "version": _codex_version(),
    }
    account_id = _account_id(auth)
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id
    if _is_fedramp(auth):
        headers["X-OpenAI-Fedramp"] = "true"
    return headers


def _originator() -> str:
    return os.environ.get("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", DEFAULT_ORIGINATOR)


def _codex_version() -> str:
    override = os.environ.get("CODEX_IMAGEGEN_CODEX_VERSION")
    if override:
        return override
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return __version__
    try:
        result = subprocess.run(
            [codex_bin, "--version"],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return __version__
    output = f"{result.stdout}\n{result.stderr}".strip()
    match = re.search(r"(\d+\.\d+\.\d+)", output)
    return match.group(1) if match else __version__


def _codex_user_agent() -> str:
    system = platform.system() or "unknown"
    release = platform.release() or "unknown"
    machine = platform.machine() or "unknown"
    return f"{_originator()}/{_codex_version()} ({system} {release}; {machine}) codex-imagegen-cli/{__version__}"


def _id_token_from_jwt(raw_jwt: str) -> dict[str, Any]:
    payload = _jwt_payload(raw_jwt)
    auth_claim = payload.get("https://api.openai.com/auth")
    if not isinstance(auth_claim, dict):
        auth_claim = {}
    profile_email = payload.get("https://api.openai.com/profile.email")
    email = payload.get("email") or profile_email
    return {
        "email": email,
        "chatgpt_plan_type": auth_claim.get("chatgpt_plan_type"),
        "chatgpt_user_id": auth_claim.get("chatgpt_user_id") or auth_claim.get("user_id"),
        "chatgpt_account_id": auth_claim.get("chatgpt_account_id"),
        "chatgpt_account_is_fedramp": bool(auth_claim.get("chatgpt_account_is_fedramp")),
        "raw_jwt": raw_jwt,
    }


def _post_json(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    req = request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise HttpError(exc.code, raw) from exc
    except error.URLError as exc:
        raise CliError(f"Request failed: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError(f"Response was not valid JSON: {raw[:800]}") from exc
    if not isinstance(parsed, dict):
        raise CliError("Response JSON was not an object.")
    return parsed


def _post_sse(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
):
    request_headers = dict(headers)
    request_headers["Accept"] = "text/event-stream"
    request_headers["Content-Type"] = "application/json"
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            pending = ""
            while True:
                chunk = response.read(4096)
                if not chunk:
                    break
                pending += chunk.decode("utf-8", errors="replace")
                while "\n\n" in pending:
                    block, pending = pending.split("\n\n", 1)
                    parsed = _parse_sse_block(block)
                    if parsed is not None:
                        yield parsed
            parsed = _parse_sse_block(pending)
            if parsed is not None:
                yield parsed
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise HttpError(exc.code, raw) from exc
    except error.URLError as exc:
        raise CliError(f"Request failed: {exc.reason}") from exc


def _parse_sse_block(block: str) -> Optional[Tuple[Optional[str], str]]:
    event = None
    data_lines: List[str] = []
    for raw in block.splitlines():
        if raw.startswith("event:"):
            event = raw.split(":", 1)[1].strip()
        elif raw.startswith("data:"):
            data_lines.append(raw.split(":", 1)[1].lstrip())
    if not event and not data_lines:
        return None
    return event, "\n".join(data_lines)


def _refresh_auth(auth: dict[str, Any], auth_file: Path, *, timeout: float) -> dict[str, Any]:
    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        raise CliError("Codex ChatGPT auth not found. Run `codex login` and choose ChatGPT.")
    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise CliError("Codex refresh token not found. Run `codex login` again.")

    refresh_url = os.environ.get("CODEX_IMAGEGEN_REFRESH_URL", DEFAULT_REFRESH_URL)
    payload = {
        "client_id": CODEX_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    refreshed = _post_json(refresh_url, payload=payload, timeout=timeout)
    for key in ("access_token", "refresh_token"):
        value = refreshed.get(key)
        if isinstance(value, str) and value:
            tokens[key] = value
    id_token = refreshed.get("id_token")
    if isinstance(id_token, str) and id_token:
        tokens["id_token"] = _id_token_from_jwt(id_token)
    auth["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(json.dumps(auth, indent=2) + "\n", encoding="utf-8")
    return auth


def _load_ready_auth(args: argparse.Namespace) -> Tuple[dict[str, Any], Path]:
    auth_file = _auth_file(args)
    auth = _load_auth(auth_file)
    if _token_is_expiring(_access_token(auth)):
        auth = _refresh_auth(auth, auth_file, timeout=args.timeout)
    return auth, auth_file


def _data_url_for_image(path: Path) -> str:
    if not path.exists():
        raise CliError(f"Image file not found: {path}")
    if not path.is_file():
        raise CliError(f"Image path is not a file: {path}")
    mime_type = mimetypes.guess_type(str(path))[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _codex_config_file(args: argparse.Namespace) -> Path:
    return _codex_home(args) / "config.toml"


def _default_model(args: argparse.Namespace) -> str:
    if args.model:
        return args.model
    env_model = os.environ.get("CODEX_IMAGEGEN_MODEL")
    if env_model:
        return env_model
    config_file = _codex_config_file(args)
    if config_file.exists():
        match = re.search(
            r"""(?m)^model\s*=\s*["']([^"']+)["']""",
            config_file.read_text(encoding="utf-8"),
        )
        if match:
            return match.group(1)
    return DEFAULT_CODEX_MODEL


def _prompt_with_controls(prompt: str, args: argparse.Namespace) -> str:
    controls = []
    if args.size != "auto":
        controls.append(f"Requested image size: {args.size}.")
    if args.quality != "auto":
        controls.append(f"Requested quality: {args.quality}.")
    if args.background != "auto":
        controls.append(f"Requested background: {args.background}.")
    if args.image_model != DEFAULT_IMAGE_MODEL:
        controls.append(f"Requested image model preference: {args.image_model}.")
    if not controls:
        return prompt
    return "\n\n".join([prompt, "Additional image constraints:\n" + "\n".join(controls)])


def _responses_payload(
    *,
    prompt: str,
    args: argparse.Namespace,
    mode: str,
    images: Optional[Sequence[Path]] = None,
) -> dict[str, Any]:
    content: List[dict[str, Any]] = [{"type": "input_text", "text": _prompt_with_controls(prompt, args)}]
    if images:
        if len(images) > MAX_EDIT_IMAGES:
            raise CliError(f"Edit supports at most {MAX_EDIT_IMAGES} images.")
        for image in images:
            content.append({"type": "input_image", "image_url": _data_url_for_image(image)})
    instructions = (
        "Use the available image generation tool to generate exactly one PNG image for the user request. "
        "Do not use any other tool."
    )
    if mode == "edit":
        instructions += " Treat the provided input images as edit/reference images for the request."
    return {
        "model": _default_model(args),
        "instructions": instructions,
        "input": [{"type": "message", "role": "user", "content": content}],
        "tools": [{"type": "image_generation", "output_format": "png"}],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "reasoning": None,
        "store": False,
        "stream": True,
        "include": [],
        "prompt_cache_key": "codex-imagegen-cli",
        "client_metadata": {"x-codex-installation-id": "codex-imagegen-cli"},
    }


def _image_payload(
    *,
    prompt: str,
    args: argparse.Namespace,
    images: Optional[Sequence[Path]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt": prompt,
        "background": args.background,
        "model": args.image_model,
        "quality": args.quality,
        "size": args.size,
    }
    if args.n != 1:
        payload["n"] = args.n
    if images is not None:
        if len(images) > MAX_EDIT_IMAGES:
            raise CliError(f"Edit supports at most {MAX_EDIT_IMAGES} images.")
        payload["images"] = [{"image_url": _data_url_for_image(path)} for path in images]
    return payload


def _endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _decode_outputs(response: dict[str, Any], output_path: Path, *, force: bool) -> List[Path]:
    data = response.get("data")
    if not isinstance(data, list) or not data:
        raise CliError("Image response did not contain any data.")
    paths = _output_paths(output_path, len(data))
    for item, path in zip(data, paths):
        if not isinstance(item, dict):
            raise CliError("Image response item was not an object.")
        encoded = item.get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise CliError("Image response item did not contain b64_json.")
        _check_output(path, force)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_bytes(base64.b64decode(encoded))
        except ValueError as exc:
            raise CliError("Image response b64_json was not valid base64.") from exc
    return paths


def _write_response_image(encoded: str, output_path: Path, *, force: bool) -> Path:
    _check_output(output_path, force)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_path.write_bytes(base64.b64decode(encoded))
    except ValueError as exc:
        raise CliError("Image generation result was not valid base64.") from exc
    return output_path


def _output_paths(output_path: Path, count: int) -> List[Path]:
    if count <= 1:
        return [output_path]
    suffix = output_path.suffix or ".png"
    stem = output_path.stem
    return [output_path.with_name(f"{stem}-{idx}{suffix}") for idx in range(1, count + 1)]


def _redacted_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted = dict(headers)
    if "Authorization" in redacted:
        redacted["Authorization"] = "Bearer <redacted>"
    return redacted


def _dry_run_payload(
    *,
    url: str,
    headers: Optional[dict[str, str]],
    payload: dict[str, Any],
    output_path: Path,
    output_count: Optional[int] = None,
) -> dict[str, Any]:
    count = output_count if output_count is not None else int(payload.get("n", 1))
    return {
        "method": "POST",
        "url": url,
        "headers": _redacted_headers(headers or {"Authorization": "Bearer <redacted>"}),
        "payload": _redact_large_images(payload),
        "outputs": [str(path) for path in _output_paths(output_path, count)],
    }


def _redact_large_images(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_data_url(item) if key == "image_url" else _redact_large_images(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_large_images(item) for item in value]
    return value


def _redact_data_url(value: Any) -> Any:
    if not isinstance(value, str) or ";base64," not in value:
        return value
    prefix = value.split(";base64,", 1)[0]
    return f"{prefix};base64,<redacted>"


def _call_direct_image_backend(
    *,
    args: argparse.Namespace,
    mode: str,
    payload: dict[str, Any],
    output_path: Path,
) -> List[Path]:
    path = "images/generations" if mode == "generate" else "images/edits"
    url = _endpoint(args.base_url, path)
    if args.dry_run:
        print(json.dumps(_dry_run_payload(url=url, headers=None, payload=payload, output_path=output_path), indent=2))
        return _output_paths(output_path, int(payload.get("n", 1)))

    auth, auth_file = _load_ready_auth(args)
    headers = _auth_headers(auth)
    try:
        response = _post_json(url, headers=headers, payload=payload, timeout=args.timeout)
    except HttpError as exc:
        if exc.status != 401:
            raise
        auth = _refresh_auth(auth, auth_file, timeout=args.timeout)
        headers = _auth_headers(auth)
        response = _post_json(url, headers=headers, payload=payload, timeout=args.timeout)
    return _decode_outputs(response, output_path, force=args.force)


def _call_responses_backend(
    *,
    args: argparse.Namespace,
    mode: str,
    prompt: str,
    output_path: Path,
    image_paths: Optional[Sequence[Path]] = None,
) -> List[Path]:
    url = _endpoint(args.base_url, "responses")
    payload = _responses_payload(prompt=prompt, args=args, mode=mode, images=image_paths)
    output_paths = _output_paths(output_path, args.n)
    if args.dry_run:
        print(
            json.dumps(
                _dry_run_payload(
                    url=url,
                    headers=None,
                    payload=payload,
                    output_path=output_path,
                    output_count=args.n,
                ),
                indent=2,
            )
        )
        return output_paths

    auth, auth_file = _load_ready_auth(args)
    headers = _auth_headers(auth)
    saved: List[Path] = []
    for idx, path in enumerate(output_paths, start=1):
        current_payload = dict(payload)
        if args.n > 1:
            current_payload["prompt_cache_key"] = f"codex-imagegen-cli-{idx}"
            _log(f"  image {idx}/{args.n} ...")
        try:
            encoded = _stream_image_result(url, headers=headers, payload=current_payload, timeout=args.timeout)
        except HttpError as exc:
            if exc.status != 401:
                raise
            auth = _refresh_auth(auth, auth_file, timeout=args.timeout)
            headers = _auth_headers(auth)
            encoded = _stream_image_result(url, headers=headers, payload=current_payload, timeout=args.timeout)
        saved.append(_write_response_image(encoded, path, force=args.force))
    return saved


def _stream_image_result(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> str:
    last_status = None
    last_error = None
    for _event, data in _post_sse(url, headers=headers, payload=payload, timeout=timeout):
        if not data:
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") in {"response.failed", "response.incomplete"}:
            last_error = event
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "image_generation_call":
            last_status = item.get("status")
            result = item.get("result")
            if isinstance(result, str) and result:
                return result
    if last_error is not None:
        raise CliError(f"Responses image generation failed: {last_error}")
    if last_status:
        raise CliError(f"Responses stream ended without an image result; last status was {last_status}.")
    raise CliError("Responses stream ended without an image generation result.")


def _call_image_backend(
    *,
    args: argparse.Namespace,
    mode: str,
    prompt: str,
    output_path: Path,
    image_paths: Optional[Sequence[Path]] = None,
) -> List[Path]:
    if args.backend == "direct":
        payload = _image_payload(
            prompt=prompt,
            args=args,
            images=image_paths if mode == "edit" else None,
        )
        return _call_direct_image_backend(
            args=args,
            mode=mode,
            payload=payload,
            output_path=output_path,
        )
    return _call_responses_backend(
        args=args,
        mode=mode,
        prompt=prompt,
        output_path=output_path,
        image_paths=image_paths,
    )


def _print_saved(paths: Sequence[Path]) -> None:
    for path in paths:
        print(str(path))


def _run_one(
    *,
    args: argparse.Namespace,
    mode: str,
    prompt: str,
    output_path: Path,
    image_paths: Optional[Sequence[Path]] = None,
    log_prefix: str = "",
) -> bool:
    if not args.dry_run:
        for path in _output_paths(output_path, args.n):
            _check_output(path, args.force)

    if not args.dry_run:
        count = f"{args.n}× " if args.n > 1 else ""
        prefix = f"{log_prefix} " if log_prefix else ""
        _log(f"{prefix}{mode} {count}{output_path}")
        t0 = time.monotonic()

    paths = _call_image_backend(
        args=args,
        mode=mode,
        prompt=prompt,
        output_path=output_path,
        image_paths=image_paths,
    )

    if not args.dry_run:
        elapsed = time.monotonic() - t0
        _log(f"  done ({elapsed:.1f}s)")
        _print_saved(paths)
    return True


def _add_auth_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cd", help="Base directory for resolving relative paths.")
    parser.add_argument("--auth-file", help="Path to Codex auth.json. Defaults to $CODEX_HOME/auth.json.")
    parser.add_argument("--codex-home", help="Codex home directory. Defaults to $CODEX_HOME or ~/.codex.")
    parser.add_argument(
        "--model",
        help=(
            "Codex reasoning model. Defaults to CODEX_IMAGEGEN_MODEL, "
            "then Codex config, then the built-in fallback."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=["responses", "direct"],
        default="responses",
        help="Backend path to use. Default is the stable responses path.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CODEX_IMAGEGEN_BASE_URL", DEFAULT_BASE_URL),
        help="Codex backend base URL.",
    )
    parser.add_argument("--timeout", type=float, default=300.0, help="HTTP timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Print the request without contacting Codex.")


def _add_image_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--image-model",
        default=DEFAULT_IMAGE_MODEL,
        help="Image model preference. Exact model control is only available with --backend direct.",
    )
    parser.add_argument(
        "--background",
        choices=["auto", "transparent", "opaque"],
        default="auto",
        help="Background preference. Exact control is only available with --backend direct.",
    )
    parser.add_argument(
        "--quality",
        choices=["auto", "low", "medium", "high"],
        default="auto",
        help="Quality preference. Exact control is only available with --backend direct.",
    )
    parser.add_argument(
        "--size",
        default="auto",
        help='Size preference, for example "auto" or "1024x1024". Exact control is only available with --backend direct.',
    )
    parser.add_argument("--n", type=int, default=1, help="Number of images to request.")


def _add_prompt_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--out", required=True)
    parser.add_argument("--force", action="store_true")


def _validate_common(args: argparse.Namespace) -> Path:
    if args.n < 1:
        raise CliError("--n must be at least 1.")
    return _resolve_cd(args.cd)


def _cmd_generate(args: argparse.Namespace) -> int:
    cd = _validate_common(args)
    prompt = _read_prompt(args.prompt, args.prompt_file, cd)
    output_path = _resolve_path(args.out, cd)
    _run_one(args=args, mode="generate", prompt=prompt, output_path=output_path)
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    cd = _validate_common(args)
    prompt = _read_prompt(args.prompt, args.prompt_file, cd)
    output_path = _resolve_path(args.out, cd)
    image_paths = [_resolve_path(raw, cd) for raw in args.image]
    _run_one(
        args=args,
        mode="edit",
        prompt=prompt,
        output_path=output_path,
        image_paths=image_paths,
    )
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    cd = _validate_common(args)
    jobs = _load_jobs_jsonl(args.input, cd)
    out_dir = _resolve_path(args.out_dir, cd)
    failures = 0
    for idx, job in enumerate(jobs, start=1):
        prompt = str(job["prompt"]).strip()
        raw_out = job.get("out")
        if raw_out:
            output_path = _resolve_path(str(out_dir / str(raw_out)), cd)
        else:
            output_path = out_dir / f"{idx:03d}-{_slugify(prompt)}.png"
        images = [_resolve_path(raw, cd) for raw in job.get("images", [])]
        mode = str(job.get("mode", "edit" if images else "generate"))
        if mode not in {"generate", "edit"}:
            raise CliError(f"Job {idx} has invalid mode: {mode}")
        if mode == "edit" and not images:
            raise CliError(f"Job {idx} mode is edit but images is empty.")
        try:
            _run_one(
                args=args,
                mode=mode,
                prompt=prompt,
                output_path=output_path,
                image_paths=images,
                log_prefix=f"[{idx}/{len(jobs)}]",
            )
        except CliError as exc:
            failures += 1
            _warn(f"[{idx}/{len(jobs)}] failed: {exc}")
            if args.fail_fast:
                break
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-imagegen",
        description="Generate and edit images through the Codex ChatGPT image backend.",
    )
    parser.add_argument("--version", "-v", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser("generate", help="Generate one or more images.")
    _add_prompt_args(gen)
    _add_image_args(gen)
    _add_auth_args(gen)
    gen.set_defaults(func=_cmd_generate)

    edit = subparsers.add_parser("edit", help="Edit an image using one to five input images.")
    _add_prompt_args(edit)
    edit.add_argument("--image", action="append", required=True)
    _add_image_args(edit)
    _add_auth_args(edit)
    edit.set_defaults(func=_cmd_edit)

    batch = subparsers.add_parser("batch", help="Run image jobs from a JSONL file.")
    batch.add_argument("--input", required=True)
    batch.add_argument("--out-dir", required=True)
    batch.add_argument("--force", action="store_true")
    batch.add_argument("--fail-fast", action="store_true")
    _add_image_args(batch)
    _add_auth_args(batch)
    batch.set_defaults(func=_cmd_batch)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CliError as exc:
        _die(str(exc))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
