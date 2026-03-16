"""
Optimized State.

.. contents:: Table of Contents
    :backlinks: none
    :local:

Introduction
------------

LMDB-backed ``PreState`` implementation for high-performance state access
during sync. Implements the ``PreState`` protocol from ``ethereum.state``.
"""

import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

try:
    import rust_pyspec_glue
except ImportError as e:
    raise e from Exception(
        "Install with `pip install 'ethereum[optimized]'` to enable this "
        "package"
    )

from ethereum_types.bytes import Bytes, Bytes32
from ethereum_types.numeric import U256, Uint

from ethereum.crypto.hash import Hash32, keccak256
from ethereum.state import (
    EMPTY_CODE_HASH,
    Account,
    Address,
    BlockDiff,
    InternalNode,
    Root,
)

log = logging.getLogger(__name__)


class State:
    """
    LMDB-backed ``PreState`` implementation.

    Implement the ``PreState`` protocol using ``rust_pyspec_glue.DB`` for
    persistent storage.
    """

    default_path: Optional[str] = None

    db: Any
    _code_store: Dict[Hash32, Bytes]

    def __init__(self, path: Optional[str] = None) -> None:
        if path is None:
            path = State.default_path

        log.info("using optimized state db at %s", path)

        self.db = rust_pyspec_glue.DB(path)
        self._code_store = {}
        self.db.begin_mutable()

    def __enter__(self) -> "State":
        """Support with statements."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Support with statements."""
        close_state(self)

    def __eq__(self, other: object) -> bool:
        """Test equality by comparing state roots."""
        if not isinstance(other, State):
            return NotImplemented
        return state_root(self) == state_root(other)

    # -- PreState protocol --------------------------------------------------

    def get_account_optional(
        self, address: Address
    ) -> Optional[Account]:
        """
        Get the account at an address.

        Return ``None`` if there is no account at the address.
        """
        account = self.db.get_account_optional(address)
        if account is not None:
            code = account[2]
            if code:
                code_hash = keccak256(code)
                self._code_store[code_hash] = code
            else:
                code_hash = EMPTY_CODE_HASH
            return Account(
                Uint(account[0]), U256(account[1]), code_hash
            )
        else:
            return None

    def get_storage(
        self, address: Address, key: Bytes32
    ) -> U256:
        """
        Get a storage value.

        Return ``U256(0)`` if the key has not been set.
        """
        return U256(self.db.get_storage(address, key))

    def get_code(self, code_hash: Hash32) -> Bytes:
        """
        Get the bytecode for a given code hash.

        Return ``b""`` for ``EMPTY_CODE_HASH``.
        """
        if code_hash == EMPTY_CODE_HASH:
            return b""
        return self._code_store.get(code_hash, b"")

    def account_has_storage(self, address: Address) -> bool:
        """
        Check whether an account has any storage.

        Only needed for EIP-7610.
        """
        return self.db.has_storage(address)

    def compute_state_root_and_trie_changes(
        self,
        account_changes: Dict[Address, Optional[Account]],
        storage_changes: Dict[Address, Dict[Bytes32, U256]],
    ) -> Tuple[Root, List[InternalNode]]:
        """
        Compute the state root after applying changes to the pre-state.

        Flush changes to the Rust DB layer and compute the root. Return
        an empty list for trie changes (the LMDB backend does not
        surface internal nodes).

        Mark the state as already flushed so that
        ``apply_changes_to_state`` does not re-flush.
        """
        _flush_changes(self, account_changes, storage_changes)
        self._changes_flushed = True
        return self.db.state_root(), []


def close_state(state: State) -> None:
    """Close a state, releasing all resources it holds."""
    state.db.close()
    state.db = None
    del state._code_store


def state_root(state: State) -> Root:
    """Compute the state root of the current state."""
    return state.db.state_root()


def store_code(state: State, code: Bytes) -> Hash32:
    """Store bytecode and return its hash."""
    if not code:
        return EMPTY_CODE_HASH
    code_hash = keccak256(code)
    state._code_store[code_hash] = code
    return code_hash


def set_account(
    state: State,
    address: Address,
    account: Optional[Account],
) -> None:
    """Set an account in the LMDB state."""
    if account is None:
        state.db.set_account(address, None)
    else:
        code = state._code_store.get(account.code_hash, b"")
        state.db.set_account(
            address,
            SimpleNamespace(
                nonce=account.nonce,
                balance=account.balance,
                code=code,
            ),
        )


def set_storage(
    state: State,
    address: Address,
    key: Bytes32,
    value: U256,
) -> None:
    """Set a storage value in the LMDB state."""
    state.db.set_storage(address, key, value)


def apply_changes_to_state(state: State, diff: BlockDiff) -> None:
    """
    Apply block-level diff to the LMDB state for the next block.

    If ``compute_state_root_and_trie_changes`` already flushed the
    account and storage changes, only update the code store.

    Parameters
    ----------
    state :
        The LMDB-backed state.
    diff :
        Account, storage, and code changes to apply.
    """
    state._code_store.update(diff.code_changes)
    if getattr(state, "_changes_flushed", False):
        state._changes_flushed = False
        return
    _flush_changes(
        state, diff.account_changes, diff.storage_changes
    )


def _flush_changes(
    state: State,
    account_changes: Dict[Address, Optional[Account]],
    storage_changes: Dict[Address, Dict[Bytes32, U256]],
) -> None:
    """Send account and storage changes to the Rust layer."""
    for address, account in account_changes.items():
        if account is None:
            state.db.set_account(address, None)
            state.db.destroy_storage(address)
        else:
            code = state._code_store.get(account.code_hash, b"")
            state.db.set_account(
                address,
                SimpleNamespace(
                    nonce=account.nonce,
                    balance=account.balance,
                    code=code,
                ),
            )
    for address, storage in storage_changes.items():
        for key, value in storage.items():
            state.db.set_storage(address, key, value)


# -- DB lifecycle (sync-only) ---------------------------------------------


def get_metadata(state: State, key: Bytes) -> Optional[Bytes]:
    """Get a piece of metadata."""
    return state.db.get_metadata(key)


def set_metadata(
    state: State, key: Bytes, value: Bytes
) -> None:
    """Set a piece of metadata."""
    return state.db.set_metadata(key, value)


def begin_db_transaction(state: State) -> None:
    """
    Start a database transaction. A transaction is automatically started
    when a ``State`` is created.
    """
    state.db.begin_mutable()


def commit_db_transaction(state: State) -> None:
    """Commit the current database transaction."""
    state.db.commit_mutable()


def rollback_db_transaction(state: State) -> None:
    """Rollback the current database transaction."""
    state.db.rollback_mutable()
