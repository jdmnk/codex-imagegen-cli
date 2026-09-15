from __future__ import annotations

import argparse
import base64
import codecs
import copy
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib import error, request

from PIL import Image, ImageOps

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

from codex_imagegen_cli import __version__

DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_REFRESH_URL = "https://auth.openai.com/oauth/token"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_CODEX_MODEL = "gpt-5.5"
# Match Codex request format; the backend does not report the actual image model.
NATIVE_REQUEST_MODEL = "gpt-image-2"
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_EDIT_IMAGES = 5
DEFAULT_ORIGINATOR = "codex_cli_rs"
OUTPUT_FORMAT_CHOICES = ("auto", "png", "webp")
MAX_RESPONSES_IMAGE_RETRIES = 4
INPUT_IMAGE_RATE_LIMIT_DELAYS = (65.0, 130.0, 260.0, 300.0)
DEFAULT_INPUT_MAX_EDGE = 1536
DEFAULT_INPUT_WEBP_QUALITY = 90


class CliError(Exception):
    """Expected user-facing CLI failure."""


class HttpError(CliError):
    def __init__(self, status: int, body: str, retry_after: str | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}: {body[:800]}")
        self.status = status
        self.body = body


class TransportError(CliError):
    """Network/socket failure before a complete API response was received."""


class ResponsesImageGenerationError(CliError):
    def __init__(self, event: dict[str, Any]) -> None:
        self.event = event
        error_obj = _responses_image_error(event)
        code = error_obj.get("code") if error_obj else None
        message = error_obj.get("message") if error_obj else None
        if isinstance(code, str) and isinstance(message, str):
            super().__init__(f"Responses image generation failed ({code}): {message}")
        elif isinstance(message, str):
            super().__init__(f"Responses image generation failed: {message}")
        else:
            super().__init__(f"Responses image generation failed: {event}")


@dataclass(frozen=True)
class RetryDecision:
    delay: float
    reason: str


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _read_prompt(prompt: str | None, prompt_file: str | None, cd: Path) -> str:
    text = _read_optional_prompt(prompt, prompt_file, cd)
    if text is None:
        raise CliError("Missing prompt. Use --prompt or --prompt-file.")
    return text


def _read_optional_prompt(prompt: str | None, prompt_file: str | None, cd: Path) -> str | None:
    if prompt is not None and prompt_file is not None:
        raise CliError("Use --prompt or --prompt-file, not both.")
    if prompt_file is not None:
        text = _read_text(_resolve_path(prompt_file, cd), "Prompt file").strip()
    elif prompt is not None:
        text = prompt.strip()
    else:
        return None
    if not text:
        raise CliError("Prompt is empty.")
    return text


def _read_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CliError(f"{label} could not be read: {path} ({exc})") from exc


def _style_transfer_prompt(extra_prompt: str | None = None) -> str:
    prompt = (
        "Create a new version of the first input image. Preserve its main subject, identity, "
        "composition, proportions, and important details. Use the final input image only as a style "
        "reference: apply its visual medium, rendering approach, color palette, lighting, texture, "
        "finish, and mood. Do not copy the style reference's subject or scene content."
    )
    if extra_prompt:
        prompt += f"\n\nAdditional instruction: {extra_prompt}"
    return prompt


def _resolve_cd(raw_cd: str | None) -> Path:
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


