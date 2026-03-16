"""
Compiled EVM utility functions.

Hot-path utility functions rewritten with native ``int`` for mypyc
compilation. Compile with::

    mypyc src/ethereum/evm_utils.py

Falls back to the Python implementations if not compiled.
"""

from typing import List, Tuple


def ceil32_int(value: int) -> int:
    """Round up to the next multiple of 32."""
    remainder: int = value & 31  # value % 32, but faster
    if remainder == 0:
        return value
    return value + 32 - remainder


def calculate_memory_gas_cost_int(size_in_bytes: int) -> int:
    """Calculate gas cost for memory allocation."""
    size_in_words: int = ceil32_int(size_in_bytes) // 32
    linear_cost: int = size_in_words * 3  # GAS_MEMORY = 3
    quadratic_cost: int = (size_in_words * size_in_words) // 512
    return linear_cost + quadratic_cost


def calculate_gas_extend_memory_int(
    memory_len: int,
    extensions: List[Tuple[int, int]],
) -> Tuple[int, int]:
    """
    Calculate gas to extend memory.

    Return (gas_to_pay, bytes_to_extend).
    """
    size_to_extend: int = 0
    to_be_paid: int = 0
    current_size: int = memory_len

    for start_position, size in extensions:
        if size == 0:
            continue
        before_size: int = ceil32_int(current_size)
        after_size: int = ceil32_int(start_position + size)
        if after_size <= before_size:
            continue

        size_to_extend += after_size - before_size
        already_paid: int = calculate_memory_gas_cost_int(before_size)
        total_cost: int = calculate_memory_gas_cost_int(after_size)
        to_be_paid += total_cost - already_paid
        current_size = after_size

    return (to_be_paid, size_to_extend)


def buffer_read_int(
    buffer: bytes, start: int, size: int
) -> bytes:
    """Read from buffer with zero-padding, using plain int."""
    buf_len: int = len(buffer)
    if start >= buf_len:
        return b"\x00" * size
    end: int = start + size
    if end <= buf_len:
        return buffer[start:end]
    # Partial read + zero padding
    return buffer[start:buf_len] + b"\x00" * (end - buf_len)
