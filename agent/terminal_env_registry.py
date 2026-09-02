"""
Terminal Environment Registry
=============================

Central map of registered pluggable terminal backends. Populated by plugins
at load time via :meth:`PluginContext.register_terminal_environment_provider`;
consumed by :func:`tools.terminal_tool._create_environment` and the
classification helpers spread across the terminal/file/approval/prompt
surfaces.

Unlike the image/video/web/browser registries there is **no active-provider
resolution here**: the active backend is whatever ``TERMINAL_ENV`` /
``terminal.backend`` names, exactly as for built-in backends. The registry's
only job is mapping that name to a provider instance (and answering the
classification questions the core historically answered with frozensets of
built-in names).

Built-in backend names are reserved — :func:`register_provider` rejects a
provider whose ``name`` collides with one, so a plugin can never shadow the
in-tree docker/modal/... implementations.

Mirrors :mod:`agent.browser_registry` scope semantics: providers register
into a per-profile scope (multiplexed gateways) or the global base map.

Lock order: :func:`_ensure_builtin_providers` (guarded by ``_bootstrap_lock``)
must always run BEFORE ``_lock`` is taken, never while holding it --
``register_provider`` takes ``_lock``, so bootstrapping under ``_lock`` would
deadlock. Every read path calls the bootstrap first, as a separate step.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from agent.terminal_env_provider import TerminalEnvironmentProvider
from hermes_constants import hermes_home_key

logger = logging.getLogger(__name__)


#: Names owned by in-tree backends in tools/environments/ — never
#: registrable by plugins. Includes internal-mode aliases (managed_modal).
BUILTIN_BACKEND_NAMES = frozenset({
    "local", "docker", "singularity", "modal", "managed_modal",
    "daytona", "vercel_sandbox", "ssh",
})


_providers: Dict[str, TerminalEnvironmentProvider] = {}
_scoped_providers: Dict[str, Dict[str, TerminalEnvironmentProvider]] = {}
_generation = 0
_scoped_generations: Dict[str, int] = {}
_lock = threading.Lock()
_builtin_bootstrap_done = False
#: Separate from ``_lock``: registration takes ``_lock``, so bootstrapping
#: under it would deadlock. Every caller runs this BEFORE taking ``_lock``.
_bootstrap_lock = threading.Lock()


def _ensure_builtin_providers() -> None:
    """Register the in-tree providers that ride this registry.

    ``apple_container`` is an in-tree backend that classifies through the
    registry rather than through the hardcoded frozensets. Nothing imports
    its module eagerly, and a bare ``run_agent`` process never runs plugin
    discovery, so every read path bootstraps it here. Runs once; called
    before the lock, never while holding it.

    The flag flips only once registration has actually finished. Setting it
    first lets a second thread skip the bootstrap and read the registry while
    it is still empty, which classifies apple_container as a non-container
    backend for that call: host cwds stop being sanitized and file tools fall
    back to host path resolution, silently and only under concurrency.
    """
    global _builtin_bootstrap_done
    assert not _lock.locked(), "bootstrap must run before _lock is taken"
    if _builtin_bootstrap_done:
        return
    with _bootstrap_lock:
        if _builtin_bootstrap_done:
            return
        try:
            from tools.environments.apple_container_provider import (
                register_builtin_providers,
            )

            register_builtin_providers()
        except Exception:
            logger.debug("Built-in terminal provider bootstrap failed", exc_info=True)
        # Set even on failure: a broken import must not be retried on every
        # registry read.
        _builtin_bootstrap_done = True


def register_provider(
    provider: TerminalEnvironmentProvider, *, scope: Optional[str] = None
) -> None:
    """Register a terminal environment provider.

    Re-registration (same ``name``) overwrites the previous entry — makes
    hot-reload scenarios (tests, dev loops) behave predictably.

    Raises:
        TypeError: not a TerminalEnvironmentProvider instance.
        ValueError: empty name or collision with a built-in backend name.
    """
    if not isinstance(provider, TerminalEnvironmentProvider):
        raise TypeError(
            f"register_provider() expects a TerminalEnvironmentProvider "
            f"instance, got {type(provider).__name__}"
        )
    raw_name = provider.name
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValueError("Terminal environment provider .name must be a non-empty string")
    name = raw_name.strip().lower()
    if name in BUILTIN_BACKEND_NAMES:
        raise ValueError(
            f"Terminal backend name '{name}' is reserved for the built-in "
            f"{name} backend and cannot be registered by a plugin"
        )
    global _generation
    with _lock:
        target = _providers if scope is None else _scoped_providers.setdefault(scope, {})
        existing = target.get(name)
        target[name] = provider
        if scope is None:
            _generation += 1
        else:
            _scoped_generations[scope] = _scoped_generations.get(scope, 0) + 1
    if existing is not None:
        logger.debug(
            "Terminal environment provider '%s' re-registered (was %r)",
            name, type(existing).__name__,
        )
    else:
        logger.debug(
            "Registered terminal environment provider '%s' (%s)",
            name, type(provider).__name__,
        )


def list_providers(*, scope: Optional[str] = None) -> List[TerminalEnvironmentProvider]:
    """Return all registered providers, sorted by name."""
    _ensure_builtin_providers()
    with _lock:
        merged = dict(_providers)
        merged.update(_scoped_providers.get(scope or hermes_home_key(), {}))
        items = list(merged.values())
    return sorted(items, key=lambda p: p.name)


def get_provider(
    name: str, *, scope: Optional[str] = None
) -> Optional[TerminalEnvironmentProvider]:
    """Return the provider registered under *name*, or None."""
    if not isinstance(name, str):
        return None
    _ensure_builtin_providers()
    key = name.strip().lower()
    with _lock:
        return (
            _scoped_providers.get(scope or hermes_home_key(), {}).get(key)
            or _providers.get(key)
        )


def plugin_backend_names(*, scope: Optional[str] = None) -> List[str]:
    """Names of all registered plugin backends (sorted)."""
    return [p.name.strip().lower() for p in list_providers(scope=scope)]


def provider_flag(name: str, attr: str, default=False):
    """Read a classification attribute off the provider for *name*.

    Fail-soft: unknown backend or a raising property returns *default* so a
    misbehaving plugin degrades to built-in-equivalent behavior instead of
    taking the terminal tool down.
    """
    provider = get_provider(name)
    if provider is None:
        return default
    try:
        return getattr(provider, attr, default)
    except Exception:
        logger.debug(
            "Terminal environment provider '%s' attribute '%s' raised",
            name, attr, exc_info=True,
        )
        return default


def plugin_strip_env_keys() -> frozenset:
    """Union of every registered provider's ``strip_env_keys``.

    Secrets are stripped for ALL registered backends, not just the active
    one — a token in the process environment is strippable regardless of
    which backend is selected (mirrors how MODAL_*/DAYTONA_API_KEY sit in
    the static tier-1 set unconditionally).
    """
    keys: set = set()
    _ensure_builtin_providers()
    with _lock:
        all_providers = list(_providers.values())
        for scoped in _scoped_providers.values():
            all_providers.extend(scoped.values())
    for provider in all_providers:
        try:
            keys.update(provider.strip_env_keys)
        except Exception:
            logger.debug(
                "Terminal environment provider strip_env_keys raised",
                exc_info=True,
            )
    return frozenset(keys)


def snapshot_registration(
    name: str, *, scope: Optional[str] = None
) -> Optional[TerminalEnvironmentProvider]:
    with _lock:
        target = _providers if scope is None else _scoped_providers.get(scope, {})
        return target.get(name.strip().lower())


def registry_generation(*, scope: Optional[str] = None) -> tuple:
    """Return a cache fingerprint for the global base and one profile."""
    active_scope = scope or hermes_home_key()
    with _lock:
        return _generation, _scoped_generations.get(active_scope, 0)


def restore_registration(
    name: str,
    current: TerminalEnvironmentProvider,
    previous: Optional[TerminalEnvironmentProvider],
    *,
    scope: Optional[str] = None,
) -> bool:
    """Restore a plugin registration only when *current* is still installed."""
    key = name.strip().lower()
    global _generation
    with _lock:
        target = _providers if scope is None else _scoped_providers.setdefault(scope, {})
        if target.get(key) is not current:
            return False
        if previous is None:
            target.pop(key, None)
        else:
            target[key] = previous
        if scope is None:
            _generation += 1
        else:
            _scoped_generations[scope] = _scoped_generations.get(scope, 0) + 1
            if not target:
                _scoped_providers.pop(scope, None)
    return True


def _reset_for_tests() -> None:
    """Clear all registrations. Test hook, mirrors sibling registries."""
    global _generation, _builtin_bootstrap_done
    with _bootstrap_lock:
        _builtin_bootstrap_done = False
    with _lock:
        _providers.clear()
        _scoped_providers.clear()
        _scoped_generations.clear()
        _generation = 0
