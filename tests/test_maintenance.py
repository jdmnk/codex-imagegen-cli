"""Backend, credential persistence, output and transport integration regressions."""

import base64
import copy
import json
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
from http.client import IncompleteRead
from io import BytesIO

import pytest
from PIL import Image
from test_regressions import args_for, fake_auth, image_result, jwt

from codex_imagegen_cli import cli


def install_fake_auth(tmp_path, monkeypatch):
    auth = fake_auth()
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(auth))
    monkeypatch.setattr(cli, "_load_ready_auth", lambda a: (auth, path))
    monkeypatch.setattr(cli, "_auth_headers", lambda a: {"Authorization": "Bearer fake-access"})
    return auth, path


def test_native_is_default_and_does_not_read_reasoning_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_IMAGEGEN_IMAGE_MODEL", "nonexistent-legacy-override")
    (tmp_path / "config.toml").write_text("not valid toml")
    assert (
        cli.main(
            [
                "generate",
                "--prompt",
                "native",
                "--out",
                str(tmp_path / "a.png"),
                "--codex-home",
                str(tmp_path),
                "--dry-run",
            ]
        )
        == 0
    )
    request = json.loads(capsys.readouterr().out)
    assert request["url"].endswith("/images/generations")
    assert request["payload"] == {
        "model": "gpt-image-2",
        "prompt": "native",
        "quality": "auto",
        "size": "auto",
        "background": "auto",
    }


def test_native_edit_and_style_reference_encode_ordered_images(tmp_path, monkeypatch, capsys):
    install_fake_auth(tmp_path, monkeypatch)
    source = tmp_path / "content.png"
    style = tmp_path / "style.png"
    Image.new("RGB", (4, 4), "red").save(source)
    Image.new("RGB", (4, 4), "blue").save(style)
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        return {"data": [{"b64_json": image_result()}]}

    monkeypatch.setattr(cli, "_post_json", post)
    out = tmp_path / "edit.webp"
    assert (
        cli.main(["edit", "--image", str(source), "--style-image", str(style), "--out", str(out)])
        == 0
    )
    assert calls[0][0].endswith("/images/edits")
    payload = calls[0][1]["payload"]
    assert "final input image only as a style reference" in payload["prompt"]
    for ref, color in zip(payload["images"], [0, 2], strict=True):
        with Image.open(BytesIO(base64.b64decode(ref["image_url"].split(",")[1]))) as image:
            assert image.getpixel((0, 0))[color] > 240
    with Image.open(out) as image:
        assert image.format == "WEBP"
        image.verify()
    assert capsys.readouterr().out.splitlines() == [str(out)]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": []},
        {"data": [{"b64_json": None}]},
        {"data": [{"b64_json": "a"}, {"b64_json": "b"}]},
    ],
)
def test_native_rejects_malformed_response(body, monkeypatch):
    monkeypatch.setattr(cli, "_post_json", lambda *a, **kw: body)
    with pytest.raises(cli.CliError):
        cli._native_image_result("https://example.invalid", headers={}, payload={}, timeout=1)


def test_native_multiple_outputs_preserves_progress_on_failure(tmp_path, monkeypatch, capsys):
    install_fake_auth(tmp_path, monkeypatch)
    calls = []

    def post(*a, **kw):
        calls.append(kw["payload"])
        if len(calls) == 2:
            raise cli.TransportError("interrupted")
        return {"data": [{"b64_json": image_result()}]}

    monkeypatch.setattr(cli, "_post_json", post)
    with pytest.raises(SystemExit):
        cli.main(["generate", "--prompt", "test", "--n", "2", "--out", str(tmp_path / "out.png")])
    assert capsys.readouterr().out.splitlines() == [str(tmp_path / "out-1.png")]
    assert (tmp_path / "out-1.png").exists()
    assert not (tmp_path / "out-2.png").exists()
    assert len(calls) == 2


