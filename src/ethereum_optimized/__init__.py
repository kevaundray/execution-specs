"""
Optimized Implementations.

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

This module contains alternative implementations of routines in the spec
that have been optimized for speed rather than clarity.

The optimized ``State`` class implements the ``PreState`` protocol and
can be used directly in place of the in-memory trie-based state.

The PoW validation is monkey patched into the fork module for pre-Merge
forks.
"""

from importlib import import_module
from typing import Optional

from ethereum_spec_tools.forks import Hardfork

from .fork import get_optimized_pow_patches


def monkey_patch_optimized_spec(fork_name: str) -> None:
    """
    Replace the ethash implementation with one that supports higher
    performance.

    This function must be called before the spec interface is imported
    anywhere.
    """
    slow_spec = import_module("ethereum.forks." + fork_name + ".fork")

    optimized_pow_patches = get_optimized_pow_patches(fork_name)

    for name, value in optimized_pow_patches.items():
        setattr(slow_spec, name, value)


def monkey_patch(state_path: Optional[str]) -> None:
    """
    Apply all monkey patches to the specification.

    Only PoW validation is monkey-patched. The optimized state is used
    directly via ``ethereum_optimized.state_db.State``.
    """
    from .state_db import State

    if state_path is not None:
        State.default_path = state_path

    forks = Hardfork.discover()

    for fork in forks:
        if fork.consensus.is_pow():
            monkey_patch_optimized_spec(fork.short_name)
