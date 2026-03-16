"""
Optimized Interpreter Functions.

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

Pure-Python optimized versions of hot-path interpreter functions.
These replace the spec versions via monkey patching for better
performance without requiring external dependencies.

The main bottlenecks in the spec interpreter (per cProfile):

1. ``Uint.__init__`` (16% of EVM time) - pervasive Uint construction.
2. ``get_valid_jump_destinations`` (27% cumulative) - Uint + Enum per byte.
3. ``Enum.__call__`` + dict lookup (7%) - opcode dispatch overhead.
4. ``evm_trace`` (3%) - trace calls even when discarding.
5. ``charge_gas`` (5%) - trace overhead inside gas charging.
"""

from importlib import import_module
from typing import Any, Callable, Dict, Set

from ethereum_types.bytes import Bytes
from ethereum_types.numeric import Uint

import ethereum.trace as _trace_mod


def _make_optimized_get_valid_jump_destinations() -> (
    Callable[[Bytes], Set[Uint]]
):
    """
    Create an optimized ``get_valid_jump_destinations``.

    Use plain ``int`` internally; only wrap in ``Uint`` for the rare
    JUMPDEST insertions.
    """
    jumpdest = 0x5B
    push1 = 0x60
    push32 = 0x7F

    def get_valid_jump_destinations(code: Bytes) -> Set[Uint]:
        valid_jump_destinations: set = set()
        pc = 0
        code_len = len(code)
        add = valid_jump_destinations.add

        while pc < code_len:
            byte = code[pc]
            if byte == jumpdest:
                add(Uint(pc))
            elif push1 <= byte <= push32:
                pc += byte - push1 + 1
            pc += 1

        return valid_jump_destinations

    return get_valid_jump_destinations


def _make_optimized_charge_gas(fork_name: str) -> Callable:
    """
    Create an optimized ``charge_gas`` that skips tracing when the
    active tracer is the discard tracer.
    """
    fork_gas = import_module(f"ethereum.forks.{fork_name}.vm.gas")
    out_of_gas_error = fork_gas.OutOfGasError
    discard = _trace_mod.discard_evm_trace
    _new = object.__new__

    def charge_gas(evm: Any, amount: Uint) -> None:
        tracer = _trace_mod._evm_trace
        if tracer is not discard:
            tracer(evm, _trace_mod.GasAndRefund(int(amount)))

        # Inline the comparison and subtraction using raw ._number
        # to bypass Uint.__lt__ (isinstance + compare) and
        # Uint.__isub__ (isinstance + object.__new__ + subtract).
        gas = evm.gas_left
        amt = amount._number
        if gas._number < amt:
            raise out_of_gas_error
        new_gas = _new(type(gas))
        new_gas._number = gas._number - amt
        evm.gas_left = new_gas

    return charge_gas


def _make_optimized_evm_trace() -> Callable:
    """
    Create an ``evm_trace`` that short-circuits when the discard
    tracer is active, avoiding the function call overhead entirely.
    """
    discard = _trace_mod.discard_evm_trace

    def evm_trace(evm: object, event: Any) -> None:
        tracer = _trace_mod._evm_trace
        if tracer is not discard:
            tracer(evm, event)

    return evm_trace