@pytest.mark.parametrize("policy", ["warn", "error"])
def test_size_mismatch_policy_preserves_existing_output(tmp_path, capsys, policy):
    out = tmp_path / "out.png"
    out.write_bytes(b"old")
    kw = dict(
        force=True,
        output_format="png",
        webp_quality=85,
        requested_size="1024x1024",
        size_policy=policy,
    )
    if policy == "error":
        with pytest.raises(cli.CliError, match="backend returned 2x2"):
            cli._write_response_image(image_result(), out, **kw)
        assert out.read_bytes() == b"old"
    else:
        cli._write_response_image(image_result(), out, **kw)
        assert "backend returned 2x2" in capsys.readouterr().err
        with Image.open(out) as image:
            assert image.size == (2, 2)


def test_matching_size_and_auto_do_not_warn(tmp_path, capsys):
    for size in ["auto", "2x2"]:
        cli._write_response_image(
            image_result(),
            tmp_path / f"{size}.png",
            force=False,
            output_format="png",
            webp_quality=85,
            requested_size=size,
            size_policy="error",
        )
    assert "Warning" not in capsys.readouterr().err


@pytest.mark.parametrize("data", [b"not an image", b"\x89PNG\r\n\x1a\n"])
def test_invalid_image_preserves_existing_output(tmp_path, data):
    out = tmp_path / "out.png"
    out.write_bytes(b"old")
    with pytest.raises(cli.CliError, match="invalid image"):
        cli._write_response_image(
            base64.b64encode(data).decode(), out, force=True, output_format="png", webp_quality=85
        )
    assert out.read_bytes() == b"old"


def test_atomic_write_failure_preserves_original_and_cleans_temp(tmp_path, monkeypatch):
    out = tmp_path / "auth.json"
    out.write_bytes(b"original")

    def fail(*a):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(cli.os, "replace", fail)
    with pytest.raises(OSError):
        cli._atomic_write(out, b"new", replace=True)
    assert out.read_bytes() == b"original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["auth.json"]


def test_output_created_during_generation_is_not_overwritten(tmp_path, monkeypatch):
    out = tmp_path / "image.png"
    link = cli.os.link

    def race(source, target):
        out.write_bytes(b"another process")
        return link(source, target)

    monkeypatch.setattr(cli.os, "link", race)
    with pytest.raises(cli.CliError, match="already exists"):
        cli._write_response_image(
            image_result(), out, force=False, output_format="png", webp_quality=85
        )
    assert out.read_bytes() == b"another process"
    assert list(tmp_path.iterdir()) == [out]


def test_refresh_keeps_raw_jwt_permissions_and_unknown_fields(tmp_path, monkeypatch):
    auth = fake_auth()
    auth["extra"] = {"preserve": True}
    original = copy.deepcopy(auth)
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(auth))
    new_id = jwt({"email": "test@example.invalid"})
    monkeypatch.setattr(
        cli,
        "_post_json",
        lambda *a, **kw: {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "id_token": new_id,
        },
    )
    updated = cli._refresh_auth(auth, path, timeout=1)
    assert auth == original
    assert updated == json.loads(path.read_text())
    assert updated["tokens"]["id_token"] == new_id
    assert updated["extra"] == auth["extra"]
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_refreshed_auth_is_readable_by_installed_codex(tmp_path, monkeypatch):
    codex = shutil.which("codex")
    if not codex:
        pytest.skip("Codex binary not installed; JWT schema is covered separately")
    auth = fake_auth()
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(auth))
    monkeypatch.setattr(
        cli,
        "_post_json",
        lambda *a, **kw: {
            "access_token": "new-access",
            "id_token": jwt({"email": "test@example.invalid"}),
        },
    )
    cli._refresh_auth(auth, path, timeout=1)
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(fragment in k for fragment in ("TOKEN", "API_KEY", "AUTH", "BEDROCK", "AWS"))
    }
    env["CODEX_HOME"] = str(tmp_path)
    result = subprocess.run(
        [codex, "login", "status"], env=env, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert "ChatGPT" in result.stderr + result.stdout


def test_concurrent_refreshes_reuse_new_token(tmp_path, monkeypatch):
    auth = fake_auth()
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(auth))
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def post(*a, **kw):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return {"access_token": "new-access", "refresh_token": "new-refresh"}

    monkeypatch.setattr(cli, "_post_json", post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cli._refresh_auth, auth, path, timeout=2)
        try:
            assert entered.wait(2)
            second = pool.submit(cli._refresh_auth, auth, path, timeout=2)
        finally:
            release.set()
        assert first.result(timeout=3)["tokens"]["access_token"] == "new-access"
        assert second.result(timeout=3)["tokens"]["access_token"] == "new-access"
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["replace", "delete"])
def test_refresh_does_not_overwrite_external_auth_change(tmp_path, monkeypatch, change):
    auth = fake_auth()
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(auth))
    other = copy.deepcopy(auth)
    other["tokens"]["access_token"] = "external-change"

    def post(*a, **kw):
        if change == "replace":
            path.write_text(json.dumps(other))
        else:
            path.unlink()
        return {"access_token": "imagegen-refresh"}

    monkeypatch.setattr(cli, "_post_json", post)
    with pytest.raises(cli.CliError, match="auth changed"):
        cli._refresh_auth(auth, path, timeout=1)
    assert (json.loads(path.read_text()) == other) if change == "replace" else not path.exists()


