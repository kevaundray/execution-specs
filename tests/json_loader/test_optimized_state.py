"""Tests for the optimized state implementation."""

import sys

import pytest
from ethereum_types.numeric import U256, Uint

import ethereum.forks.frontier.state as slow_state
from ethereum.forks.frontier.fork_types import EMPTY_ACCOUNT as FRONTIER_EMPTY
from ethereum.forks.tangerine_whistle.utils.hexadecimal import hex_to_address
from ethereum.state import EMPTY_CODE_HASH, Account, BlockDiff

try:
    from ethereum_optimized.state_db import (
        State,
        apply_changes_to_state,
        set_account,
        set_storage,
        state_root,
    )

    HAS_OPTIMIZED = True
except ImportError:
    HAS_OPTIMIZED = False


ADDRESS_FOO = hex_to_address(
    "0x00000000219ab540356cbb839cbe05303d7705fa"
)
STORAGE_FOO = U256(101).to_be_bytes32()

EMPTY = Account(
    nonce=Uint(0), balance=U256(0), code_hash=EMPTY_CODE_HASH
)

skip_no_optimized = pytest.mark.skipif(
    not HAS_OPTIMIZED,
    reason="missing dependency (use `pip install 'ethereum[optimized]'`)",
)


@skip_no_optimized
def test_storage_key() -> None:
    """
    Test that optimized state storage operations match the normal
    implementation.
    """
    opt = State()
    set_account(opt, ADDRESS_FOO, EMPTY)
    set_storage(opt, ADDRESS_FOO, STORAGE_FOO, U256(42))

    normal = slow_state.State()
    slow_state.set_account(normal, ADDRESS_FOO, FRONTIER_EMPTY)
    slow_state.set_storage(normal, ADDRESS_FOO, STORAGE_FOO, U256(42))

    assert opt.get_storage(ADDRESS_FOO, STORAGE_FOO) == U256(42)
    assert state_root(opt) == slow_state.state_root(normal)


@skip_no_optimized
def test_apply_changes() -> None:
    """
    Test that apply_changes_to_state flushes diffs to the LMDB state
    and produces the same root as the trie-based state.
    """
    opt = State()
    diff = BlockDiff(
        account_changes={ADDRESS_FOO: EMPTY},
        storage_changes={ADDRESS_FOO: {STORAGE_FOO: U256(99)}},
        code_changes={},
    )
    apply_changes_to_state(opt, diff)

    normal = slow_state.State()
    slow_state.set_account(normal, ADDRESS_FOO, FRONTIER_EMPTY)
    slow_state.set_storage(normal, ADDRESS_FOO, STORAGE_FOO, U256(99))

    assert state_root(opt) == slow_state.state_root(normal)


@skip_no_optimized
def test_prestate_protocol() -> None:
    """
    Test that the optimized State satisfies the PreState protocol read
    methods.
    """
    opt = State()
    set_account(opt, ADDRESS_FOO, EMPTY)
    set_storage(opt, ADDRESS_FOO, STORAGE_FOO, U256(42))

    assert opt.get_account_optional(ADDRESS_FOO) is not None
    assert opt.get_storage(ADDRESS_FOO, STORAGE_FOO) == U256(42)
    assert opt.get_code(EMPTY_CODE_HASH) == b""
    assert opt.account_has_storage(ADDRESS_FOO) is True
