"""Aspect-ratio request coverage; all transport/auth data is synthetic."""

import base64
import json
from io import BytesIO

import pytest
from PIL import Image
from test_regressions import fake_auth

from codex_imagegen_cli import cli


def image_result(width: int, height: int) -> str:
    out = BytesIO()
    Image.new("RGB", (width, height), "blue").save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode()


def ratio_args(tmp_path, ratio):
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
            "--aspect-ratio",
            ratio,
        ]
    )


@pytest.mark.parametrize(
    "value",
    ["1:1", "16:9", "9:16", "2:3", "3:2", "21:9", "9:18", "2:1", "1:2", "3:1", "1:3"],
)
def test_supported_aspect_ratios_are_accepted(value):
    assert cli._parse_aspect_ratio(value) == value


@pytest.mark.parametrize(
    "value", ["0:1", "1:0", "16:0", "0:9", "4:1", "1:4", "abc", "16", "16x9", ""]
)
def test_invalid_aspect_ratios_are_rejected(value):
    with pytest.raises(cli.argparse.ArgumentTypeError):
        cli._parse_aspect_ratio(value)


def test_aspect_ratio_is_mutually_exclusive_with_size(tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "generate",
                "--prompt",
                "test",
                "--out",
                str(tmp_path / "out.png"),
                "--size",
                "1024x1024",
                "--aspect-ratio",
                "16:9",
            ]
        )
    assert "not allowed with argument --size" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        ("16:9", "16:9 landscape format, wider than it is tall"),
        ("2:3", "2:3 portrait format, taller than it is wide"),
        ("1:1", "1:1 square"),
    ],
)
def test_prompt_gains_ratio_orientation_guidance(ratio, expected):
    prompt = cli._aspect_ratio_prompt("A lighthouse.", ratio)
    assert expected in prompt
    assert "A lighthouse." in prompt


def test_ratio_instruction_leads_the_prompt():
    """Framing leads the scene, matching the style-library template ordering."""
    prompt = cli._aspect_ratio_prompt("A lighthouse.", "16:9")
    assert prompt.startswith("The frame must be")
    assert prompt.index("The frame must be") < prompt.index("A lighthouse.")


def test_prompt_ratio_guidance_survives_an_empty_prompt():
    assert cli._aspect_ratio_prompt("", "16:9").strip() != ""


def test_send_prompt_carries_ratio_guidance(tmp_path, monkeypatch):
    calls = []

    def post(url, **kw):
        calls.append(kw["payload"])
        return {"data": [{"b64_json": image_result(1024, 1536)}]}

    monkeypatch.setattr(cli, "_post_json", post)
    monkeypatch.setattr(cli, "_load_ready_auth", lambda a: (fake_auth(), tmp_path / "auth.json"))
    monkeypatch.setattr(cli, "_auth_headers", lambda a: {})
    args = ratio_args(tmp_path, "2:3")
    args.backend = "native"
    cli._call_native_backend(
        args=args, mode="generate", prompt="A lighthouse.", output_path=tmp_path / "out.png"
    )
    assert "2:3 portrait format" in calls[0]["prompt"]


@pytest.mark.parametrize(
    ("ratio", "size", "mismatch"),
    [
        ("2:3", "1024x1536", False),
        ("2:3", "1254x1254", True),
        ("2:3", "941x1672", True),
        ("16:9", "1672x941", False),
        ("16:9", "1254x1254", True),
        ("1:1", "1254x1254", False),
        ("21:9", "1916x821", False),
        ("21:9", "2141x734", True),
        ("3:2", "1536x1024", False),
    ],
)
def test_aspect_ratio_mismatch_detection(ratio, size, mismatch):
    assert cli._aspect_ratio_mismatch(ratio, size) is mismatch


def _write(encoded, out, ratio, policy, force=False):
    return cli._write_response_image(
        encoded,
        out,
        force=force,
        output_format="png",
        webp_quality=85,
        size_policy=policy,
        requested_aspect_ratio=ratio,
    )


def test_matching_ratio_does_not_warn(tmp_path, capsys):
    _write(image_result(1024, 1536), tmp_path / "out.png", "2:3", "error")
    assert "Warning" not in capsys.readouterr().err


def test_ratio_mismatch_warns_and_keeps_original_dimensions(tmp_path, capsys):
    out = tmp_path / "out.png"
    _write(image_result(1254, 1254), out, "2:3", "warn")
    assert "Requested aspect ratio 2:3, backend returned 1254x1254" in capsys.readouterr().err
    with Image.open(out) as image:
        assert image.size == (1254, 1254)


def test_ratio_mismatch_error_does_not_write_output(tmp_path):
    out = tmp_path / "out.png"
    with pytest.raises(cli.CliError, match="Requested aspect ratio 2:3"):
        _write(image_result(1254, 1254), out, "2:3", "error")
    assert not out.exists()


def test_ratio_mismatch_error_preserves_existing_output(tmp_path):
    out = tmp_path / "out.png"
    out.write_bytes(b"old")
    with pytest.raises(cli.CliError, match="Requested aspect ratio 2:3"):
        _write(image_result(1254, 1254), out, "2:3", "error", force=True)
    assert out.read_bytes() == b"old"


def test_ratio_request_keeps_size_in_payload_untouched(tmp_path):
    """The backend ignores size; the ratio must not be smuggled into it."""
    args = ratio_args(tmp_path, "16:9")
    payload = cli._responses_payload(prompt="x", args=args, mode="generate")
    assert payload["tools"][0]["size"] == "auto"


def test_batch_applies_the_cli_ratio_to_every_job(tmp_path, monkeypatch):
    """Batch image options come from CLI flags, matching --size and --quality."""
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text(
        json.dumps({"prompt": "a", "out": "a.png"})
        + "\n"
        + json.dumps({"prompt": "b", "out": "b.png"})
        + "\n",
        encoding="utf-8",
    )
    seen = []

    def run_one(*, args, mode, prompt, output_path, image_paths=None, log_prefix=""):
        seen.append((args.aspect_ratio, mode, prompt))
        return True

    monkeypatch.setattr(cli, "_run_one", run_one)
    code = cli.main(
        [
            "batch",
            "--input",
            str(jobs),
            "--out-dir",
            str(tmp_path / "out"),
            "--aspect-ratio",
            "16:9",
        ]
    )
    assert code == 0
    assert seen == [("16:9", "generate", "a"), ("16:9", "generate", "b")]
