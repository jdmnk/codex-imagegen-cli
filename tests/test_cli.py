from __future__ import annotations

import base64
import json
import time

import pytest

from codex_imagegen_cli import cli
from codex_imagegen_cli.cli import main


PNG_BYTES = b"\x89PNG\r\n\x1a\n"


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


def test_generate_dry_run_prints_codex_request(tmp_path, capsys):
    output = tmp_path / "output" / "mug.png"

    code = main(
        [
            "generate",
            "--prompt",
            "A studio photo of a mug",
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
    assert payload["method"] == "POST"
    assert payload["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert payload["headers"]["Authorization"] == "Bearer <redacted>"
    assert payload["payload"]["model"] == "gpt-test"
    assert payload["payload"]["tools"] == [{"type": "image_generation", "output_format": "png"}]
    assert payload["payload"]["input"][0]["content"][0]["text"] == "A studio photo of a mug"
    assert payload["outputs"] == [str(output)]


def test_generate_writes_backend_image(tmp_path, monkeypatch):
    auth_file = _auth_file(tmp_path)
    output = tmp_path / "mug.png"
    calls = []

    def fake_post_sse(url, *, headers, payload, timeout):
        calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        yield (
            "response.output_item.done",
            json.dumps(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "image_generation_call",
                        "status": "completed",
                        "result": base64.b64encode(PNG_BYTES).decode("ascii"),
                    },
                }
            ),
        )

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


def test_edit_encodes_input_image(tmp_path, monkeypatch):
    auth_file = _auth_file(tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(PNG_BYTES)
    output = tmp_path / "edited.png"
    seen = {}

    def fake_post_sse(url, *, headers, payload, timeout):
        seen["url"] = url
        seen["payload"] = payload
        yield (
            "response.output_item.done",
            json.dumps(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "image_generation_call",
                        "status": "completed",
                        "result": base64.b64encode(PNG_BYTES).decode("ascii"),
                    },
                }
            ),
        )

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
    assert output.read_bytes() == PNG_BYTES


def test_expired_token_is_refreshed(tmp_path, monkeypatch):
    expired = _jwt({"exp": int(time.time()) - 10})
    fresh = _jwt({"exp": int(time.time()) + 3600})
    auth_file = _auth_file(tmp_path, access_token=expired)
    output = tmp_path / "out.png"
    auth_headers = []

    def fake_post_json(url, *, headers=None, payload, timeout):
        if url == cli.DEFAULT_REFRESH_URL:
            return {"access_token": fresh, "refresh_token": "refresh2"}

    def fake_post_sse(url, *, headers, payload, timeout):
        auth_headers.append(headers["Authorization"])
        yield (
            "response.output_item.done",
            json.dumps(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "image_generation_call",
                        "status": "completed",
                        "result": base64.b64encode(PNG_BYTES).decode("ascii"),
                    },
                }
            ),
        )

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


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert captured.out.strip().startswith("codex-imagegen ")
