"""Routing regressions for terminal calls using Apple Container."""

from unittest.mock import Mock

import tools.environments.apple_container as apple
import tools.terminal_tool as terminal_tool


def _config() -> dict:
    return {
        "env_type": "apple_container",
        "apple_container_image": "python:3.12-slim",
        "apple_container_volumes": ["/host/data:/workspace/data:ro"],
        "cwd": "/workspace",
        "host_cwd": None,
        "timeout": 42,
        "lifetime_seconds": 300,
        "container_cpu": 4,
        "container_memory": 6144,
        "container_disk": 51200,
        "container_persistent": True,
        "modal_mode": "auto",
        "vercel_runtime": "",
    }


def test_factory_passes_all_apple_container_settings(monkeypatch):
    fake = Mock(return_value=object())
    monkeypatch.setattr(apple, "AppleContainerEnvironment", fake)

    result = terminal_tool._create_environment(
        env_type="apple_container",
        image="python:3.12-slim",
        cwd="/workspace",
        timeout=42,
        container_config={
            "container_cpu": 4,
            "container_memory": 6144,
            "container_persistent": True,
            "apple_container_image": "python:3.12-slim",
            "apple_container_volumes": ["/host/data:/workspace/data:ro"],
        },
        task_id="task-1",
    )

    assert result is fake.return_value
    fake.assert_called_once_with(
        image="python:3.12-slim",
        cwd="/workspace",
        timeout=42,
        cpu=4,
        memory=6144,
        persistent_filesystem=True,
        task_id="task-1",
        volumes=["/host/data:/workspace/data:ro"],
        extra_args=[],
    )


def test_terminal_creation_passes_apple_specific_config(monkeypatch):
    captured = {}

    class DummyEnvironment:
        cwd = "/workspace"

        def execute(self, *args, **kwargs):
            return {"output": "", "exit_code": 0}

    def fake_create_environment(**kwargs):
        captured.update(kwargs)
        return DummyEnvironment()

    monkeypatch.setattr(terminal_tool, "_get_env_config", _config)
    monkeypatch.setattr(terminal_tool, "_create_environment", fake_create_environment)
    monkeypatch.setattr(terminal_tool, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(terminal_tool, "_check_all_guards", lambda *args, **kwargs: {"approved": True})
    monkeypatch.setattr(terminal_tool, "_active_environments", {})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})

    terminal_tool.terminal_tool(command="pwd", task_id="task-route")

    assert captured["image"] == "python:3.12-slim"
    assert captured["container_config"]["apple_container_image"] == "python:3.12-slim"
    assert captured["container_config"]["apple_container_volumes"] == [
        "/host/data:/workspace/data:ro"
    ]


def test_registered_container_providers_agree_with_approval_conditionality():
    """tools.approval._should_skip_container_guards keeps its own hardcoded
    ``{"docker", "apple_container"}`` set instead of reading a provider's
    ``skip_container_guards`` (rewiring approval.py is out of scope). This is
    the tripwire: derive "host-access-conditional" behaviorally (does
    has_host_access flip the outcome for that name?) instead of re-hardcoding
    approval.py's literal set here, so the test can't itself drift from the
    source it is checking. Any registered provider approval.py treats as
    conditional must declare skip_container_guards=False, matching that
    guards apply once a host path is mounted -- an unconditional-skip
    provider on that path would silently disagree with approval.py.
    """
    from agent.terminal_env_registry import list_providers
    from tools.approval import _should_skip_container_guards

    for provider in list_providers():
        name = provider.name.strip().lower()
        conditional = (
            _should_skip_container_guards(name, has_host_access=False)
            != _should_skip_container_guards(name, has_host_access=True)
        )
        if conditional:
            assert provider.skip_container_guards is False, (
                f"{name!r} is host-access-conditional in "
                "tools.approval._should_skip_container_guards but its "
                "provider declares skip_container_guards=True, an "
                "unconditional skip that disagrees with approval.py."
            )
