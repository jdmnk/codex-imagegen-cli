"""Regression coverage from the September 2026 audit; no live credentials/network."""

import base64
import json
from contextlib import contextmanager
from http.client import IncompleteRead
from io import BytesIO

import pytest
from PIL import Image

from codex_imagegen_cli import cli


def jwt(payload):
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"e30.{encoded}.signature"


def fake_auth():
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": jwt({"email": "test@example.invalid"}),
            "access_token": "fake-access",
            "refresh_token": "fake-refresh",
            "account_id": "fake-account",
        },
    }


def image_result():
    out = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode()


def args_for(tmp_path):
    return cli.build_parser().parse_args(
        [
            "generate",
            "--backend",
            "responses",
            "--prompt",
            "test",
            "--out",
            str(tmp_path / "out.png"),
            "--model",
            "test-model",
        ]
    )


def test_refresh_preserves_id_token_as_jwt_string(tmp_path, monkeypatch):
    new_id = jwt({"email": "test@example.invalid"})
    monkeypatch.setattr(
        cli,
        "_post_json",
        lambda *a, **kw: {
            "id_token": new_id,
            "access_token": "new-access",
            "refresh_token": "new-refresh",
        },
    )
    path = tmp_path / "auth.json"
    cli._refresh_auth(fake_auth(), path, timeout=1)
    assert json.loads(path.read_text())["tokens"]["id_token"] == new_id


def test_stream_accepts_crlf_event_separators(monkeypatch):
    events = [
        {"type": "response.created"},
        {
            "type": "response.output_item.done",
            "item": {
                "type": "image_generation_call",
                "status": "completed",
                "result": image_result(),
            },
        },
    ]
    body = "".join("data: " + json.dumps(e) + "\r\n\r\n" for e in events).encode()
    monkeypatch.setattr(cli.request, "urlopen", lambda *a, **kw: BytesIO(body))
    assert cli._stream_image_result("https://example.invalid", headers={}, payload={}, timeout=1)


def test_stream_accepts_result_in_completed_response(monkeypatch):
    encoded = image_result()
    event = {
        "type": "response.completed",
        "response": {
            "status": "completed",
            "output": [
                {
                    "type": "image_generation_call",
                    "status": "completed",
                    "result": encoded,
                }
            ],
        },
    }
    monkeypatch.setattr(cli, "_post_sse", lambda *a, **kw: iter([(None, json.dumps(event))]))
    assert (
        cli._stream_image_result("https://example.invalid", headers={}, payload={}, timeout=1)
        == encoded
    )


def test_truncated_http_response_has_cli_transport_error(monkeypatch):
    @contextmanager
    def broken(*a, **kw):
        raise IncompleteRead(b"partial", 100)
        yield

    monkeypatch.setattr(cli.request, "urlopen", broken)
    with pytest.raises(cli.TransportError):
        list(cli._post_sse("https://example.invalid", headers={}, payload={}, timeout=1))


@pytest.mark.parametrize("first_unauthorized", [False, True])
def test_rate_limit_retry_also_works_after_http_errors(tmp_path, monkeypatch, first_unauthorized):
    sequence = ([cli.HttpError(401, "expired")] if first_unauthorized else []) + [
        cli.ResponsesImageGenerationError(
            {
                "response": {
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Please try again in 1ms.",
                    }
                }
            }
        )
        if first_unauthorized
        else cli.HttpError(429, "rate limited"),
        image_result(),
    ]

    def stream(*a, **kw):
        result = sequence.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cli, "_stream_image_result", stream)
    monkeypatch.setattr(cli, "_load_ready_auth", lambda a: (fake_auth(), tmp_path / "auth.json"))
    monkeypatch.setattr(cli, "_refresh_auth", lambda *a, **kw: fake_auth())
    monkeypatch.setattr(cli, "_auth_headers", lambda a: {})
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    args = args_for(tmp_path)
    assert cli._call_responses_backend(
        args=args, mode="generate", prompt="test", output_path=tmp_path / "out.png"
    )
    assert not sequence


def test_batch_null_images_behaves_as_empty_list(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text('{"prompt":"test", "images":null}\n')
    monkeypatch.setattr(cli, "_run_one", lambda **kw: True)
    assert cli.main(["batch", "--input", str(jobs), "--out-dir", str(tmp_path)]) == 0


def test_batch_rejects_nontext_prompt(tmp_path):
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text('{"prompt":null}\n')
    with pytest.raises(cli.CliError):
        cli._load_jobs_jsonl(str(jobs), tmp_path)


def test_batch_invalid_job_does_not_abort_remaining_jobs(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text('{"prompt":"bad", "mode":"invalid"}\n{"prompt":"good"}\n')
    visited = []
    monkeypatch.setattr(cli, "_run_one", lambda **kw: visited.append(kw["prompt"]))
    try:
        cli.main(["batch", "--input", str(jobs), "--out-dir", str(tmp_path)])
    except SystemExit:
        pass
    assert visited == ["good"]


def test_config_does_not_use_model_from_inactive_profile(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text('[profiles.inactive]\nmodel = "unavailable-model"\n')
    args = args_for(tmp_path)
    args.model = None
    args.codex_home = str(tmp_path)
    monkeypatch.delenv("CODEX_IMAGEGEN_MODEL", raising=False)
    assert cli._default_model(args) == cli.DEFAULT_CODEX_MODEL


def test_compaction_applies_exif_orientation(tmp_path):
    source = tmp_path / "portrait.jpg"
    image = Image.new("RGB", (40, 20), "blue")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    encoded, _ = cli._encoded_input_image(source, args_for(tmp_path))
    with Image.open(BytesIO(encoded)) as output:
        assert output.size == (20, 40)


def test_corrupt_base64_does_not_create_successful_output(tmp_path):
    out = tmp_path / "out.png"
    with pytest.raises(cli.CliError):
        cli._write_response_image("!!!!", out, force=False, output_format="png", webp_quality=85)
    assert not out.exists()


def test_timeout_must_be_positive(tmp_path):
    args = args_for(tmp_path)
    args.timeout = -1
    with pytest.raises(cli.CliError):
        cli._validate_common(args)


def test_prompt_directory_is_a_user_facing_error(tmp_path):
    with pytest.raises(cli.CliError):
        cli._read_prompt(None, str(tmp_path), tmp_path)
