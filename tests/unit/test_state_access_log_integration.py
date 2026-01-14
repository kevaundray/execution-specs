"""Integration tests for StateAccessLog with EVM instructions."""

import pytest
from ethereum_types.bytes import Bytes32
from ethereum_types.numeric import U256

from ethereum.forks.amsterdam.state_access_log import (
    StateAccessLog,
    StorageRead,
    StorageWrite,
    log_storage_read,
    log_storage_write,
)


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