def _make_optimized_memory_ops(
    fork_name: str,
) -> Dict[str, Any]:
    """
    Create optimized memory and copy instructions.

    ``buffer_read`` creates 2 ``Uint`` objects, slices, copies to
    ``bytes``, then right-pads.  ``codecopy`` adds more ``Uint``
    constructions for gas calculation. This version uses plain ``int``
    throughout.
    """
    from ethereum_types.numeric import U256, Uint

    from ethereum_optimized.evm_utils import (
        buffer_read_int,
        calculate_gas_extend_memory_int,
        ceil32_int,
    )

    fork_gas = import_module(f"ethereum.forks.{fork_name}.vm.gas")
    fork_stack = import_module(f"ethereum.forks.{fork_name}.vm.stack")

    gas_copy = int(fork_gas.GAS_COPY)
    gas_very_low = fork_gas.GAS_VERY_LOW
    stack_pop = fork_stack.pop
    _new = object.__new__
    _uint_1 = Uint(1)

    gas_mod = import_module(f"ethereum.forks.{fork_name}.vm.gas")

    def fast_memory_write(
        memory: bytearray, start: int, value: bytes
    ) -> None:
        """Write to memory using plain int index."""
        memory[start : start + len(value)] = value

    def codecopy(evm: Any) -> None:
        """Optimized codecopy using int arithmetic throughout."""
        # STACK
        mem_start = stack_pop(evm.stack)._number
        code_start = stack_pop(evm.stack)._number
        size = stack_pop(evm.stack)._number

        # GAS — all int arithmetic, no Uint construction
        words = ceil32_int(size) // 32
        copy_gas_cost = gas_copy * words
        gas_to_pay, expand_by = calculate_gas_extend_memory_int(
            len(evm.memory), [(mem_start, size)]
        )
        total_gas = Uint(
            gas_very_low._number + copy_gas_cost + gas_to_pay
        )
        gas_mod.charge_gas(evm, total_gas)

        # OPERATION
        if expand_by > 0:
            evm.memory += b"\x00" * expand_by
        if size > 0:
            value = buffer_read_int(evm.code, code_start, size)
            evm.memory[mem_start : mem_start + len(value)] = value

        # PROGRAM COUNTER
        evm.pc += _uint_1

    def extcodecopy(evm: Any) -> None:
        """Optimized extcodecopy — same pattern as codecopy."""
        # This is more complex (needs account lookup), so we only
        # optimize the simpler codecopy for now.
        raise NotImplementedError

    patches: Dict[str, Any] = {
        "codecopy": codecopy,
    }
    return patches


def _make_optimized_stack_ops(
    fork_name: str,
) -> Dict[str, Any]:
    """
    Create optimized stack instructions for a fork.

    The spec ``push_n`` calls ``buffer_read`` → ``right_pad_zero_bytes``
    → ``U256.from_be_bytes``, creating ~8 intermediate ``Uint``/``U256``
    objects per invocation. This version reads code bytes directly with
    plain ``int`` indexing and constructs a single ``U256`` result.
    """
    from functools import partial

    from ethereum_types.numeric import U256, Uint

    fork_gas = import_module(f"ethereum.forks.{fork_name}.vm.gas")
    fork_stack = import_module(f"ethereum.forks.{fork_name}.vm.stack")
    fork_exceptions = import_module(
        f"ethereum.forks.{fork_name}.vm.exceptions"
    )

    gas_base = fork_gas.GAS_BASE
    gas_very_low = fork_gas.GAS_VERY_LOW
    stack_push = fork_stack.push
    stack_pop = fork_stack.pop
    stack_underflow = fork_exceptions.StackUnderflowError

    # Pre-compute pc increments for push0..push32 to avoid Uint
    # construction at runtime. push_n(num_bytes=N) increments by N+1.
    _pc_increments = [Uint(n + 1) for n in range(33)]
    _uint_1 = Uint(1)
    _u256_0 = U256(0)

    # Get the charge_gas that will be active (may already be patched)
    # We import it fresh each time push_n runs via closure over the module.
    gas_mod = import_module(f"ethereum.forks.{fork_name}.vm.gas")

    def push_n(evm: Any, num_bytes: int) -> None:
        """Optimized push_n: read code bytes directly, skip buffer_read."""
        gas_mod.charge_gas(
            evm, gas_base if num_bytes == 0 else gas_very_low
        )

        if num_bytes == 0:
            stack_push(evm.stack, _u256_0)
        else:
            pc = evm.pc._number  # Access raw int directly
            start = pc + 1
            end = start + num_bytes
            code = evm.code
            code_len = len(code)

            if end <= code_len:
                # Fast path: all bytes within code bounds
                data = int.from_bytes(code[start:end], "big")
            else:
                # Slow path: need zero-padding beyond code end
                available = code_len - start
                if available > 0:
                    raw = code[start:code_len]
                else:
                    raw = b""
                data = int.from_bytes(
                    raw.ljust(num_bytes, b"\x00"), "big"
                )

            stack_push(evm.stack, U256(data))

        evm.pc += _pc_increments[num_bytes]

    def dup_n(evm: Any, item_number: int) -> None:
        """Optimized dup_n: avoid extra Uint(1) construction."""
        gas_mod.charge_gas(evm, gas_very_low)
        stk = evm.stack
        if item_number >= len(stk):
            raise stack_underflow
        stack_push(stk, stk[len(stk) - 1 - item_number])
        evm.pc += _uint_1

    def swap_n(evm: Any, item_number: int) -> None:
        """Optimized swap_n: avoid extra Uint(1) construction."""
        gas_mod.charge_gas(evm, gas_very_low)
        stk = evm.stack
        if item_number >= len(stk):
            raise stack_underflow
        stk[-1], stk[-1 - item_number] = (
            stk[-1 - item_number],
            stk[-1],
        )
        evm.pc += _uint_1

    def pop_op(evm: Any) -> None:
        """Optimized pop: avoid extra Uint(1) construction."""
        stack_pop(evm.stack)
        gas_mod.charge_gas(evm, gas_base)
        evm.pc += _uint_1

    patches: Dict[str, Any] = {}

    # push0..push32
    for n in range(33):
        patches[f"push{n}"] = partial(push_n, num_bytes=n)
    # Also patch the underlying push_n for any direct callers
    patches["push_n"] = push_n

    # dup1..dup16
    for n in range(16):
        patches[f"dup{n + 1}"] = partial(dup_n, item_number=n)
    patches["dup_n"] = dup_n

    # swap1..swap16
    for n in range(1, 17):
        patches[f"swap{n}"] = partial(swap_n, item_number=n)
    patches["swap_n"] = swap_n

    # pop
    patches["pop"] = pop_op

    return patches


