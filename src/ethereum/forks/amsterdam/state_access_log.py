"""
State Access Log for EVM Execution.

A flat operation log that captures all state accesses during execution.
Used to derive ExecutionWitness (stateless proofs) and Block Access Lists.

Frame markers (FrameStart/FrameEnd) track call hierarchy for revert handling.
Large data (code, headers) is deduplicated and stored separately.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Union

from ethereum_types.bytes import Bytes, Bytes32
from ethereum_types.frozen import slotted_freezable
from ethereum_types.numeric import U256, Uint

from ethereum.crypto.hash import Hash32

from .fork_types import Address


# Frame markers
@slotted_freezable
@dataclass
class FrameStart:
    """Marks the start of a call frame."""

    frame_id: Uint


@slotted_freezable
@dataclass
class FrameEnd:
    """Marks the end of a call frame with success/failure status."""

    frame_id: Uint
    success: bool


# State access operations
@slotted_freezable
@dataclass
class AccountRead:
    """Records reading account state (balance, nonce, code)."""

    address: Address
    balance: U256
    nonce: Uint
    code_hash: Hash32


@slotted_freezable
@dataclass
class AccountWrite:
    """Records writing to an account field."""

    address: Address
    field: str  # "balance" | "nonce" | "code"
    new_value: Union[U256, Uint, Hash32]


@slotted_freezable
@dataclass
class StorageRead:
    """Records reading a storage slot."""

    address: Address
    slot: Bytes32
    value: U256


@slotted_freezable
@dataclass
class StorageWrite:
    """Records writing to a storage slot."""

    address: Address
    slot: Bytes32
    old_value: U256
    new_value: U256


@slotted_freezable
@dataclass
class CodeRead:
    """Records reading contract code (EXTCODECOPY, etc.)."""

    address: Address
    code_hash: Hash32


@slotted_freezable
@dataclass
class BlockHashRead:
    """Records BLOCKHASH opcode access."""

    block_number: Uint


# Union of all operation types
Operation = Union[
    FrameStart,
    FrameEnd,
    AccountRead,
    AccountWrite,
    StorageRead,
    StorageWrite,
    CodeRead,
    BlockHashRead,
]


@dataclass
class StateAccessLog:
    """
    Flat log of state operations with deduplicated storage.

    Attributes
    ----------
    operations : List[Operation]
        Sequential log of all state operations.
    codes : Dict[Hash32, Bytes]
        Deduplicated code storage, keyed by keccak256 hash.
    headers : Dict[Uint, Hash32]
        Block hashes accessed via BLOCKHASH, keyed by block number.
    _next_frame_id : Uint
        Counter for generating unique frame IDs.
    """

    operations: List[Operation] = field(default_factory=list)
    codes: Dict[Hash32, Bytes] = field(default_factory=dict)
    headers: Dict[Uint, Hash32] = field(default_factory=dict)
    _next_frame_id: Uint = field(default=Uint(0))
