"""Positive controls for the focused audit; all transport/auth data is synthetic."""

import json
from io import BytesIO

import pytest
from PIL import Image
from test_regressions import args_for, fake_auth, image_result

from codex_imagegen_cli import cli


def test_real_sse_reader_handles_lf_delimited_events(monkeypatch):
    encoded = image_result()
    event = {
        "type": "response.output_item.done",
        "item": {
            "type": "image_generation_call",
            "status": "completed",
            "result": encoded,
        },
    }
    data = (
        "data: "
        + json.dumps({"type": "response.created"})
        + "\n\n"
        + "data: "
        + json.dumps(event)
        + "\n\n"
    ).encode()
    monkeypatch.setattr(cli.request, "urlopen", lambda *a, **kw: BytesIO(data))
    assert (
        cli._stream_image_result("https://example.invalid", headers={}, payload={}, timeout=1)
        == encoded
    )


def test_multiple_outputs_are_valid_numbered_images(tmp_path, monkeypatch):
    calls = []

    def stream(*a, **kw):
        calls.append(kw["payload"])
        return image_result()

    monkeypatch.setattr(cli, "_stream_image_result", stream)
    monkeypatch.setattr(cli, "_load_ready_auth", lambda a: (fake_auth(), tmp_path / "auth.json"))
    monkeypatch.setattr(cli, "_auth_headers", lambda a: {})
    args = args_for(tmp_path)
    args.n = 2
    out = cli._call_responses_backend(
        args=args, mode="generate", prompt="test", output_path=tmp_path / "out.png"
    )
    assert [p.name for p in out] == ["out-1.png", "out-2.png"]
    assert len(calls) == 2
    for p in out:
        with Image.open(p) as image:
            image.verify()


def test_existing_output_is_protected_before_backend_call(tmp_path, monkeypatch):
    out = tmp_path / "out.png"
    out.write_bytes(b"existing file")

    def forbidden(**kw):
        pytest.fail("backend must not run")

    monkeypatch.setattr(cli, "_call_image_backend", forbidden)
    with pytest.raises(cli.CliError, match="already exists"):
        cli._run_one(args=args_for(tmp_path), mode="generate", prompt="test", output_path=out)
    assert out.read_bytes() == b"existing file"


@pytest.mark.parametrize("fail_fast", [False, True])
def test_batch_handles_backend_job_failures(tmp_path, monkeypatch, fail_fast):
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text('"bad"\n"good"\n')
    calls = []

    def run(**kw):
        calls.append(kw["prompt"])
        if kw["prompt"] == "bad":
            raise cli.CliError("simulated backend failure")
        return True

    monkeypatch.setattr(cli, "_run_one", run)
    argv = ["batch", "--input", str(jobs), "--out-dir", str(tmp_path)]
    if fail_fast:
        argv.append("--fail-fast")
    assert cli.main(argv) == 1
    assert calls == (["bad"] if fail_fast else ["bad", "good"])


def test_dry_run_never_reads_auth(tmp_path, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("auth must not be read")

    monkeypatch.setattr(cli, "_load_ready_auth", forbidden)
    assert (
        cli.main(
            [
                "generate",
                "--prompt",
                "test",
                "--out",
                str(tmp_path / "out.png"),
                "--dry-run",
                "--auth-file",
                "/nonexistent/auth.json",
            ]
        )
        == 0
    )