def test_refresh_error_never_echoes_oauth_body(tmp_path, monkeypatch):
    def fail(*a, **kw):
        raise cli.HttpError(400, "fake-access fake-refresh")

    monkeypatch.setattr(cli, "_post_json", fail)
    with pytest.raises(cli.CliError) as error:
        cli._refresh_auth(fake_auth(), tmp_path / "auth.json", timeout=1)
    assert "fake-access" not in str(error.value)
    assert "fake-refresh" not in str(error.value)


def test_refresh_without_access_token_leaves_auth_unchanged(tmp_path, monkeypatch):
    auth = fake_auth()
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(auth))
    monkeypatch.setattr(cli, "_post_json", lambda *a, **kw: {"refresh_token": "new"})
    with pytest.raises(cli.CliError, match="no access token"):
        cli._refresh_auth(auth, path, timeout=1)
    assert json.loads(path.read_text()) == auth


def test_config_profile_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_IMAGEGEN_MODEL", raising=False)
    (tmp_path / "config.toml").write_text(
        'model = "top"\nprofile = "active"\n'
        '[profiles.inactive]\nmodel = "wrong"\n[profiles.active]\nmodel = "active-model"\n'
    )
    args = args_for(tmp_path)
    args.codex_home = str(tmp_path)
    args.model = None
    assert cli._default_model(args) == "active-model"
    args.profile = "inactive"
    assert cli._default_model(args) == "wrong"
    monkeypatch.setenv("CODEX_IMAGEGEN_MODEL", "environment")
    assert cli._default_model(args) == "environment"
    args.model = "explicit"
    assert cli._default_model(args) == "explicit"


@pytest.mark.parametrize("size", ["2048x2048", "1536x864", "3840x2160", "auto"])
def test_supported_custom_sizes(size):
    assert cli._parse_size(size) == size


@pytest.mark.parametrize(
    "size", ["1x1", "0x0", "1023x1024", "4096x4096", "3840x3840", "3840x512", "nan"]
)
def test_invalid_sizes(size):
    with pytest.raises(cli.argparse.ArgumentTypeError):
        cli._parse_size(size)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout(timeout, tmp_path):
    args = args_for(tmp_path)
    args.timeout = timeout
    with pytest.raises(cli.CliError, match="positive finite"):
        cli._validate_common(args)


@pytest.mark.parametrize("backend", ["native", "responses"])
def test_removed_image_model_flag_fails_before_auth(backend, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_ready_auth", lambda *a: pytest.fail("unexpected auth read"))
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "generate",
                "--prompt",
                "test",
                "--out",
                str(tmp_path / "out.png"),
                "--backend",
                backend,
                "--image-model",
                "gpt-image-2.5-sunburst",
            ]
        )
    assert error.value.code == 2
    assert "unrecognized arguments: --image-model" in capsys.readouterr().err


