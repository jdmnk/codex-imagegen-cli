from __future__ import annotations

import base64
import json
import time

import pytest

from codex_imagegen_cli import cli
from codex_imagegen_cli.cli import main


PNG_BYTES = b"\x89PNG\r\n\x1a\n"
VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def _jwt(payload: dict[str, object]) -> str:
    def enc(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc(payload)}.sig"


def _auth_file(tmp_path, access_token: str = "token"):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": "refresh",
                    "account_id": "acct_123",
                    "id_token": {"chatgpt_account_is_fedramp": False},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _sse_image_result(image_bytes: bytes = PNG_BYTES):
    yield (
        "response.output_item.done",
        json.dumps(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "image_generation_call",
                    "status": "completed",
                    "result": base64.b64encode(image_bytes).decode("ascii"),
                },
            }
        ),
    )


def _sse_rate_limit_result():
    yield (
        "response.failed",
        json.dumps(
            {
                "type": "response.failed",
                "response": {
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Rate limit reached. Please try again in 15ms.",
                    }
                },
            }
        ),
    )


def _rate_limit_event(message: str):
    return {
        "type": "response.failed",
        "response": {
            "error": {
                "code": "rate_limit_exceeded",
                "message": message,
            }
        },
    }


def test_generate_dry_run_prints_codex_request(tmp_path, capsys):
    output = tmp_path / "output" / "mug.png"

    code = main(
        [
            "generate",
            "--prompt",
            "A studio photo of a mug",
            "--out",
            str(output),
            "--size",
            "1536x1024",
            "--quality",
            "high",
            "--background",
            "opaque",
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["method"] == "POST"
    assert payload["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert payload["headers"]["Authorization"] == "Bearer <redacted>"
    assert payload["payload"]["input"][0]["content"][0]["text"] == "A studio photo of a mug"
    assert payload["payload"]["tools"] == [
        {
            "type": "image_generation",
            "output_format": "png",
            "size": "1536x1024",
            "quality": "high",
            "background": "opaque",
        }
    ]
    assert payload["payload"]["tool_choice"] == {"type": "image_generation"}
    assert payload["outputs"] == [str(output)]


def test_generate_dry_run_prints_multiple_direct_outputs(tmp_path, capsys):
    output = tmp_path / "output" / "shield.webp"

    code = main(
        [
            "generate",
            "--prompt",
            "A bronze shield icon",
            "--out",
            str(output),
            "--model",
            "gpt-test",
            "--n",
            "3",
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["outputs"] == [
        str(tmp_path / "output" / "shield-1.webp"),
        str(tmp_path / "output" / "shield-2.webp"),
        str(tmp_path / "output" / "shield-3.webp"),
    ]


def test_edit_dry_run_redacts_direct_input_images(tmp_path, capsys):
    source = tmp_path / "source.png"
    source.write_bytes(PNG_BYTES)
    output = tmp_path / "output.png"

    code = main(
        [
            "edit",
            "--image",
            str(source),
            "--prompt",
            "Make it blue",
            "--out",
            str(output),
            "--model",
            "gpt-test",
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert payload["payload"]["input"][0]["content"][1]["image_url"] == "data:image/png;base64,<redacted>"
    assert payload["payload"]["tools"][0]["background"] == "auto"


def test_edit_dry_run_compacts_valid_input_image_to_webp(tmp_path, capsys):
    source = tmp_path / "source.png"
    source.write_bytes(VALID_PNG_BYTES)
    output = tmp_path / "output.png"

    code = main(
        [
            "edit",
            "--image",
            str(source),
            "--prompt",
            "Make it blue",
            "--out",
            str(output),
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["payload"]["input"][0]["content"][1]["image_url"] == "data:image/webp;base64,<redacted>"


def test_model_default_prefers_env_before_codex_config(tmp_path, monkeypatch, capsys):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text('model = "gpt-config"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_IMAGEGEN_MODEL", "gpt-env")
    output = tmp_path / "output.png"

    code = main(
        [
            "generate",
            "--prompt",
            "A bronze shield icon",
            "--out",
            str(output),
            "--codex-home",
            str(codex_home),
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["payload"]["model"] == "gpt-env"


def test_generate_writes_backend_image(tmp_path, monkeypatch):
    auth_file = _auth_file(tmp_path)
    output = tmp_path / "mug.png"
    calls = []

    def fake_post_sse(url, *, headers, payload, timeout):
        calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        yield from _sse_image_result()

    monkeypatch.setattr(cli, "_post_sse", fake_post_sse)

    code = main(
        [
            "generate",
            "--prompt",
            "A studio photo of a mug",
            "--out",
            str(output),
            "--auth-file",
            str(auth_file),
            "--model",
            "gpt-test",
        ]
    )

    assert code == 0
    assert output.read_bytes() == PNG_BYTES
    assert calls[0]["url"].endswith("/responses")
    assert calls[0]["headers"]["Authorization"] == "Bearer token"
    assert calls[0]["headers"]["ChatGPT-Account-ID"] == "acct_123"
    assert calls[0]["headers"]["originator"] == "codex_cli_rs"
    assert calls[0]["payload"]["input"][0]["content"][0]["text"] == "A studio photo of a mug"
    assert calls[0]["payload"]["tools"][0] == {
        "type": "image_generation",
        "output_format": "png",
        "size": "auto",
        "quality": "auto",
        "background": "auto",
    }


def test_generate_converts_webp_output_from_file_extension(tmp_path, monkeypatch):
    auth_file = _auth_file(tmp_path)
    output = tmp_path / "mug.webp"

    def fake_post_sse(url, *, headers, payload, timeout):
        yield from _sse_image_result(VALID_PNG_BYTES)

    monkeypatch.setattr(cli, "_post_sse", fake_post_sse)

    code = main(
        [
            "generate",
            "--prompt",
            "A studio photo of a mug",
            "--out",
            str(output),
            "--auth-file",
            str(auth_file),
            "--webp-quality",
            "72",
        ]
    )

    data = output.read_bytes()
    assert code == 0
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WEBP"


def test_output_format_can_force_webp_with_png_extension(tmp_path, monkeypatch):
    auth_file = _auth_file(tmp_path)
    output = tmp_path / "mug.png"

    def fake_post_sse(url, *, headers, payload, timeout):
        yield from _sse_image_result(VALID_PNG_BYTES)

    monkeypatch.setattr(cli, "_post_sse", fake_post_sse)

    code = main(
        [
            "generate",
            "--prompt",
            "A studio photo of a mug",
            "--out",
            str(output),
            "--auth-file",
            str(auth_file),
            "--output-format",
            "webp",
        ]
    )

    data = output.read_bytes()
    assert code == 0
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WEBP"


def test_direct_backend_retries_streamed_rate_limit(tmp_path, monkeypatch):
    auth_file = _auth_file(tmp_path)
    output = tmp_path / "mug.png"
    calls = []

    def fake_post_sse(url, *, headers, payload, timeout):
        calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        if len(calls) == 1:
            yield from _sse_rate_limit_result()
        else:
            yield from _sse_image_result()

    monkeypatch.setattr(cli, "_post_sse", fake_post_sse)
    monkeypatch.setattr(cli.time, "sleep", lambda _delay: None)

    code = main(
        [
            "generate",
            "--prompt",
            "A studio photo of a mug",
            "--out",
            str(output),
            "--auth-file",
            str(auth_file),
            "--model",
            "gpt-test",
        ]
    )

    assert code == 0
    assert output.read_bytes() == PNG_BYTES
    assert len(calls) == 2


def test_input_image_rate_limit_waits_for_minute_window():
    event = _rate_limit_event(
        "Rate limit reached for gpt-image-2-codex (for limit gpt-image) "
        "in organization org on input-images per min: Limit 4000, Used 4000, Requested 1. "
        "Please try again in 15ms."
    )

    assert cli._responses_image_retry_delay(event, attempt=0) == 65.0
    assert cli._responses_image_retry_delay(event, attempt=1) == 130.0
    assert cli._responses_image_retry_delay(event, attempt=2) == 260.0
    assert cli._responses_image_retry_delay(event, attempt=3) == 300.0


def test_input_image_rate_limit_retry_reason_is_specific():
    event = _rate_limit_event(
        "Rate limit reached for gpt-image-2-codex (for limit gpt-image) "
        "in organization org on input-images per min: Limit 4000, Used 4000, Requested 1. "
        "Please try again in 15ms."
    )

    decision = cli._responses_image_retry_decision(event, attempt=0)

    assert decision == cli.RetryDecision(65.0, "input-image quota full")


def test_streamed_image_failure_message_is_concise():
    event = _rate_limit_event("Rate limit reached. Please try again later.")

    err = cli.ResponsesImageGenerationError(event)

    assert str(err) == (
        "Responses image generation failed (rate_limit_exceeded): "
        "Rate limit reached. Please try again later."
    )


def test_post_sse_wraps_low_level_transport_error(monkeypatch):
    def fake_urlopen(req, timeout):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(cli.request, "urlopen", fake_urlopen)

    with pytest.raises(cli.TransportError) as exc:
        list(cli._post_sse("https://example.test/responses", headers={}, payload={}, timeout=1))

    assert str(exc.value) == "Request failed while reading streamed response: [Errno 32] Broken pipe"


def test_stream_transport_error_is_user_facing(tmp_path, monkeypatch, capsys):
    auth_file = _auth_file(tmp_path)
    output = tmp_path / "mug.png"

    def fake_post_sse(url, *, headers, payload, timeout):
        raise cli.TransportError("Request failed while reading streamed response: [Errno 32] Broken pipe")
        yield

    monkeypatch.setattr(cli, "_post_sse", fake_post_sse)

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "generate",
                "--prompt",
                "A studio photo of a mug",
                "--out",
                str(output),
                "--auth-file",
                str(auth_file),
            ]
        )

    captured = capsys.readouterr()
    assert exc.value.code == 1
    assert "Error: Request failed while reading streamed response: [Errno 32] Broken pipe" in captured.err


def test_edit_encodes_input_image(tmp_path, monkeypatch):
    auth_file = _auth_file(tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(PNG_BYTES)
    output = tmp_path / "edited.png"
    seen = {}

    def fake_post_sse(url, *, headers, payload, timeout):
        seen["url"] = url
        seen["payload"] = payload
        yield from _sse_image_result()

    monkeypatch.setattr(cli, "_post_sse", fake_post_sse)

    code = main(
        [
            "edit",
            "--image",
            str(source),
            "--prompt",
            "Replace the background",
            "--out",
            str(output),
            "--auth-file",
            str(auth_file),
            "--model",
            "gpt-test",
        ]
    )

    assert code == 0
    assert seen["url"].endswith("/responses")
    assert seen["payload"]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert seen["payload"]["tools"][0]["type"] == "image_generation"
    assert output.read_bytes() == PNG_BYTES


def test_edit_can_use_style_image_without_prompt(tmp_path, monkeypatch):
    auth_file = _auth_file(tmp_path)
    source = tmp_path / "source.png"
    style = tmp_path / "style.png"
    source.write_bytes(PNG_BYTES)
    style.write_bytes(PNG_BYTES)
    output = tmp_path / "styled.png"
    seen = {}

    def fake_post_sse(url, *, headers, payload, timeout):
        seen["payload"] = payload
        yield from _sse_image_result()

    monkeypatch.setattr(cli, "_post_sse", fake_post_sse)

    code = main(
        [
            "edit",
            "--image",
            str(source),
            "--style-image",
            str(style),
            "--out",
            str(output),
            "--auth-file",
            str(auth_file),
        ]
    )

    content = seen["payload"]["input"][0]["content"]
    assert code == 0
    assert content[0]["type"] == "input_text"
    assert "Preserve its main subject" in content[0]["text"]
    assert "Use the final input image only as a style reference" in content[0]["text"]
    assert content[1]["type"] == "input_image"
    assert content[2]["type"] == "input_image"
    assert output.read_bytes() == PNG_BYTES


def test_edit_style_image_keeps_prompt_as_extra_guidance(tmp_path, monkeypatch):
    auth_file = _auth_file(tmp_path)
    source = tmp_path / "source.png"
    style = tmp_path / "style.png"
    source.write_bytes(PNG_BYTES)
    style.write_bytes(PNG_BYTES)
    output = tmp_path / "styled.png"
    seen = {}

    def fake_post_sse(url, *, headers, payload, timeout):
        seen["payload"] = payload
        yield from _sse_image_result()

    monkeypatch.setattr(cli, "_post_sse", fake_post_sse)

    code = main(
        [
            "edit",
            "--image",
            str(source),
            "--style-image",
            str(style),
            "--prompt",
            "Keep the background bright.",
            "--out",
            str(output),
            "--auth-file",
            str(auth_file),
        ]
    )

    text = seen["payload"]["input"][0]["content"][0]["text"]
    assert code == 0
    assert "Additional instruction: Keep the background bright." in text


def test_edit_resizes_large_input_image_before_upload(tmp_path, monkeypatch):
    auth_file = _auth_file(tmp_path)
    source = tmp_path / "source.png"
    image_cls = cli.Image
    image = image_cls.new("RGB", (2000, 1000), (255, 0, 0))
    image.save(source, "PNG")
    output = tmp_path / "edited.png"
    seen = {}

    def fake_post_sse(url, *, headers, payload, timeout):
        seen["payload"] = payload
        yield from _sse_image_result()

    monkeypatch.setattr(cli, "_post_sse", fake_post_sse)

    code = main(
        [
            "edit",
            "--image",
            str(source),
            "--prompt",
            "Replace the background",
            "--out",
            str(output),
            "--auth-file",
            str(auth_file),
            "--input-max-edge",
            "1000",
        ]
    )

    image_url = seen["payload"]["input"][0]["content"][1]["image_url"]
    _, encoded = image_url.split(";base64,", 1)
    with image_cls.open(cli.BytesIO(base64.b64decode(encoded))) as compacted:
        size = compacted.size

    assert code == 0
    assert image_url.startswith("data:image/webp;base64,")
    assert size == (1000, 500)


def test_expired_token_is_refreshed(tmp_path, monkeypatch):
    expired = _jwt({"exp": int(time.time()) - 10})
    fresh = _jwt({"exp": int(time.time()) + 3600})
    auth_file = _auth_file(tmp_path, access_token=expired)
    output = tmp_path / "out.png"
    auth_headers = []

    def fake_post_json(url, *, headers=None, payload, timeout):
        if url == cli.DEFAULT_REFRESH_URL:
            return {"access_token": fresh, "refresh_token": "refresh2"}
        raise AssertionError("image generation should use streamed responses")

    def fake_post_sse(url, *, headers, payload, timeout):
        auth_headers.append(headers["Authorization"])
        yield from _sse_image_result()

    monkeypatch.setattr(cli, "_post_json", fake_post_json)
    monkeypatch.setattr(cli, "_post_sse", fake_post_sse)

    code = main(
        [
            "generate",
            "--prompt",
            "A mug",
            "--out",
            str(output),
            "--auth-file",
            str(auth_file),
            "--model",
            "gpt-test",
        ]
    )

    saved_auth = json.loads(auth_file.read_text(encoding="utf-8"))
    assert code == 0
    assert auth_headers == [f"Bearer {fresh}"]
    assert saved_auth["tokens"]["access_token"] == fresh
    assert saved_auth["tokens"]["refresh_token"] == "refresh2"


def test_batch_accepts_jsonl_string_jobs(tmp_path, capsys):
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text('"A compact shuttle parked in a hangar"\n', encoding="utf-8")

    code = main(
        [
            "batch",
            "--input",
            str(jobs),
            "--out-dir",
            str(tmp_path / "generated"),
            "--model",
            "gpt-test",
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["payload"]["input"][0]["content"][0]["text"] == "A compact shuttle parked in a hangar"
    assert payload["outputs"][0].endswith("001-a-compact-shuttle-parked-in-a-hangar.png")


def test_size_rejects_unknown_value(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "generate",
                "--prompt",
                "A mug",
                "--out",
                str(tmp_path / "mug.png"),
                "--size",
                "2048x2048",
                "--dry-run",
            ]
        )

    assert exc.value.code == 2


def test_webp_quality_rejects_out_of_range(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "generate",
                "--prompt",
                "A mug",
                "--out",
                str(tmp_path / "mug.webp"),
                "--webp-quality",
                "101",
                "--dry-run",
            ]
        )

    captured = capsys.readouterr()
    assert exc.value.code == 1
    assert "--webp-quality must be between 1 and 100" in captured.err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert captured.out.strip().startswith("codex-imagegen ")
