"""Tests for StateAccessLog core data structures and helper functions.

These are unit tests for the pyspec module, not EIP specification tests.
"""

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
    end_frame,
    log_account_read,
    log_account_write,
    log_blockhash_read,
    log_code_read,
    log_storage_read,
    log_storage_write,
    start_frame,
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


def test_start_frame_increments_id():
    """start_frame creates FrameStart and increments counter."""
    log = StateAccessLog()

    frame_id_1 = start_frame(log)
    assert frame_id_1 == Uint(0)
    assert log._next_frame_id == Uint(1)
    assert len(log.operations) == 1
    assert isinstance(log.operations[0], FrameStart)
    assert log.operations[0].frame_id == Uint(0)

    frame_id_2 = start_frame(log)
    assert frame_id_2 == Uint(1)
    assert log._next_frame_id == Uint(2)


def test_end_frame_records_success():
    """end_frame creates FrameEnd with success status."""
    log = StateAccessLog()
    frame_id = start_frame(log)

    end_frame(log, frame_id, success=True)

    assert len(log.operations) == 2
    assert isinstance(log.operations[1], FrameEnd)
    assert log.operations[1].frame_id == frame_id
    assert log.operations[1].success is True


def test_end_frame_records_failure():
    """end_frame creates FrameEnd with failure status."""
    log = StateAccessLog()
    frame_id = start_frame(log)

    end_frame(log, frame_id, success=False)

    assert log.operations[1].success is False


def test_log_account_read_deduplicates_code():
    """log_account_read stores code once, references by hash."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    code = b"contract code here"
    code_hash = keccak256(code)

    log_account_read(log, address, U256(100), Uint(1), code)

    assert len(log.operations) == 1
    assert isinstance(log.operations[0], AccountRead)
    assert log.operations[0].code_hash == code_hash
    assert log.codes[code_hash] == code

    # Second read with same code doesn't duplicate
    log_account_read(log, address, U256(200), Uint(2), code)
    assert len(log.codes) == 1


def test_log_account_write_balance():
    """log_account_write records balance changes."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")

    log_account_write(log, address, "balance", U256(500))

    assert len(log.operations) == 1
    op = log.operations[0]
    assert isinstance(op, AccountWrite)
    assert op.field == "balance"
    assert op.new_value == U256(500)


def test_log_account_write_code_deduplicates():
    """log_account_write for code stores and references by hash."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    new_code = b"new contract code"
    code_hash = keccak256(new_code)

    log_account_write(log, address, "code", new_code)

    op = log.operations[0]
    assert op.new_value == code_hash
    assert log.codes[code_hash] == new_code


def test_log_storage_read():
    """log_storage_read records storage slot access."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    slot = Bytes32(b"\x00" * 31 + b"\x05")

    log_storage_read(log, address, slot, U256(123))

    assert len(log.operations) == 1
    op = log.operations[0]
    assert isinstance(op, StorageRead)
    assert op.slot == slot
    assert op.value == U256(123)


def test_log_storage_write():
    """log_storage_write records storage slot modification."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    slot = Bytes32(b"\x00" * 31 + b"\x05")

    log_storage_write(log, address, slot, U256(10), U256(20))

    op = log.operations[0]
    assert isinstance(op, StorageWrite)
    assert op.old_value == U256(10)
    assert op.new_value == U256(20)


def test_log_code_read_deduplicates():
    """log_code_read stores code once."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    code = b"some bytecode"
    code_hash = keccak256(code)

    log_code_read(log, address, code)

    op = log.operations[0]
    assert isinstance(op, CodeRead)
    assert op.code_hash == code_hash
    assert log.codes[code_hash] == code


def test_log_blockhash_read():
    """log_blockhash_read stores block hash."""
    log = StateAccessLog()
    block_hash = bytes.fromhex("ab" * 32)

    log_blockhash_read(log, Uint(1000), block_hash)

    op = log.operations[0]
    assert isinstance(op, BlockHashRead)
    assert op.block_number == Uint(1000)
    assert log.headers[Uint(1000)] == block_hash
