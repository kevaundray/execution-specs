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

from ethereum.crypto.hash import Hash32, keccak256

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


def start_frame(log: StateAccessLog) -> Uint:
    """
    Start a new call frame.

    Parameters
    ----------
    log :
        The state access log.

    Returns
    -------
    frame_id : Uint
        The unique ID for this frame.
    """
    frame_id = log._next_frame_id
    log._next_frame_id = Uint(int(frame_id) + 1)
    log.operations.append(FrameStart(frame_id))
    return frame_id


def end_frame(log: StateAccessLog, frame_id: Uint, success: bool) -> None:
    """
    End a call frame with success/failure status.

    Parameters
    ----------
    log :
        The state access log.
    frame_id :
        The frame ID from start_frame.
    success :
        Whether the frame completed successfully.
    """
    log.operations.append(FrameEnd(frame_id, success))


def log_account_read(
    log: StateAccessLog,
    address: Address,
    balance: U256,
    nonce: Uint,
    code: Bytes,
) -> None:
    """
    Log an account read, deduplicating code.

    Parameters
    ----------
    log :
        The state access log.
    address :
        The account address.
    balance :
        The account balance.
    nonce :
        The account nonce.
    code :
        The account code bytes.
    """
    code_hash = keccak256(code)
    if code_hash not in log.codes:
        log.codes[code_hash] = code
    log.operations.append(AccountRead(address, balance, nonce, code_hash))


def log_account_write(
    log: StateAccessLog,
    address: Address,
    field: str,
    new_value: Union[U256, Uint, Bytes],
) -> None:
    """
    Log an account field write.

    Parameters
    ----------
    log :
        The state access log.
    address :
        The account address.
    field :
        The field being written ("balance", "nonce", or "code").
    new_value :
        The new value (code is stored by hash).
    """
    assert field in ("balance", "nonce", "code"), f"Invalid field: {field}"
    if field == "code":
        assert isinstance(new_value, (bytes, Bytes))
        code_hash = keccak256(new_value)
        if code_hash not in log.codes:
            log.codes[code_hash] = new_value
        new_value = code_hash
    log.operations.append(AccountWrite(address, field, new_value))


def log_storage_read(
    log: StateAccessLog,
    address: Address,
    slot: Bytes32,
    value: U256,
) -> None:
    """
    Log a storage read.

    Parameters
    ----------
    log :
        The state access log.
    address :
        The account address.
    slot :
        The storage slot.
    value :
        The value read.
    """
    log.operations.append(StorageRead(address, slot, value))


def log_storage_write(
    log: StateAccessLog,
    address: Address,
    slot: Bytes32,
    old_value: U256,
    new_value: U256,
) -> None:
    """
    Log a storage write.

    Parameters
    ----------
    log :
        The state access log.
    address :
        The account address.
    slot :
        The storage slot.
    old_value :
        The previous value.
    new_value :
        The new value.
    """
    log.operations.append(StorageWrite(address, slot, old_value, new_value))


def log_code_read(
    log: StateAccessLog,
    address: Address,
    code: Bytes,
) -> None:
    """
    Log a code read (EXTCODECOPY, etc.), deduplicating.

    Parameters
    ----------
    log :
        The state access log.
    address :
        The account address.
    code :
        The code bytes.
    """
    code_hash = keccak256(code)
    if code_hash not in log.codes:
        log.codes[code_hash] = code
    log.operations.append(CodeRead(address, code_hash))


def log_blockhash_read(
    log: StateAccessLog,
    block_number: Uint,
    block_hash: Hash32,
) -> None:
    """
    Log a BLOCKHASH read.

    Parameters
    ----------
    log :
        The state access log.
    block_number :
        The block number queried.
    block_hash :
        The block hash returned.
    """
    if block_number in log.headers:
        assert log.headers[block_number] == block_hash
    else:
        log.headers[block_number] = block_hash
    log.operations.append(BlockHashRead(block_number))