def get_optimized_runtime_patches(
    fork_name: str,
) -> Dict[str, Any]:
    """
    Return optimized functions for a fork's ``runtime`` module.
    """
    return {
        "get_valid_jump_destinations": (
            _make_optimized_get_valid_jump_destinations()
        ),
    }


def _make_optimized_gas_funcs(fork_name: str) -> Dict[str, Any]:
    """
    Create optimized gas calculation functions that use the compiled
    ``evm_utils`` module (native ``int`` arithmetic compiled via mypyc)
    instead of ``Uint`` objects.
    """
    try:
        from ethereum_optimized.evm_utils import (
            calculate_gas_extend_memory_int,
            calculate_memory_gas_cost_int,
            ceil32_int,
        )
    except ImportError:
        return {}

    from ethereum_types.numeric import U256, Uint

    fork_gas = import_module(f"ethereum.forks.{fork_name}.vm.gas")

    # Wrap ceil32 to convert Uint ↔ int at boundaries
    def fast_ceil32(value: Uint) -> Uint:
        return Uint(ceil32_int(value._number))

    def fast_calculate_memory_gas_cost(size_in_bytes: Uint) -> Uint:
        return Uint(calculate_memory_gas_cost_int(size_in_bytes._number))

    def fast_calculate_gas_extend_memory(
        memory: bytearray, extensions: Any
    ) -> Any:
        int_extensions = [
            (int(start), int(size)) for start, size in extensions
        ]
        gas, extend = calculate_gas_extend_memory_int(
            len(memory), int_extensions
        )
        return fork_gas.ExtendMemory(Uint(gas), Uint(extend))

    return {
        "calculate_memory_gas_cost": fast_calculate_memory_gas_cost,
        "calculate_gas_extend_memory": fast_calculate_gas_extend_memory,
    }


def get_optimized_gas_patches(
    fork_name: str,
) -> Dict[str, Any]:
    """
    Return optimized functions for a fork's ``gas`` module.
    """
    patches: Dict[str, Any] = {
        "charge_gas": _make_optimized_charge_gas(fork_name),
    }
    patches.update(_make_optimized_gas_funcs(fork_name))
    return patches


