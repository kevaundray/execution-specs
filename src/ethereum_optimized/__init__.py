"""
Optimized Implementations.

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

This module contains alternative implementations of routines in the spec that
have been optimized for speed rather than clarity.

They can be monkey patched in during start up by calling the `monkey_patch()`
function. This must be done before those modules are imported anywhere.

There are two categories of optimizations:

1. **Pure-Python interpreter patches** (no external deps):
   Faster EVM interpreter, gas, stack, and memory operations.
   Applied by `monkey_patch_interpreter()`.

2. **C-extension patches** (require optional deps):
   LMDB-backed state DB and optimized ethash.
   Applied by `monkey_patch(state_path)`.
"""

from importlib import import_module
from typing import Any, Optional, cast

from ethereum_spec_tools.forks import Hardfork

from .interpreter import (
    _make_optimized_memory_ops,
    get_optimized_gas_patches,
    get_optimized_runtime_patches,
    get_optimized_stack_patches,
    get_optimized_trace_patches,
    patch_interpreter_loop,
    patch_uint_fast_arithmetic,
)

INSTRUCTION_SUBMODULES = [
    "arithmetic",
    "bitwise",
    "block",
    "comparison",
    "control_flow",
    "environment",
    "keccak",
    "log",
    "memory",
    "stack",
    "storage",
    "system",
]


def monkey_patch_interpreter() -> None:
    """
    Apply pure-Python interpreter optimizations to every fork.

    This patches the EVM interpreter hot paths:
    - ``get_valid_jump_destinations`` (int-based, avoids Uint/Enum)
    - ``charge_gas`` (skip trace when not tracing, inline arithmetic)
    - ``evm_trace`` (short-circuit when discard tracer active)
    - ``Uint``/``U256`` arithmetic (bypass __init__ validation)
    - ``push_n``/``dup_n``/``swap_n``/``pop`` (direct code read)
    - ``codecopy`` (int-based memory ops)
    - Interpreter inner loop (mypyc-compiled, if available)

    No external dependencies required.
    """
    import gc

    import ethereum.trace as trace_mod

    forks = Hardfork.discover()

    # Patch Uint/U256 arithmetic
    patch_uint_fast_arithmetic()

    # Disable GC during execution (re-enabled at exit)
    gc.disable()
    import atexit

    atexit.register(gc.enable)

    # Patch shared trace module
    for attr, value in get_optimized_trace_patches().items():
        setattr(trace_mod, attr, value)

    for fork in forks:
        name = fork.short_name

        # Patch runtime (get_valid_jump_destinations)
        try:
            runtime_mod = import_module(
                f"ethereum.forks.{name}.vm.runtime"
            )
        except (ImportError, ModuleNotFoundError):
            continue

        for attr, value in get_optimized_runtime_patches(name).items():
            setattr(runtime_mod, attr, value)

        # Patch gas module
        try:
            gas_mod = import_module(
                f"ethereum.forks.{name}.vm.gas"
            )
        except (ImportError, ModuleNotFoundError):
            continue

        gas_patches = get_optimized_gas_patches(name)
        for attr, value in gas_patches.items():
            setattr(gas_mod, attr, value)

        # Patch functions in instruction modules that imported by name
        for submod in INSTRUCTION_SUBMODULES:
            try:
                mod = import_module(
                    f"ethereum.forks.{name}.vm.instructions.{submod}"
                )
            except (ImportError, ModuleNotFoundError):
                continue
            for patch_name, patch_val in gas_patches.items():
                if hasattr(mod, patch_name):
                    setattr(mod, patch_name, patch_val)

        # Patch stack operations (push_n, dup_n, swap_n, pop)
        try:
            stack_mod = import_module(
                f"ethereum.forks.{name}.vm.instructions.stack"
            )
            instructions_mod = import_module(
                f"ethereum.forks.{name}.vm.instructions"
            )
        except (ImportError, ModuleNotFoundError):
            continue

        stack_patches = get_optimized_stack_patches(name)
        for attr, value in stack_patches.items():
            setattr(stack_mod, attr, value)

        # Update op_implementation dispatch dict
        op_impl = instructions_mod.op_implementation
        ops_enum = instructions_mod.Ops

        for n in range(33):
            func = stack_patches.get(f"push{n}")
            if func is not None:
                try:
                    op_impl[ops_enum(0x5F + n)] = func
                except ValueError:
                    pass
        for n in range(16):
            func = stack_patches.get(f"dup{n + 1}")
            if func is not None:
                try:
                    op_impl[ops_enum(0x80 + n)] = func
                except ValueError:
                    pass
        for n in range(1, 17):
            func = stack_patches.get(f"swap{n}")
            if func is not None:
                try:
                    op_impl[ops_enum(0x8F + n)] = func
                except ValueError:
                    pass
        pop_func = stack_patches.get("pop")
        if pop_func is not None:
            try:
                op_impl[ops_enum(0x50)] = pop_func
            except ValueError:
                pass

        # Patch memory/copy opcodes (codecopy, etc.)
        mem_patches = _make_optimized_memory_ops(name)
        try:
            env_mod = import_module(
                f"ethereum.forks.{name}.vm.instructions.environment"
            )
        except (ImportError, ModuleNotFoundError):
            env_mod = None
        if env_mod is not None:
            for attr, value in mem_patches.items():
                if hasattr(env_mod, attr):
                    setattr(env_mod, attr, value)
            codecopy_func = mem_patches.get("codecopy")
            if codecopy_func is not None:
                try:
                    op_impl[ops_enum(0x39)] = codecopy_func
                except ValueError:
                    pass

        # Replace interpreter inner loop (mypyc-compiled if available)
        patch_interpreter_loop(name)


def monkey_patch_optimized_state_db(
    fork_name: str, state_path: Optional[str]
) -> None:
    """
    Replace the state interface with one that supports high performance
    updates and storing state in a database.

    This function must be called before the state interface is imported
    anywhere.
    """
    from .state_db import get_optimized_state_patches

    slow_state = cast(
        Any,
        import_module("ethereum.forks." + fork_name + ".state"),
    )

    optimized_state_db_patches = get_optimized_state_patches(fork_name)

    for name, value in optimized_state_db_patches.items():
        setattr(slow_state, name, value)

    if state_path is not None:
        slow_state.State.default_path = state_path


def monkey_patch_optimized_spec(fork_name: str) -> None:
    """
    Replace the ethash implementation with one that supports higher
    performance.

    This function must be called before the spec interface is imported
    anywhere.
    """
    from .fork import get_optimized_pow_patches

    slow_spec = import_module("ethereum.forks." + fork_name + ".fork")

    optimized_pow_patches = get_optimized_pow_patches(fork_name)

    for name, value in optimized_pow_patches.items():
        setattr(slow_spec, name, value)


def monkey_patch(state_path: Optional[str]) -> None:
    """
    Apply all monkey patches to the specification.

    Includes both pure-Python interpreter patches and (if available)
    C-extension state DB and ethash patches.
    """
    monkey_patch_interpreter()

    forks = Hardfork.discover()

    for fork in forks:
        try:
            monkey_patch_optimized_state_db(
                fork.short_name, state_path
            )
        except ImportError:
            pass  # rust_pyspec_glue not installed

        # Only patch the POW code on POW forks
        if fork.consensus.is_pow():
            try:
                monkey_patch_optimized_spec(fork.short_name)
            except ImportError:
                pass  # ethash not installed
