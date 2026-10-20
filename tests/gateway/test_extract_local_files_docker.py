"""Bare container paths in reply text are deliverable under the Docker backend.

``MEDIA:/output/x.png`` already translates a container path onto its ``docker_volumes`` host mount
(``validate_media_delivery_path``). A bare ``/output/x.png`` in the reply text went through
``extract_local_files`` first, which only accepted paths that exist on the gateway host, so the
attachment was dropped before translation could run. Both routes now accept the same paths.
"""

import json

import pytest

from gateway.platforms.base import BasePlatformAdapter

_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 16


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    """A Docker-backed gateway with ``<tmp>/out`` mounted at ``/output`` in the sandbox."""
    home = tmp_path / "hermes"
    out = tmp_path / "out"
    home.mkdir()
    out.mkdir()
    (out / "chart.png").write_bytes(_PNG)
    volumes = [f"{out}:/output"]
    (home / "config.yaml").write_text(
        json.dumps({"terminal": {"backend": "docker", "docker_volumes": volumes}}), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", json.dumps(volumes))
    return out


def test_bare_container_path_is_extracted_and_delivered_from_the_host_mount(mounted):
    paths, cleaned = BasePlatformAdapter.extract_local_files("Here you go: /output/chart.png enjoy")

    assert paths == ["/output/chart.png"]
    assert "/output/chart.png" not in cleaned
    assert BasePlatformAdapter.filter_local_delivery_paths(paths) == [str((mounted / "chart.png").resolve())]


def test_a_container_path_with_no_file_behind_it_is_still_ignored(mounted):
    paths, cleaned = BasePlatformAdapter.extract_local_files("see /output/missing.png")

    assert paths == []
    assert "/output/missing.png" in cleaned


def test_without_the_docker_backend_a_container_path_is_not_translated(mounted, monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")

    paths, _ = BasePlatformAdapter.extract_local_files("see /output/chart.png")

    assert paths == []
