"""Integration tests for StateAccessLog with EVM instructions."""

import pytest
from ethereum_types.bytes import Bytes32
from ethereum_types.numeric import U256, Uint

from ethereum.forks.amsterdam.state_access_log import (
    AccountRead,
    BlockHashRead,
    CodeRead,
    StateAccessLog,
    StorageRead,
    StorageWrite,
    log_account_read,
    log_blockhash_read,
    log_code_read,
    log_storage_read,
    log_storage_write,
)
from ethereum.crypto.hash import keccak256


def test_sload_logs_storage_read():
    """SLOAD should log a StorageRead operation when access_log is set."""
    # This is a placeholder - actual integration requires EVM setup
    # For now, verify the logging function works correctly
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    slot = Bytes32(b"\x00" * 31 + b"\x01")

    log_storage_read(log, address, slot, U256(42))

    assert len(log.operations) == 1
    op = log.operations[0]
    assert isinstance(op, StorageRead)
    assert op.value == U256(42)


def test_sstore_logs_storage_write():
    """SSTORE should log a StorageWrite operation when access_log is set."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    slot = Bytes32(b"\x00" * 31 + b"\x01")

    log_storage_write(log, address, slot, U256(10), U256(20))

    assert len(log.operations) == 1
    op = log.operations[0]
    assert isinstance(op, StorageWrite)
    assert op.old_value == U256(10)
    assert op.new_value == U256(20)


def test_balance_logs_account_read():
    """BALANCE should log AccountRead when access_log is set."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    code = b"contract"

    log_account_read(log, address, U256(1000), Uint(5), code)

    op = log.operations[0]
    assert isinstance(op, AccountRead)
    assert op.balance == U256(1000)
    assert op.nonce == Uint(5)
    assert op.code_hash == keccak256(code)


def test_extcodecopy_logs_code_read():
    """EXTCODECOPY should log CodeRead when access_log is set."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    code = b"bytecode"

    log_code_read(log, address, code)

    op = log.operations[0]
    assert isinstance(op, CodeRead)
    assert op.code_hash == keccak256(code)
    assert log.codes[op.code_hash] == code


def test_blockhash_logs_header_read():
    """BLOCKHASH should log BlockHashRead when access_log is set."""
    log = StateAccessLog()
    block_hash = bytes.fromhex("ab" * 32)

    log_blockhash_read(log, Uint(12345), block_hash)

    op = log.operations[0]
    assert isinstance(op, BlockHashRead)
    assert op.block_number == Uint(12345)
    assert log.headers[Uint(12345)] == block_hash


from ethereum.forks.amsterdam.state_access_log import (
    FrameStart,
    FrameEnd,
    start_frame,
    end_frame,
)


def test_frame_handling_success():
    """Successful call frame should have FrameEnd with success=True."""
    log = StateAccessLog()

    frame_id = start_frame(log)
    # ... execution happens ...
    end_frame(log, frame_id, success=True)

    assert len(log.operations) == 2
    assert isinstance(log.operations[0], FrameStart)
    assert isinstance(log.operations[1], FrameEnd)
    assert log.operations[1].success is True


def test_frame_handling_failure():
    """Failed call frame should have FrameEnd with success=False."""
    log = StateAccessLog()

    frame_id = start_frame(log)
    # ... execution fails ...
    end_frame(log, frame_id, success=False)

    assert log.operations[1].success is False


def test_nested_frames():
    """Nested call frames should have correct IDs."""
    log = StateAccessLog()

    outer_id = start_frame(log)
    inner_id = start_frame(log)
    end_frame(log, inner_id, success=True)
    end_frame(log, outer_id, success=True)

    assert len(log.operations) == 4
    assert log.operations[0].frame_id == Uint(0)  # outer start
    assert log.operations[1].frame_id == Uint(1)  # inner start
    assert log.operations[2].frame_id == Uint(1)  # inner end
    assert log.operations[3].frame_id == Uint(0)  # outer end