def _load_jobs_jsonl(path: str, cd: Path) -> list[dict[str, Any]]:
    input_path = _resolve_path(path, cd)
    if not input_path.exists():
        raise CliError(f"Batch input not found: {input_path}")
    jobs: list[dict[str, Any]] = []
    for line_no, raw in enumerate(_read_text(input_path, "Batch input").splitlines(), start=1):
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
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise CliError(f"Line {line_no} is missing a non-empty prompt.")
        images = item.get("images", [])
        if images is None:
            images = []
        if not isinstance(images, list) or not all(isinstance(v, str) for v in images):
            raise CliError(f"Line {line_no} images must be a list of strings.")
        if "out" in item and (not isinstance(item["out"], str) or not item["out"].strip()):
            raise CliError(f"Line {line_no} out must be a non-empty string.")
        jobs.append({**item, "prompt": prompt.strip(), "images": images})
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
            f"Codex auth file not found: {auth_file}. This CLI requires file-based ChatGPT auth; "
            'keyring and ephemeral credentials are not supported. Set cli_auth_credentials_store = "file" '
            "in Codex config before logging in, or use --auth-file with an existing auth.json."
        )
    try:
        data = json.loads(_read_text(auth_file, "Codex auth file"))
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
        raise CliError(
            "Codex ChatGPT access token not found. Run `codex login` and choose ChatGPT."
        )
    return access_token


