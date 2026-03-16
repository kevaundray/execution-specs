"""
Compiled U256 arithmetic primitives.

Standalone functions operating on native ``int``, compiled via mypyc.
These are wrapped by Python functions and monkey-patched onto the
``U256`` class.

Compile with::

    mypyc src/ethereum/evm_u256.py
"""
from typing import Final

MAX_U256: Final[int] = (1 << 256) - 1


def u256_mod(a: int, b: int) -> int:
    """U256 modulo."""
    if b == 0:
        return 0
    return a % b


def u256_mul(a: int, b: int) -> int:
    """U256 multiply (checked)."""
    return a * b


def u256_floordiv(a: int, b: int) -> int:
    """U256 floor division."""
    if b == 0:
        return 0
    return a // b


def u256_wrapping_add(a: int, b: int) -> int:
    """U256 wrapping addition."""
    return (a + b) & MAX_U256


def u256_wrapping_sub(a: int, b: int) -> int:
    """U256 wrapping subtraction."""
    return (a - b) & MAX_U256


def u256_wrapping_mul(a: int, b: int) -> int:
    """U256 wrapping multiplication."""
    return (a * b) & MAX_U256


def u256_addmod(x: int, y: int, n: int) -> int:
    """(x + y) % n, or 0 if n == 0."""
    if n == 0:
        return 0
    return (x + y) % n


def u256_mulmod(x: int, y: int, n: int) -> int:
    """(x * y) % n, or 0 if n == 0."""
    if n == 0:
        return 0
    return (x * y) % n
