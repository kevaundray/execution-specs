"""Tests for ExecutionWitness builder."""

from ethereum_types.bytes import Bytes32
from ethereum_types.numeric import U256, Uint

from ethereum.forks.amsterdam.execution_witness import build_execution_witness
from ethereum.forks.amsterdam.state_access_log import (
    StateAccessLog,
    end_frame,
    log_blockhash_read,
    log_code_read,
    log_storage_read,
    start_frame,
)


def test_execution_witness_empty_log():
    """Empty log produces empty witness."""
    log = StateAccessLog()
    witness = build_execution_witness(log)

    assert witness.codes == []
    assert witness.keys == []
    assert witness.headers == []


def test_execution_witness_collects_codes():
    """Witness collects code preimages."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    code = b"contract bytecode"

    log_code_read(log, address, code)
    witness = build_execution_witness(log)

    assert code in witness.codes


def test_execution_witness_deduplicates_codes():
    """Same code accessed twice appears once in witness."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    code = b"contract bytecode"

    log_code_read(log, address, code)
    log_code_read(log, address, code)
    witness = build_execution_witness(log)

    assert len(witness.codes) == 1


def test_execution_witness_collects_keys():
    """Witness collects address and slot preimages."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    slot = Bytes32(b"\x00" * 31 + b"\x01")

    log_storage_read(log, address, slot, U256(42))
    witness = build_execution_witness(log)

    assert bytes(address) in witness.keys
    assert bytes(slot) in witness.keys


def test_execution_witness_includes_reverted_reads():
    """Reads from reverted frames are included (needed for verification)."""
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    code = b"reverted code"

    frame_id = start_frame(log)
    log_code_read(log, address, code)
    end_frame(log, frame_id, success=False)  # Frame reverted

    witness = build_execution_witness(log)
    assert code in witness.codes  # Still included


def test_execution_witness_collects_headers():
    """Witness collects block headers for BLOCKHASH."""
    log = StateAccessLog()
    block_hash = bytes.fromhex("ab" * 32)

    log_blockhash_read(log, Uint(1000), block_hash)
    witness = build_execution_witness(log)

    assert len(witness.headers) == 1
