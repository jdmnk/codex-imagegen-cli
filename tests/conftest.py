"""Offline tests must not depend on a developer's Codex configuration or auth."""

import pytest


@pytest.fixture(autouse=True)
def isolated_codex_settings(tmp_path, monkeypatch, request):
    if request.node.get_closest_marker("live"):
        return
    home = tmp_path / "isolated-codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_IMAGEGEN_CODEX_VERSION", "0.154.0")
    for name in [
        "CODEX_IMAGEGEN_MODEL",
        "CODEX_IMAGEGEN_BASE_URL",
        "CODEX_IMAGEGEN_REFRESH_URL",
        "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    ]:
        monkeypatch.delenv(name, raising=False)