def test_native_transparency_and_wrong_model_flags_fail_before_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_load_ready_auth", lambda *a: pytest.fail("unexpected auth read"))
    for extra in [["--background", "transparent"], ["--model", "gpt-5.5"]]:
        with pytest.raises(SystemExit):
            cli.main(["generate", "--prompt", "test", "--out", str(tmp_path / "out.png"), *extra])


def test_sse_crlf_and_utf8_split_across_chunks(monkeypatch):
    text = 'event: response.output_text.delta\r\ndata: {"delta":"café"}\r\n\r\n'

    class Chunks(BytesIO):
        def read1(self, n):
            return self.read(1)

    monkeypatch.setattr(cli.request, "urlopen", lambda *a, **kw: Chunks(text.encode()))
    events = list(cli._post_sse("https://example.invalid", headers={}, payload={}, timeout=1))
    assert events == [("response.output_text.delta", '{"delta":"café"}')]


def test_stream_closes_on_completed_image(monkeypatch):
    closed = []

    def events(*a, **kw):
        try:
            yield (
                None,
                json.dumps(
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "image_generation_call",
                            "status": "completed",
                            "result": image_result(),
                        },
                    }
                ),
            )
            pytest.fail("should return after complete image")
        finally:
            closed.append(True)

    monkeypatch.setattr(cli, "_post_sse", events)
    cli._stream_image_result("https://example.invalid", headers={}, payload={}, timeout=1)
    assert closed == [True]


def test_json_incomplete_read_is_transport_error(monkeypatch):
    class Broken(BytesIO):
        def read(self, *a):
            raise IncompleteRead(b"partial", 10)

    monkeypatch.setattr(cli.request, "urlopen", lambda *a, **kw: Broken())
    with pytest.raises(cli.TransportError):
        cli._post_json("https://example.invalid", payload={}, timeout=1)


@pytest.mark.parametrize(
    "header,delay", [("3", 3), ("0", 1), ("invalid", 1), ("301", None), ("inf", None)]
)
def test_retry_after_bounded(header, delay):
    result = cli._http_retry_decision(cli.HttpError(429, "rate limited", header), 0)
    assert (result.delay if result else None) == delay


def test_retry_after_http_date(monkeypatch):
    monkeypatch.setattr(cli.time, "time", lambda: 1000000)
    result = cli._http_retry_decision(
        cli.HttpError(429, "rate limited", formatdate(1000020, usegmt=True)), 0
    )
    assert result.delay == 20


def test_server_message_delay_is_bounded():
    body = json.dumps(
        {"error": {"code": "rate_limit_exceeded", "message": "Please try again in 9999s."}}
    )
    assert cli._http_retry_decision(cli.HttpError(429, body), 0) is None


@pytest.mark.parametrize(
    "code", ["insufficient_quota", "billing_hard_limit_reached", "moderation_blocked"]
)
def test_quota_errors_are_not_retried(code):
    assert (
        cli._http_retry_decision(cli.HttpError(429, json.dumps({"error": {"code": code}})), 0)
        is None
    )


def test_native_401_then_429_uses_common_retry_loop(tmp_path, monkeypatch):
    install_fake_auth(tmp_path, monkeypatch)
    responses = [
        cli.HttpError(401, "expired"),
        cli.HttpError(429, "rate limited", "2"),
        {"data": [{"b64_json": image_result()}]},
    ]

    def post(*a, **kw):
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(cli, "_post_json", post)
    monkeypatch.setattr(cli, "_refresh_auth", lambda *a, **kw: fake_auth())
    sleeps = []
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    assert cli.main(["generate", "--prompt", "test", "--out", str(tmp_path / "out.png")]) == 0
    assert not responses
    assert sleeps == [2]


def test_batch_rejects_output_escape_and_continues(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text('{"prompt":"bad","out":"../outside.png"}\n{"prompt":"good"}\n')
    calls = []
    monkeypatch.setattr(cli, "_run_one", lambda **kw: calls.append(kw["prompt"]))
    assert cli.main(["batch", "--input", str(jobs), "--out-dir", str(tmp_path / "out")]) == 1
    assert calls == ["good"]
