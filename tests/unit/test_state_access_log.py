"""Tests for StateAccessLog core data structures."""

import pytest
from ethereum_types.bytes import Bytes32
from ethereum_types.numeric import U256, Uint

from ethereum.crypto.hash import keccak256
from ethereum.forks.amsterdam.state_access_log import (
    AccountRead,
    AccountWrite,
    CodeRead,
    BlockHashRead,
    FrameEnd,
    FrameStart,
    StateAccessLog,
    StorageRead,
    StorageWrite,
)


def test_state_access_log_initialization():
    """StateAccessLog initializes with empty collections."""
    log = StateAccessLog()
    assert log.operations == []
    assert log.codes == {}
    assert log.headers == {}
    assert log._next_frame_id == Uint(0)


def test_frame_start_creation():
    """FrameStart stores frame_id."""
    frame = FrameStart(frame_id=Uint(5))
    assert frame.frame_id == Uint(5)


def test_frame_end_creation():
    """FrameEnd stores frame_id and success status."""
    frame = FrameEnd(frame_id=Uint(3), success=True)
    assert frame.frame_id == Uint(3)
    assert frame.success is True


def test_account_read_creation():
    """AccountRead stores address, balance, nonce, code_hash."""
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    code_hash = keccak256(b"code")
    op = AccountRead(
        address=address,
        balance=U256(1000),
        nonce=Uint(5),
        code_hash=code_hash,
    )
    assert op.address == address
    assert op.balance == U256(1000)
    assert op.nonce == Uint(5)
    assert op.code_hash == code_hash


def test_storage_read_creation():
    """StorageRead stores address, slot, value."""
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    slot = Bytes32(b"\x00" * 31 + b"\x01")
    op = StorageRead(address=address, slot=slot, value=U256(42))
    assert op.address == address
    assert op.slot == slot
    assert op.value == U256(42)


def test_storage_write_creation():
    """StorageWrite stores address, slot, old_value, new_value."""
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    slot = Bytes32(b"\x00" * 31 + b"\x01")
    op = StorageWrite(
        address=address,
        slot=slot,
        old_value=U256(10),
        new_value=U256(20),
    )
    assert op.old_value == U256(10)
    assert op.new_value == U256(20)


def test_account_write_creation():
    """AccountWrite stores address, field, and new_value."""
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    op = AccountWrite(address=address, field="balance", new_value=U256(500))
    assert op.address == address
    assert op.field == "balance"
    assert op.new_value == U256(500)


def test_code_read_creation():
    """CodeRead stores address and code_hash."""
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    code_hash = keccak256(b"contract code")
    op = CodeRead(address=address, code_hash=code_hash)
    assert op.address == address
    assert op.code_hash == code_hash


def test_block_hash_read_creation():
    """BlockHashRead stores block_number."""
    op = BlockHashRead(block_number=Uint(12345))
    assert op.block_number == Uint(12345)