def get_optimized_stack_patches(
    fork_name: str,
) -> Dict[str, Any]:
    """
    Return optimized stack instruction functions for a fork.
    """
    return _make_optimized_stack_ops(fork_name)


def get_optimized_trace_patches() -> Dict[str, Any]:
    """
    Return optimized functions for the ``ethereum.trace`` module.
    """
    return {
        "evm_trace": _make_optimized_evm_trace(),
    }


def patch_interpreter_loop(fork_name: str) -> bool:
    """
    Replace the interpreter's inner bytecode loop with a compiled
    version for a single fork.

    The inner loop (``while evm.running ...``) is byte-for-byte
    identical across all 24 forks. This function replaces it with
    ``execute_bytecode`` from ``ethereum_optimized.evm_loop`` (compiled via
    mypyc), which uses ``list[256]`` dispatch instead of ``Ops``
    enum construction + ``dict`` lookup.

    Returns True if the patch was applied, False if the compiled
    module is not available.
    """
    try:
        from ethereum_optimized.evm_loop import execute_bytecode
    except ImportError:
        return False

    import inspect
    import textwrap

    interpreter_mod = import_module(
        f"ethereum.forks.{fork_name}.vm.interpreter"
    )
    instructions_mod = import_module(
        f"ethereum.forks.{fork_name}.vm.instructions"
    )
    exceptions_mod = import_module(
        f"ethereum.forks.{fork_name}.vm.exceptions"
    )

    # Build dispatch table from current op_implementation (which may
    # already include our optimized push_n/dup_n/etc.).
    dispatch = [None] * 256
    for op_enum, func in instructions_mod.op_implementation.items():
        dispatch[op_enum.value] = func

    invalid_opcode_cls = exceptions_mod.InvalidOpcode

    # The loop text is identical across all forks:
    old_loop = (
        "while evm.running and evm.pc < ulen(evm.code):\n"
        "                try:\n"
        "                    op = Ops(evm.code[evm.pc])\n"
        "                except ValueError as e:\n"
        "                    raise InvalidOpcode(evm.code[evm.pc])"
        " from e\n"
        "\n"
        "                evm_trace(evm, OpStart(op))\n"
        "                op_implementation[op](evm)\n"
        "                evm_trace(evm, OpEnd())\n"
        "\n"
        "            evm_trace(evm, EvmStop(Ops.STOP))"
    )

    new_loop = (
        "try:\n"
        "                _execute_bytecode("
        "evm.code, _dispatch, evm)\n"
        "            except ValueError as _e:\n"
        "                raise _InvalidOpcode(_e.args[0])"
        " from _e"
    )

    original = interpreter_mod.process_message
    src = inspect.getsource(original)
    src = textwrap.dedent(src)

    if old_loop not in src:
        return False

    new_src = src.replace(old_loop, new_loop)

    # Build the namespace for exec — include everything the
    # original function needs plus our additions.
    ns = dict(vars(interpreter_mod))
    ns["_execute_bytecode"] = execute_bytecode
    ns["_dispatch"] = dispatch
    ns["_InvalidOpcode"] = invalid_opcode_cls

    exec(compile(new_src, f"<optimized:{fork_name}>", "exec"), ns)
    interpreter_mod.process_message = ns["process_message"]
    return True


