"""
Root conftest for the test suite.

Applies pure-Python performance optimizations via
``ethereum_optimized.monkey_patch_interpreter()``, plus test-specific
infrastructure caching (fork helpers, GC).
"""

from functools import lru_cache

from ethereum_optimized import monkey_patch_interpreter


def _apply_infrastructure_optimizations() -> None:
    """Cache test infrastructure hot paths (fork helpers)."""
    # Cache transition_fork_to (called 684K times, 1.7s).
    from execution_testing.forks import helpers as fork_helpers
    from execution_testing.forks.helpers import get_transition_forks
    from execution_testing.forks.transition_base_fork import (
        TransitionBaseClass,
    )

    _transition_forks = [
        f
        for f in get_transition_forks()
        if issubclass(f, TransitionBaseClass)
    ]

    @lru_cache(maxsize=256)
    def _fast_transition_fork_to(fork_to):
        result = set()
        for tf in _transition_forks:
            if tf.transitions_to() == fork_to:
                result.add(tf)
        return frozenset(result)

    def transition_fork_to_cached(fork_to):
        return set(_fast_transition_fork_to(fork_to))

    fork_helpers.transition_fork_to = transition_fork_to_cached

    # Also patch in modules that imported by name
    import execution_testing.forks as forks_pkg
    from execution_testing.cli.pytest_commands.plugins.forks import (
        forks as forks_plugin,
    )

    forks_pkg.transition_fork_to = transition_fork_to_cached
    forks_plugin.transition_fork_to = transition_fork_to_cached

    # Cache _is_subclass_of on BaseForkMeta (called 2.4M times).
    from execution_testing.forks.base_fork import BaseForkMeta

    _orig_maybe = BaseForkMeta._maybe_transitioned
    _subclass_cache: dict = {}

    @staticmethod  # type: ignore[misc]
    def _fast_is_subclass_of(a: object, b: object) -> bool:
        key = (id(a), id(b))
        result = _subclass_cache.get(key)
        if result is None:
            a2 = _orig_maybe(a)
            b2 = _orig_maybe(b)
            result = issubclass(a2, b2)
            _subclass_cache[key] = result
        return result

    BaseForkMeta._is_subclass_of = _fast_is_subclass_of


monkey_patch_interpreter()
_apply_infrastructure_optimizations()
