"""Differential tests: eth_trie_rs state root vs the pure-Python spec."""

import pytest

from ethereum.merkle_patricia_trie import EMPTY_TRIE_ROOT
from ethereum.state import (
    EMPTY_CODE_HASH,
    Account,
    State,
    set_account,
    set_storage,
    state_root,
)
from ethereum_types.bytes import Bytes20, Bytes32
from ethereum_types.numeric import U256, Uint

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
    assert eth_trie_rs.state_root([], {}) == bytes(EMPTY_TRIE_ROOT)


def test_single_account_no_storage() -> None:
    addr = Bytes20(b"\x11" * 20)
    accounts = {addr: Account(Uint(7), U256(1000), EMPTY_CODE_HASH)}
    assert rust_state_root(accounts, {}) == python_state_root(accounts, {})
