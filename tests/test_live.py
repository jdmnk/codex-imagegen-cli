"""Opt-in compatibility checks: three image requests using existing ChatGPT auth.

CODEX_IMAGEGEN_LIVE_TEST=1 uv run pytest -m live -v
Never refreshes or writes the user's credentials. No servers or subprocess agents.
"""

import os

import pytest
from PIL import Image

from codex_imagegen_cli import cli

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CODEX_IMAGEGEN_LIVE_TEST") != "1",
        reason="live account usage is opt-in",
    ),
]


@pytest.fixture
def live_options(monkeypatch):
    def refuse_refresh(*args, **kwargs):
        raise cli.CliError(
            "Live compatibility check will not refresh shared credentials; log in with Codex and retry."
        )

    monkeypatch.setattr(cli, "_refresh_auth", refuse_refresh)
    monkeypatch.setattr(cli, "MAX_RESPONSES_IMAGE_RETRIES", 0)
    monkeypatch.delenv("CODEX_IMAGEGEN_BASE_URL", raising=False)
    args = cli.build_parser().parse_args(["generate", "--prompt", "test", "--out", "unused.png"])
    auth = cli._load_auth(cli._auth_file(args))
    assert not cli._token_is_expiring(cli._access_token(auth)), (
        "Use Codex to refresh your login first"
    )
    return ["--quality", "low", "--timeout", "120", "--size", "1024x1024"]


def test_live_native_generate_and_edit(tmp_path, monkeypatch, capsys, live_options):
    source = tmp_path / "native.png"
    assert (
        cli.main(
            [
                "generate",
                "--prompt",
                "A solid blue circle on a plain white background. No text.",
                "--out",
                str(source),
                *live_options,
            ]
        )
        == 0
    )
    with Image.open(source) as image:
        image.load()
        assert image.format == "PNG"
        assert min(image.size) >= 256
        actual_size = image.size
    output = capsys.readouterr()
    assert output.out.splitlines() == [str(source)]
    if actual_size != (1024, 1024):
        assert "backend returned" in output.err
    edited = tmp_path / "native-edit.webp"
    assert (
        cli.main(
            [
                "edit",
                "--image",
                str(source),
                "--prompt",
                "Change the blue circle to red. Preserve the composition and white background.",
                "--out",
                str(edited),
                *live_options,
            ]
        )
        == 0
    )
    with Image.open(edited) as image:
        image.load()
        assert image.format == "WEBP"
        assert min(image.size) >= 256


def test_live_responses_compatibility(tmp_path, live_options):
    out = tmp_path / "responses.png"
    assert (
        cli.main(
            [
                "generate",
                "--backend",
                "responses",
                "--prompt",
                "A solid green triangle on a plain white background. No text.",
                "--out",
                str(out),
                *live_options,
            ]
        )
        == 0
    )
    with Image.open(out) as image:
        image.load()
        assert image.format == "PNG"
        assert min(image.size) >= 256
