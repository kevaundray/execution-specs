"""
Compiled EVM inner loop.

This module is designed to be compiled with mypyc for maximum
performance. It replaces the hot while-loop in ``process_message``
that dispatches opcodes.

Compile with::

    mypyc src/ethereum/evm_loop.py

The compiled ``.so`` can coexist with the ``.py`` source; Python
will prefer the native extension when available.
"""

from typing import Any, List, Optional


def execute_bytecode(
    code: bytes,
    dispatch: List[Optional[Any]],
    evm: Any,
) -> None:
    """
    Execute EVM bytecode using a pre-built dispatch table.

    Replace the inner ``while evm.running`` loop in
    ``process_message``. Uses ``list`` indexing by opcode byte
    value instead of ``Ops`` enum construction + ``dict`` lookup.
    Skips trace calls (appropriate when the discard tracer is active).

    Parameters
    ----------
    code :
        The bytecode to execute.
    dispatch :
        A list of 256 entries mapping opcode byte values to their
        implementation callables (or ``None`` for invalid opcodes).
    evm :
        The live EVM state object.

    Raises
    ------
    ValueError
        If an invalid opcode (``dispatch[byte] is None``) is
        encountered.
    """
    code_len: int = len(code)

    while evm.running:
        pc_int: int = evm.pc._number
        if pc_int >= code_len:
            break

        byte: int = code[pc_int]
        impl = dispatch[byte]
        if impl is None:
            raise ValueError(byte)

        impl(evm)