def patch_uint_fast_arithmetic() -> None:
    """
    Monkey-patch ``Uint`` arithmetic to bypass ``__init__`` validation.

    The standard ``Uint.__iadd__`` calls ``Uint(result)`` which goes
    through ``__init__`` → ``int(value)`` → ``_in_range(value)``.
    Since adding/subtracting two valid Uints always produces a valid
    Uint (non-negative for add, checked for sub), we can skip the
    ``__init__`` validation and construct via ``object.__new__``.
    """
    from ethereum_types.numeric import U256, Uint

    _new = object.__new__

    # Use `type(x) is cls` instead of `isinstance(x, cls)`.
    # isinstance traverses the MRO (14.7s for 206M calls in
    # benchmarks). `type() is` is a single pointer comparison.

    def _fast_iadd(self, right):  # type: ignore
        cls = type(self)
        if type(right) is not cls:
            return NotImplemented
        obj = _new(cls)
        obj._number = self._number + right._number
        return obj

    def _fast_add(self, right):  # type: ignore
        cls = type(self)
        if type(right) is not cls:
            return NotImplemented
        obj = _new(cls)
        obj._number = self._number + right._number
        return obj

    def _fast_isub(self, right):  # type: ignore
        cls = type(self)
        if type(right) is not cls:
            return NotImplemented
        if right._number > self._number:
            raise OverflowError()
        obj = _new(cls)
        obj._number = self._number - right._number
        return obj

    def _fast_sub(self, right):  # type: ignore
        cls = type(self)
        if type(right) is not cls:
            return NotImplemented
        if self._number < right._number:
            raise OverflowError()
        obj = _new(cls)
        obj._number = self._number - right._number
        return obj

    def _fast_uint_init(self, value):  # type: ignore
        if type(value) is int:
            if value < 0:
                raise OverflowError()
            self._number = value
        else:
            int_value = int(value)
            if not self._in_range(int_value):
                raise OverflowError()
            self._number = int_value

    def _fast_u256_init(self, value):  # type: ignore
        if type(value) is int:
            if value < 0 or value > U256.MAX_VALUE._number:
                raise OverflowError()
            self._number = value
        else:
            int_value = int(value)
            if not self._in_range(int_value):
                raise OverflowError()
            self._number = int_value

    def _fast_u256_eq(self, other):  # type: ignore
        # Fast path for U256 == U256 (most common), then U256 == int.
        if type(other) is type(self):
            return self._number == other._number
        if hasattr(other, "_number"):
            return self._number == other._number
        if type(other) is int:
            return self._number == other
        # Fall back to original for exotic types (SupportsInt, etc.)
        try:
            return self._number == int(other)
        except (TypeError, ValueError):
            return NotImplemented

    # Apply to Uint
    Uint.__iadd__ = _fast_iadd
    Uint.__add__ = _fast_add
    Uint.__isub__ = _fast_isub
    Uint.__sub__ = _fast_sub
    Uint.__init__ = _fast_uint_init

    # Apply to U256 — init and equality
    U256.__init__ = _fast_u256_init
    U256.__eq__ = _fast_u256_eq

    # Patch U256 arithmetic with mypyc-compiled primitives.
    # Each compiled function operates on raw int, so the Python
    # wrapper just extracts _number and wraps the result.
    try:
        from ethereum_optimized.evm_u256 import (
            u256_addmod,
            u256_floordiv,
            u256_mod,
            u256_mul,
            u256_mulmod,
            u256_wrapping_add,
            u256_wrapping_mul,
            u256_wrapping_sub,
        )

        def _u256_mod(self, other):  # type: ignore
            if type(other) is not type(self):
                return NotImplemented
            obj = _new(type(self))
            obj._number = u256_mod(self._number, other._number)
            return obj

        def _u256_mul(self, other):  # type: ignore
            if type(other) is not type(self):
                return NotImplemented
            obj = _new(type(self))
            obj._number = u256_mul(self._number, other._number)
            return obj

        def _u256_floordiv(self, other):  # type: ignore
            if type(other) is not type(self):
                return NotImplemented
            obj = _new(type(self))
            obj._number = u256_floordiv(
                self._number, other._number
            )
            return obj

        def _u256_wrapping_add(self, other):  # type: ignore
            obj = _new(type(self))
            obj._number = u256_wrapping_add(
                self._number, other._number
            )
            return obj

        def _u256_wrapping_sub(self, other):  # type: ignore
            obj = _new(type(self))
            obj._number = u256_wrapping_sub(
                self._number, other._number
            )
            return obj

        U256.__mod__ = _u256_mod
        U256.__mul__ = _u256_mul
        U256.__floordiv__ = _u256_floordiv
        U256.wrapping_add = _u256_wrapping_add
        U256.wrapping_sub = _u256_wrapping_sub

    except ImportError:
        pass  # Compiled module not available
