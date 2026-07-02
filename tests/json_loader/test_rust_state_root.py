"""Differential tests: eth_trie_rs state root vs the pure-Python spec."""

import random

import pytest
from ethereum_types.bytes import Bytes20, Bytes32
from ethereum_types.numeric import U256, Uint

from ethereum.merkle_patricia_trie import EMPTY_TRIE_ROOT
from ethereum.state import (
    EMPTY_CODE_HASH,
    Account,
    State,
    set_account,
    set_storage,
    state_root,
)

eth_trie_rs = pytest.importorskip("eth_trie_rs")


def python_state_root(
    accounts: dict[Bytes20, Account],
    storage: dict[Bytes20, dict[Bytes32, U256]],
) -> bytes:
    """Reference root via the spec's ``State``, for a dict-based snapshot."""
    state = State()
    for addr, acct in accounts.items():
        set_account(state, addr, acct)
    for addr, slots in storage.items():
        for slot, value in slots.items():
            set_storage(state, addr, slot, value)
    return bytes(state_root(state))


def rust_state_root(
    accounts: dict[Bytes20, Account],
    storage: dict[Bytes20, dict[Bytes32, U256]],
) -> bytes:
    """Root via the Rust ``eth_trie_rs.state_root`` for the same snapshot."""
    acc_list = [
        (
            bytes(addr),
            acct.nonce.to_be_bytes(),
            acct.balance.to_be_bytes(),
            bytes(acct.code_hash),
        )
        for addr, acct in accounts.items()
    ]
    stg = {
        bytes(addr): [(bytes(k), v.to_be_bytes()) for k, v in slots.items()]
        for addr, slots in storage.items()
    }
    return eth_trie_rs.state_root(acc_list, stg)


def test_empty_state_root() -> None:
    """Empty state hashes to the empty trie root."""
    assert eth_trie_rs.state_root([], {}) == bytes(EMPTY_TRIE_ROOT)


def test_single_account_no_storage() -> None:
    """A lone account with no storage matches the Python root."""
    addr = Bytes20(b"\x11" * 20)
    accounts = {addr: Account(Uint(7), U256(1000), EMPTY_CODE_HASH)}
    assert rust_state_root(accounts, {}) == python_state_root(accounts, {})


def test_accounts_with_storage() -> None:
    """Accounts with populated storage tries match the Python root."""
    a1 = Bytes20(b"\x11" * 20)
    a2 = Bytes20(b"\x22" * 20)
    accounts = {
        a1: Account(Uint(1), U256(5), EMPTY_CODE_HASH),
        a2: Account(Uint(0), U256(0), EMPTY_CODE_HASH),
    }
    storage = {
        a1: {
            Bytes32(b"\x00" * 31 + b"\x01"): U256(42),
            Bytes32(b"\xff" * 32): U256(2**200),
        },
    }
    assert rust_state_root(accounts, storage) == python_state_root(
        accounts, storage
    )


def _rand_state(
    rng: random.Random,
) -> tuple[dict[Bytes20, Account], dict[Bytes20, dict[Bytes32, U256]]]:
    accounts, storage = {}, {}
    n = rng.randint(0, 20)
    for _ in range(n):
        addr = Bytes20(bytes(rng.randrange(256) for _ in range(20)))
        accounts[addr] = Account(
            Uint(rng.randrange(2**64)),
            U256(rng.randrange(2**256)),
            EMPTY_CODE_HASH,
        )
        if rng.random() < 0.5:
            slots = {}
            for _ in range(rng.randint(1, 8)):
                key = Bytes32(bytes(rng.randrange(256) for _ in range(32)))
                slots[key] = U256(rng.randrange(1, 2**256))
            storage[addr] = slots
    return accounts, storage


@pytest.mark.parametrize("seed", range(50))
def test_property_differential(seed: int) -> None:
    """Random states agree between the Rust and Python roots."""
    rng = random.Random(seed)  # explicit seed -> reproducible
    accounts, storage = _rand_state(rng)
    assert rust_state_root(accounts, storage) == python_state_root(
        accounts, storage
    )


def test_shim_matches_pure_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Rust fast path and pure-Python fallback yield equal roots."""
    import ethereum.state as st

    a = Bytes20(b"\x33" * 20)
    accounts = {a: Account(Uint(3), U256(9), EMPTY_CODE_HASH)}
    storage = {a: {Bytes32(b"\x00" * 31 + b"\x07"): U256(123)}}

    backend_root = python_state_root(accounts, storage)  # backend active

    # Setting _eth_trie_rs to None forces the pure-Python fallback path.
    monkeypatch.setattr(st, "_eth_trie_rs", None)
    fallback_root = python_state_root(accounts, storage)

    assert backend_root == fallback_root