def _account_id(auth: dict[str, Any]) -> str | None:
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
    headers: dict[str, str] | None = None,
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
        raise HttpError(
            exc.code, raw, exc.headers.get("Retry-After") if exc.headers else None
        ) from exc
    except error.URLError as exc:
        raise TransportError(f"Request failed: {exc.reason}") from exc
    except (OSError, HTTPException, UnicodeError) as exc:
        raise TransportError(f"Request failed: {exc}") from exc
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError("Response was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise CliError("Response JSON was not an object.")
    return parsed


def _post_sse(url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float):
    request_headers = {**headers, "Accept": "text/event-stream", "Content-Type": "application/json"}
    req = request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=request_headers, method="POST"
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            decoder = codecs.getincrementaldecoder("utf-8")()
            pending = ""
            block = []
            # read1 avoids waiting for 4096 bytes before yielding a small event.
            read = getattr(response, "read1", response.read)
            while True:
                chunk = read(4096)
                pending += decoder.decode(chunk, final=not chunk)
                while True:
                    match = re.search(r"[\r\n]", pending)
                    if match is None:
                        break
                    idx = match.start()
                    if pending[idx:] == "\r" and chunk:
                        break  # CRLF may straddle chunks.
                    width = 2 if pending[idx : idx + 2] == "\r\n" else 1
                    line, pending = pending[:idx], pending[idx + width :]
                    if line:
                        block.append(line)
                    else:
                        parsed = _parse_sse_block("\n".join(block))
                        block = []
                        if parsed is not None:
                            yield parsed
                if not chunk:
                    if pending:
                        block.append(pending)
                    parsed = _parse_sse_block("\n".join(block))
                    if parsed is not None:
                        yield parsed
                    break
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise HttpError(
            exc.code, raw, exc.headers.get("Retry-After") if exc.headers else None
        ) from exc
    except error.URLError as exc:
        raise TransportError(
            f"Request failed while reading streamed response: {exc.reason}"
        ) from exc
    except (OSError, HTTPException, UnicodeError) as exc:
        raise TransportError(f"Request failed while reading streamed response: {exc}") from exc


def _parse_sse_block(block: str) -> tuple[str | None, str] | None:
    event = None
    data_lines: list[str] = []
    for raw in block.splitlines():
        if raw.startswith("event:"):
            event = raw.split(":", 1)[1].strip()
        elif raw.startswith("data:"):
            data_lines.append(raw.split(":", 1)[1].lstrip())
    if not event and not data_lines:
        return None
    return event, "\n".join(data_lines)


def _refresh_auth(auth: dict[str, Any], auth_file: Path, *, timeout: float) -> dict[str, Any]:
    # A separate lock survives atomic auth.json replacement. Codex itself does not
    # share this lock, so also re-read before and after the network request.
    with _auth_refresh_lock(auth_file, timeout):
        existed = auth_file.exists()
        current = _load_auth(auth_file) if existed else auth
        if _account_id(current) != _account_id(auth):
            raise CliError("Codex account changed during this request. Run the command again.")
        if current.get("tokens") != auth.get("tokens") and not _token_is_expiring(
            _access_token(current)
        ):
            return current
        updated = copy.deepcopy(current)
        tokens = updated.get("tokens")
        if not isinstance(tokens, dict):
            raise CliError("Codex ChatGPT auth not found. Run `codex login` and choose ChatGPT.")
        refresh_token = tokens.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise CliError("Codex refresh token not found. Run `codex login` again.")
        payload = {
            "client_id": CODEX_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        refresh_url = os.environ.get("CODEX_IMAGEGEN_REFRESH_URL", DEFAULT_REFRESH_URL)
        try:
            refreshed = _post_json(refresh_url, payload=payload, timeout=timeout)
        except HttpError as exc:
            # OAuth error bodies can contain credentials; never echo them.
            raise CliError(
                f"Codex token refresh failed (HTTP {exc.status}). Run `codex login` again."
            ) from exc
        access = refreshed.get("access_token")
        if not isinstance(access, str) or not access:
            raise CliError(
                "Codex token refresh returned no access token; auth file was not changed."
            )
        for key in ("access_token", "refresh_token", "id_token"):
            value = refreshed.get(key)
            if isinstance(value, str) and value:
                tokens[key] = value
        # Repair a legacy dictionary only when its original JWT is available.
        if isinstance(tokens.get("id_token"), dict):
            tokens["id_token"] = tokens["id_token"].get("raw_jwt")
        if not isinstance(tokens.get("id_token"), str) or not tokens["id_token"]:
            raise CliError("Codex ID token is invalid. Run `codex login` again.")
        updated["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if (existed and not auth_file.exists()) or (
            auth_file.exists() and _load_auth(auth_file) != current
        ):
            raise CliError(
                "Codex auth changed while refreshing; refusing to overwrite it. Run the command again."
            )
        _atomic_write(auth_file, (json.dumps(updated, indent=2) + "\n").encode(), replace=True)
        return updated


@contextmanager
def _auth_refresh_lock(auth_file: Path, timeout: float):
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = auth_file.with_name(auth_file.name + ".imagegen.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    with os.fdopen(fd, "r+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock_path.stat().st_size == 0:
                lock.write(b"0")
                lock.flush()
        else:
            import fcntl
        deadline = time.monotonic() + min(timeout, 30.0)
        while True:
            try:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise CliError("Timed out waiting for another imagegen auth refresh.") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, data: bytes, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # Atomic no-clobber publication: an output created during generation
            # must not be overwritten, even after the earlier preflight check.
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise CliError(
                    f"Output already exists: {path} (use --force to allow replacement)"
                ) from exc
    finally:
        Path(temporary).unlink(missing_ok=True)


def _load_ready_auth(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    auth_file = _auth_file(args)
    auth = _load_auth(auth_file)
    if _token_is_expiring(_access_token(auth)):
        auth = _refresh_auth(auth, auth_file, timeout=args.timeout)
    return auth, auth_file


def _data_url_for_image(path: Path, args: argparse.Namespace) -> str:
    if not path.exists():
        raise CliError(f"Image file not found: {path}")
    if not path.is_file():
        raise CliError(f"Image path is not a file: {path}")
    image_bytes, mime_type = _encoded_input_image(path, args)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _encoded_input_image(path: Path, args: argparse.Namespace) -> tuple[bytes, str]:
    try:
        with Image.open(path) as source:
            source.load()
            image = _resize_input_image(ImageOps.exif_transpose(source), args.input_max_edge)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert(
                    "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
                )
            output = BytesIO()
            image.save(output, "WEBP", quality=args.input_webp_quality)
            return output.getvalue(), "image/webp"
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise CliError(f"Could not read/compact input image {path}: {exc}") from exc


def _resize_input_image(image: Image.Image, max_edge: int) -> Image.Image:
    if max_edge > 0:
        width, height = image.size
        edge = max(width, height)
        if edge > max_edge:
            scale = max_edge / float(edge)
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            image = image.resize(size, Image.Resampling.LANCZOS)
    return image.copy()


def _codex_config_file(args: argparse.Namespace) -> Path:
    return _codex_home(args) / "config.toml"


def _default_model(args: argparse.Namespace) -> str:
    if args.model:
        return args.model
    env_model = os.environ.get("CODEX_IMAGEGEN_MODEL")
    if env_model:
        return env_model
    config_file = _codex_config_file(args)
    config = {}
    if config_file.exists():
        try:
            config = tomllib.loads(_read_text(config_file, "Codex config"))
        except tomllib.TOMLDecodeError as exc:
            raise CliError(f"Invalid Codex config TOML: {config_file} ({exc})") from exc
    profile = getattr(args, "profile", None) or config.get("profile")
    model = config.get("model", DEFAULT_CODEX_MODEL)
    if profile:
        profiles = config.get("profiles", {})
        if (
            not isinstance(profile, str)
            or not isinstance(profiles, dict)
            or profile not in profiles
        ):
            raise CliError(f"Unknown Codex profile: {profile}")
        settings = profiles[profile]
        if not isinstance(settings, dict):
            raise CliError(f"Invalid Codex profile: {profile}")
        model = settings.get("model", model)
    if not isinstance(model, str) or not model.strip():
        raise CliError("Codex model must be a non-empty string.")
    return model


def _native_payload(
    *, prompt: str, args: argparse.Namespace, mode: str, images: Sequence[Path] | None = None
) -> dict[str, Any]:
    payload = {
        "model": NATIVE_REQUEST_MODEL,
        "prompt": prompt,
        "size": args.size,
        "quality": args.quality,
        "background": args.background,
    }
    if mode == "edit":
        if not images or len(images) > MAX_EDIT_IMAGES:
            raise CliError(f"Edit supports one to {MAX_EDIT_IMAGES} images.")
        payload["images"] = [{"image_url": _data_url_for_image(path, args)} for path in images]
    return payload


def _responses_payload(
    *,
    prompt: str,
    args: argparse.Namespace,
    mode: str,
    images: Sequence[Path] | None = None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    if images:
        if len(images) > MAX_EDIT_IMAGES:
            raise CliError(f"Edit supports at most {MAX_EDIT_IMAGES} images.")
        for image in images:
            content.append({"type": "input_image", "image_url": _data_url_for_image(image, args)})
    image_tool = {
        "type": "image_generation",
        "output_format": "png",
        "size": args.size,
        "quality": args.quality,
        "background": args.background,
    }
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
        "tools": [image_tool],
        "tool_choice": {"type": "image_generation"},
        "parallel_tool_calls": False,
        "reasoning": None,
        "store": False,
        "stream": True,
        "include": [],
        "prompt_cache_key": "codex-imagegen-cli",
        "client_metadata": {"x-codex-installation-id": "codex-imagegen-cli"},
    }


def _endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _write_response_image(
    encoded: str,
    output_path: Path,
    *,
    force: bool,
    output_format: str,
    webp_quality: int,
    requested_size: str = "auto",
    size_policy: str = "warn",
) -> Path:
    _check_output(output_path, force)
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise CliError("Image generation result exceeds the 32 MiB limit.")
    try:
        image_bytes = base64.b64decode(encoded.strip(), validate=True)
    except ValueError as exc:
        raise CliError("Image generation result was not valid base64.") from exc
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            if image.format != "PNG":
                raise CliError(f"Expected PNG image data, received {image.format}.")
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            actual_size = f"{image.width}x{image.height}"
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise CliError(f"Image generation returned an invalid image: {exc}") from exc
    if requested_size != "auto" and requested_size != actual_size:
        message = f"Requested {requested_size}, backend returned {actual_size} for {output_path}."
        if size_policy == "error":
            raise CliError(message + " Output was not written (--size-policy error).")
        _warn(
            message
            + " Saving the original dimensions; use --size-policy error to reject mismatches."
        )
    _write_image_bytes(
        image_bytes,
        output_path,
        output_format=output_format,
        webp_quality=webp_quality,
        force=force,
    )
    _log(f"  saved {actual_size} {_resolve_output_format(output_path, output_format).upper()}")
    return output_path


def _write_image_bytes(
    image_bytes: bytes,
    output_path: Path,
    *,
    output_format: str,
    webp_quality: int,
    force: bool = False,
) -> None:
    if _resolve_output_format(output_path, output_format) == "webp":
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                output = BytesIO()
                image.save(output, "WEBP", quality=webp_quality)
                image_bytes = output.getvalue()
        except (OSError, ValueError) as exc:
            raise CliError(f"Failed to convert image to WebP: {exc}") from exc
    _atomic_write(output_path, image_bytes, replace=force)


def _resolve_output_format(output_path: Path, output_format: str) -> str:
    if output_format != "auto":
        return output_format
    return "webp" if output_path.suffix.lower() == ".webp" else "png"


def _output_paths(output_path: Path, count: int) -> list[Path]:
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
    headers: dict[str, str] | None,
    payload: dict[str, Any],
    output_path: Path,
    output_count: int | None = None,
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


def _call_responses_backend(
    *,
    args: argparse.Namespace,
    mode: str,
    prompt: str,
    output_path: Path,
    image_paths: Sequence[Path] | None = None,
) -> list[Path]:
    return _call_backend(
        args=args,
        output_path=output_path,
        url=_endpoint(args.base_url, "responses"),
        payload=_responses_payload(prompt=prompt, args=args, mode=mode, images=image_paths),
        native=False,
    )


def _call_native_backend(
    *,
    args: argparse.Namespace,
    mode: str,
    prompt: str,
    output_path: Path,
    image_paths: Sequence[Path] | None = None,
) -> list[Path]:
    return _call_backend(
        args=args,
        output_path=output_path,
        url=_endpoint(args.base_url, "images/edits" if mode == "edit" else "images/generations"),
        payload=_native_payload(prompt=prompt, args=args, mode=mode, images=image_paths),
        native=True,
    )


def _native_image_result(
    url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> str:
    response = _post_json(url, headers=headers, payload=payload, timeout=timeout)
    data = response.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise CliError("Native image response must contain exactly one image in data.")
    encoded = data[0].get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise CliError("Native image response contains no base64 image result.")
    return encoded


def _call_backend(
    *, args: argparse.Namespace, output_path: Path, url: str, payload: dict[str, Any], native: bool
) -> list[Path]:
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
    saved = []
    call = _native_image_result if native else _stream_image_result
    for idx, path in enumerate(output_paths, start=1):
        if _token_is_expiring(_access_token(auth)):
            auth = _refresh_auth(auth, auth_file, timeout=args.timeout)
            headers = _auth_headers(auth)
        if args.n > 1:
            _log(f"  image {idx}/{args.n} ...")
        attempt = 0
        refreshed = False
        while True:
            try:
                encoded = call(url, headers=headers, payload=payload, timeout=args.timeout)
                break
            except HttpError as exc:
                if exc.status == 401 and not refreshed:
                    auth = _refresh_auth(auth, auth_file, timeout=args.timeout)
                    headers = _auth_headers(auth)
                    refreshed = True
                    continue
                retry = _http_retry_decision(exc, attempt)
                if retry is None or attempt >= MAX_RESPONSES_IMAGE_RETRIES:
                    if exc.status == 404 and not native and "model_not_found" in exc.body:
                        raise CliError(
                            str(exc)
                            + " Select an accessible --model with --backend responses, or use --backend native."
                        ) from exc
                    raise
            except ResponsesImageGenerationError as exc:
                retry = _responses_image_retry_decision(exc.event, attempt)
                if retry is None or attempt >= MAX_RESPONSES_IMAGE_RETRIES:
                    raise
            # Transport failures and ambiguous server errors are deliberately not
            # replayed: an image may already have consumed account usage.
            attempt += 1
            _warn(
                f"{retry.reason}; retrying in {retry.delay:.1f}s ({attempt}/{MAX_RESPONSES_IMAGE_RETRIES})"
            )
            time.sleep(retry.delay)
        saved.append(
            _write_response_image(
                encoded,
                path,
                force=args.force,
                output_format=args.output_format,
                webp_quality=args.webp_quality,
                requested_size=args.size,
                size_policy=args.size_policy,
            )
        )
        # Publish each successful path immediately, even if a later image fails.
        print(str(path), flush=True)
    return saved


def _http_retry_decision(exc: HttpError, attempt: int) -> RetryDecision | None:
    if exc.status != 429:
        return None
    try:
        body = json.loads(exc.body)
    except ValueError:
        body = {}
    error_obj = body.get("error", {}) if isinstance(body, dict) else {}
    if isinstance(error_obj, dict) and (
        error_obj.get("code") not in {None, "rate_limit_exceeded"}
        or error_obj.get("type") in {"image_generation_user_error", "insufficient_quota"}
    ):
        return None  # Quota/billing/user errors need intervention, not a retry.
    decision = _responses_image_retry_decision(
        {
            "response": {
                "error": {
                    **(error_obj if isinstance(error_obj, dict) else {}),
                    "code": "rate_limit_exceeded",
                }
            }
        },
        attempt,
    )
    if decision is None:
        return None
    delay = decision.delay
    if exc.retry_after:
        try:
            wait = float(exc.retry_after)
        except ValueError:
            try:
                wait = parsedate_to_datetime(exc.retry_after).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                wait = 0
        if not math.isfinite(wait):
            return None
        delay = max(delay, wait)
    # Keep server-supplied delays bounded too.
    if delay > 300:
        return None
    return RetryDecision(delay, decision.reason)


def _responses_image_retry_delay(event: dict[str, Any], attempt: int) -> float | None:
    decision = _responses_image_retry_decision(event, attempt)
    return decision.delay if decision is not None else None


def _responses_image_retry_decision(event: dict[str, Any], attempt: int) -> RetryDecision | None:
    error_obj = _responses_image_error(event)
    if not isinstance(error_obj, dict) or error_obj.get("code") != "rate_limit_exceeded":
        return None
    message = error_obj.get("message")
    parsed_delay = None
    if isinstance(message, str):
        if "input-images per min" in message and _rate_limit_bucket_exhausted(message):
            delay = INPUT_IMAGE_RATE_LIMIT_DELAYS[
                min(attempt, len(INPUT_IMAGE_RATE_LIMIT_DELAYS) - 1)
            ]
            return RetryDecision(delay, "input-image quota full")
        match = re.search(r"try again in\s+(\d+(?:\.\d+)?)\s*(ms|s)", message, re.IGNORECASE)
        if match:
            parsed_delay = float(match.group(1))
            if match.group(2).lower() == "ms":
                parsed_delay /= 1000.0
    backoff_delay = min(2.0**attempt, 16.0)
    if parsed_delay is None:
        return RetryDecision(backoff_delay, "image generation rate-limited")
    if not math.isfinite(parsed_delay) or parsed_delay > 300:
        return None
    return RetryDecision(max(parsed_delay, backoff_delay), "image generation rate-limited")


def _responses_image_error(event: dict[str, Any]) -> dict[str, Any] | None:
    response = event.get("response")
    error_obj = response.get("error") if isinstance(response, dict) else event.get("error", event)
    return error_obj if isinstance(error_obj, dict) else None


def _rate_limit_bucket_exhausted(message: str) -> bool:
    used_match = re.search(r"Used\s+(\d+(?:\.\d+)?)", message)
    limit_match = re.search(r"Limit\s+(\d+(?:\.\d+)?)", message)
    if not used_match or not limit_match:
        return False
    return float(used_match.group(1)) >= float(limit_match.group(1))


def _stream_image_result(
    url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> str:
    last_status = None
    stream = _post_sse(url, headers=headers, payload=payload, timeout=timeout)
    try:
        for _event, data in stream:
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError as exc:
                raise CliError("Responses stream contained invalid JSON.") from exc
            if not isinstance(event, dict):
                continue
            if event.get("type") in {"response.failed", "response.incomplete", "error"}:
                raise ResponsesImageGenerationError(event)
            items = [event.get("item")]
            if event.get("type") == "response.completed":
                response = event.get("response")
                if isinstance(response, dict) and isinstance(response.get("output"), list):
                    items.extend(response["output"])
            for item in items:
                if isinstance(item, dict) and item.get("type") == "image_generation_call":
                    last_status = item.get("status")
                    result = item.get("result")
                    if isinstance(result, str) and result and last_status == "completed":
                        return result
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()
    if last_status:
        raise CliError(
            f"Responses stream ended without an image result; last status was {last_status}."
        )
    raise CliError("Responses stream ended without an image generation result.")


def _call_image_backend(
    *,
    args: argparse.Namespace,
    mode: str,
    prompt: str,
    output_path: Path,
    image_paths: Sequence[Path] | None = None,
) -> list[Path]:
    backend = _call_native_backend if args.backend == "native" else _call_responses_backend
    return backend(
        args=args,
        mode=mode,
        prompt=prompt,
        output_path=output_path,
        image_paths=image_paths,
    )


def _run_one(
    *,
    args: argparse.Namespace,
    mode: str,
    prompt: str,
    output_path: Path,
    image_paths: Sequence[Path] | None = None,
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

    _call_image_backend(
        args=args,
        mode=mode,
        prompt=prompt,
        output_path=output_path,
        image_paths=image_paths,
    )

    if not args.dry_run:
        elapsed = time.monotonic() - t0
        _log(f"  done ({elapsed:.1f}s)")
    return True


def _add_auth_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cd", help="Base directory for resolving relative paths.")
    parser.add_argument(
        "--auth-file", help="Path to Codex auth.json. Defaults to $CODEX_HOME/auth.json."
    )
    parser.add_argument(
        "--codex-home", help="Codex home directory. Defaults to $CODEX_HOME or ~/.codex."
    )
    parser.add_argument(
        "--backend",
        choices=["native", "responses"],
        default="native",
        help="Image backend (default: native). The image model is controlled by the backend.",
    )
    parser.add_argument(
        "--profile", help="Codex profile for reasoning-model selection (responses backend only)."
    )
    parser.add_argument(
        "--model",
        "--reasoning-model",
        help=(
            "Reasoning model for --backend responses only; does not select the image model. "
            "Defaults to CODEX_IMAGEGEN_MODEL, "
            "then Codex config, then the built-in fallback."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CODEX_IMAGEGEN_BASE_URL", DEFAULT_BASE_URL),
        help="Codex backend base URL.",
    )
    parser.add_argument("--timeout", type=float, default=300.0, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the request without contacting Codex."
    )


def _add_image_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--background",
        choices=["auto", "transparent", "opaque"],
        default="auto",
        help="Direct background parameter.",
    )
    parser.add_argument(
        "--quality",
        choices=["auto", "low", "medium", "high"],
        default="auto",
        help="Direct quality parameter.",
    )
    parser.add_argument(
        "--size",
        type=_parse_size,
        default="auto",
        help="Requested image dimensions: auto or WIDTHxHEIGHT. Backend may return a different size.",
    )
    parser.add_argument(
        "--size-policy",
        choices=["warn", "error"],
        default="warn",
        help="On a dimension mismatch, warn and save (default) or fail without writing.",
    )
    parser.add_argument(
        "--output-format",
        choices=OUTPUT_FORMAT_CHOICES,
        default="auto",
        help="Output file format. Default: auto from --out extension (.webp writes WebP, otherwise PNG).",
    )
    parser.add_argument(
        "--webp-quality",
        type=int,
        default=85,
        help="WebP encoder quality from 1 to 100. Used only for WebP output.",
    )
    parser.add_argument(
        "--input-max-edge",
        type=int,
        default=DEFAULT_INPUT_MAX_EDGE,
        help="Resize edit input images so their longest edge is at most this many pixels. Use 0 to disable.",
    )
    parser.add_argument(
        "--input-webp-quality",
        type=int,
        default=DEFAULT_INPUT_WEBP_QUALITY,
        help="WebP quality from 1 to 100 for compacted edit input images.",
    )
    parser.add_argument("--n", type=int, default=1, help="Number of images to request.")


def _add_prompt_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--out", required=True)
    parser.add_argument("--force", action="store_true")


def _parse_size(value: str) -> str:
    if value == "auto":
        return value
    match = re.fullmatch(r"([1-9][0-9]{0,3})x([1-9][0-9]{0,3})", value)
    if match:
        width, height = map(int, match.groups())
        if (
            width % 16 == height % 16 == 0
            and max(width, height) <= 3840
            and max(width, height) <= 3 * min(width, height)
            and 655360 <= width * height <= 8294400
        ):
            return value
    raise argparse.ArgumentTypeError(
        "Size must be auto or WIDTHxHEIGHT: multiples of 16, "
        "at most 3840 per edge, 1:3 to 3:1 aspect, 655360–8294400 pixels."
    )


def _validate_common(args: argparse.Namespace) -> Path:
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise CliError("--timeout must be a positive finite number.")
    if args.backend == "native" and (args.model or args.profile):
        raise CliError(
            "--model/--reasoning-model and --profile require --backend responses. "
            "The image model is controlled by the backend."
        )
    if args.background == "transparent":
        raise CliError(
            "Transparent background is not supported by the current Codex image backend. "
            "Use --background auto or opaque."
        )
    if args.n < 1:
        raise CliError("--n must be at least 1.")
    if not 1 <= args.webp_quality <= 100:
        raise CliError("--webp-quality must be between 1 and 100.")
    if args.input_max_edge < 0:
        raise CliError("--input-max-edge must be 0 or greater.")
    if not 1 <= args.input_webp_quality <= 100:
        raise CliError("--input-webp-quality must be between 1 and 100.")
    return _resolve_cd(args.cd)


def _cmd_generate(args: argparse.Namespace) -> int:
    cd = _validate_common(args)
    prompt = _read_prompt(args.prompt, args.prompt_file, cd)
    output_path = _resolve_path(args.out, cd)
    _run_one(args=args, mode="generate", prompt=prompt, output_path=output_path)
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    cd = _validate_common(args)
    prompt = _read_optional_prompt(args.prompt, args.prompt_file, cd)
    if args.style_image:
        prompt = _style_transfer_prompt(prompt)
    elif prompt is None:
        raise CliError("Missing prompt. Use --prompt, --prompt-file, or --style-image.")
    output_path = _resolve_path(args.out, cd)
    image_paths = [_resolve_path(raw, cd) for raw in args.image]
    if args.style_image:
        image_paths.append(_resolve_path(args.style_image, cd))
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
        try:
            prompt = job["prompt"]
            raw_out = job.get("out")
            output_path = (
                _resolve_path(str(out_dir / raw_out), cd)
                if raw_out
                else out_dir / f"{idx:03d}-{_slugify(prompt)}.png"
            )
            if not output_path.is_relative_to(out_dir):
                raise CliError(f"Job {idx} output must stay under --out-dir.")
            images = [_resolve_path(raw, cd) for raw in job["images"]]
            mode = job.get("mode", "edit" if images else "generate")
            if mode not in ("generate", "edit"):
                raise CliError(f"Job {idx} has invalid mode: {mode}")
            if mode == "edit" and not images:
                raise CliError(f"Job {idx} mode is edit but images is empty.")
            if mode == "generate" and images:
                raise CliError(f"Job {idx} has images but mode is generate; use edit.")
            _run_one(
                args=args,
                mode=mode,
                prompt=prompt,
                output_path=output_path,
                image_paths=images,
                log_prefix=f"[{idx}/{len(jobs)}]",
            )
        except (CliError, OSError, UnicodeError) as exc:
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
    edit.add_argument(
        "--style-image",
        help=(
            "Use this image as a style reference for the edit. "
            "The --image inputs provide the content; --prompt becomes optional extra guidance."
        ),
    )
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CliError as exc:
        _die(str(exc))
    except (OSError, UnicodeError) as exc:
        _die(str(exc))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
