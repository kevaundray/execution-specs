# State Access Log Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement a unified state access collector that captures all state operations during EVM execution, enabling derivation of ExecutionWitness and Block Access Lists.

**Architecture:** A flat operation log with frame markers stored in `StateAccessLog`. Helper functions log operations from opcodes. The log is optional (None when not tracking). Builders consume the log to produce ExecutionWitness or BAL output.

**Tech Stack:** Python 3.11+, dataclasses, ethereum-types, ethereum-rlp

---

## Task 1: Create Core Data Structures

**Files:**
- Create: `src/ethereum/forks/amsterdam/state_access_log.py`
- Test: `tests/unit/test_state_access_log.py`

**Step 1: Create test file with basic operation tests**

Create `tests/unit/test_state_access_log.py`:

```python
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_state_access_log.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ethereum.forks.amsterdam.state_access_log'`

**Step 3: Create the state_access_log module with data structures**

Create `src/ethereum/forks/amsterdam/state_access_log.py`:

```python
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
from ethereum_types.numeric import U256, Uint

from ethereum.crypto.hash import Hash32

from .fork_types import Address


# Frame markers
@dataclass
class FrameStart:
    """Marks the start of a call frame."""

    frame_id: Uint


@dataclass
class FrameEnd:
    """Marks the end of a call frame with success/failure status."""

    frame_id: Uint
    success: bool


# State access operations
@dataclass
class AccountRead:
    """Records reading account state (balance, nonce, code)."""

    address: Address
    balance: U256
    nonce: Uint
    code_hash: Hash32


@dataclass
class AccountWrite:
    """Records writing to an account field."""

    address: Address
    field: str  # "balance" | "nonce" | "code"
    new_value: Union[U256, Uint, Hash32]


@dataclass
class StorageRead:
    """Records reading a storage slot."""

    address: Address
    slot: Bytes32
    value: U256


@dataclass
class StorageWrite:
    """Records writing to a storage slot."""

    address: Address
    slot: Bytes32
    old_value: U256
    new_value: U256


@dataclass
class CodeRead:
    """Records reading contract code (EXTCODECOPY, etc.)."""

    address: Address
    code_hash: Hash32


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
        Block headers accessed via BLOCKHASH, keyed by block number.
    _next_frame_id : Uint
        Counter for generating unique frame IDs.
    """

    operations: List[Operation] = field(default_factory=list)
    codes: Dict[Hash32, Bytes] = field(default_factory=dict)
    headers: Dict[Uint, Hash32] = field(default_factory=dict)
    _next_frame_id: Uint = field(default=Uint(0))
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_state_access_log.py -v`
Expected: PASS (all 7 tests)

**Step 5: Commit**

```bash
git add src/ethereum/forks/amsterdam/state_access_log.py tests/unit/test_state_access_log.py
git commit -m "feat(amsterdam): add StateAccessLog core data structures

Introduces data structures for unified state access tracking:
- FrameStart/FrameEnd markers for call hierarchy
- AccountRead/AccountWrite for account state
- StorageRead/StorageWrite for storage slots
- CodeRead/BlockHashRead for code and block hashes
- StateAccessLog container with deduplication"
```

---

## Task 2: Add Helper Functions for Logging

**Files:**
- Modify: `src/ethereum/forks/amsterdam/state_access_log.py`
- Modify: `tests/unit/test_state_access_log.py`

**Step 1: Add tests for helper functions**

Append to `tests/unit/test_state_access_log.py`:

```python
from ethereum.forks.amsterdam.state_access_log import (
    end_frame,
    log_account_read,
    log_account_write,
    log_blockhash_read,
    log_code_read,
    log_storage_read,
    log_storage_write,
    start_frame,
)


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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_state_access_log.py::test_start_frame_increments_id -v`
Expected: FAIL with `ImportError: cannot import name 'start_frame'`

**Step 3: Add helper functions to state_access_log.py**

Append to `src/ethereum/forks/amsterdam/state_access_log.py`:

```python
from ethereum.crypto.hash import keccak256


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
    log._next_frame_id = Uint(frame_id + 1)
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
    if block_number not in log.headers:
        log.headers[block_number] = block_hash
    log.operations.append(BlockHashRead(block_number))
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_state_access_log.py -v`
Expected: PASS (all 19 tests)

**Step 5: Commit**

```bash
git add src/ethereum/forks/amsterdam/state_access_log.py tests/unit/test_state_access_log.py
git commit -m "feat(amsterdam): add StateAccessLog helper functions

Add functions for logging state operations:
- start_frame/end_frame for call frame tracking
- log_account_read/log_account_write for account state
- log_storage_read/log_storage_write for storage
- log_code_read/log_blockhash_read for code and headers

All functions handle deduplication of large data (code, headers)."
```

---

## Task 3: Add StateAccessLog to EVM Structures

**Files:**
- Modify: `src/ethereum/forks/amsterdam/vm/__init__.py`
- Test: `tests/unit/test_state_access_log.py`

**Step 1: Add test for EVM integration**

Append to `tests/unit/test_state_access_log.py`:

```python
def test_state_access_log_optional_in_block_env():
    """BlockEnvironment can have optional access_log."""
    # This test verifies the import works after modification
    from ethereum.forks.amsterdam.vm import BlockEnvironment

    # access_log should be an optional field
    assert hasattr(BlockEnvironment, "__dataclass_fields__")
    fields = BlockEnvironment.__dataclass_fields__
    assert "access_log" in fields
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_state_access_log.py::test_state_access_log_optional_in_block_env -v`
Expected: FAIL with `AssertionError` (access_log field doesn't exist)

**Step 3: Modify vm/__init__.py to add access_log**

In `src/ethereum/forks/amsterdam/vm/__init__.py`, add the import and field:

After the existing imports, add:
```python
from ..state_access_log import StateAccessLog
```

In the `BlockEnvironment` dataclass, add field at the end:
```python
    access_log: Optional[StateAccessLog] = None
```

In the `Evm` dataclass, add fields at the end:
```python
    access_log: Optional[StateAccessLog] = None
    frame_id: Optional[Uint] = None
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_state_access_log.py::test_state_access_log_optional_in_block_env -v`
Expected: PASS

**Step 5: Verify pyspec still imports**

Run: `uv run python -c "from ethereum.forks.amsterdam import fork; print('OK')"`
Expected: `OK`

**Step 6: Commit**

```bash
git add src/ethereum/forks/amsterdam/vm/__init__.py tests/unit/test_state_access_log.py
git commit -m "feat(amsterdam): add access_log to BlockEnvironment and Evm

Add optional StateAccessLog field to:
- BlockEnvironment.access_log (block-level log)
- Evm.access_log (reference to block's log)
- Evm.frame_id (current call frame ID)

Fields are Optional with default None for zero overhead
when not tracking."
```

---

## Task 4: Hook Storage Instructions (SLOAD/SSTORE)

**Files:**
- Modify: `src/ethereum/forks/amsterdam/vm/instructions/storage.py`
- Test: `tests/unit/test_state_access_log_integration.py` (new)

**Step 1: Create integration test file**

Create `tests/unit/test_state_access_log_integration.py`:

```python
"""Integration tests for StateAccessLog with EVM instructions."""

import pytest
from ethereum_types.bytes import Bytes32
from ethereum_types.numeric import U256, Uint

from ethereum.forks.amsterdam.state_access_log import (
    StateAccessLog,
    StorageRead,
    StorageWrite,
)


def test_sload_logs_storage_read():
    """SLOAD should log a StorageRead operation when access_log is set."""
    # This is a placeholder - actual integration requires EVM setup
    # For now, verify the logging function works correctly
    log = StateAccessLog()
    address = bytes.fromhex("1234567890123456789012345678901234567890")
    slot = Bytes32(b"\x00" * 31 + b"\x01")

    from ethereum.forks.amsterdam.state_access_log import log_storage_read

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

    from ethereum.forks.amsterdam.state_access_log import log_storage_write

    log_storage_write(log, address, slot, U256(10), U256(20))

    assert len(log.operations) == 1
    op = log.operations[0]
    assert isinstance(op, StorageWrite)
    assert op.old_value == U256(10)
    assert op.new_value == U256(20)
```

**Step 2: Run test to verify setup works**

Run: `uv run pytest tests/unit/test_state_access_log_integration.py -v`
Expected: PASS (tests use helper functions directly)

**Step 3: Modify storage.py to add logging**

In `src/ethereum/forks/amsterdam/vm/instructions/storage.py`:

Add import at top:
```python
from ...state_access_log import log_storage_read, log_storage_write
```

In `sload` function, after `value = get_storage(...)`, add:
```python
    # ACCESS LOG
    if evm.access_log is not None:
        log_storage_read(
            evm.access_log,
            evm.message.current_target,
            key,
            value,
        )
```

In `sstore` function, after getting `current_value` and before `set_storage(...)`, add:
```python
    # ACCESS LOG
    if evm.access_log is not None:
        log_storage_write(
            evm.access_log,
            evm.message.current_target,
            key,
            current_value,
            new_value,
        )
```

**Step 4: Verify pyspec still imports**

Run: `uv run python -c "from ethereum.forks.amsterdam.vm.instructions import storage; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add src/ethereum/forks/amsterdam/vm/instructions/storage.py tests/unit/test_state_access_log_integration.py
git commit -m "feat(amsterdam): hook SLOAD/SSTORE to StateAccessLog

Add access logging to storage instructions:
- SLOAD logs StorageRead with slot and value
- SSTORE logs StorageWrite with old and new values

Logging only occurs when evm.access_log is not None."
```

---

## Task 5: Hook Environment Instructions (BALANCE, EXTCODE*)

**Files:**
- Modify: `src/ethereum/forks/amsterdam/vm/instructions/environment.py`
- Modify: `tests/unit/test_state_access_log_integration.py`

**Step 1: Add tests for environment instruction logging**

Append to `tests/unit/test_state_access_log_integration.py`:

```python
from ethereum.forks.amsterdam.state_access_log import (
    AccountRead,
    CodeRead,
    log_account_read,
    log_code_read,
)
from ethereum.crypto.hash import keccak256


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
```

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_state_access_log_integration.py -v`
Expected: PASS

**Step 3: Modify environment.py to add logging**

In `src/ethereum/forks/amsterdam/vm/instructions/environment.py`:

Add import at top:
```python
from ...state_access_log import log_account_read, log_code_read
```

In `balance` function, after getting the account, add:
```python
    # ACCESS LOG
    if evm.access_log is not None:
        log_account_read(
            evm.access_log,
            address,
            account.balance,
            account.nonce,
            account.code,
        )
```

In `extcodesize` function, after getting the account, add:
```python
    # ACCESS LOG
    if evm.access_log is not None:
        log_code_read(evm.access_log, address, account.code)
```

In `extcodecopy` function, after getting the account, add:
```python
    # ACCESS LOG
    if evm.access_log is not None:
        log_code_read(evm.access_log, address, code)
```

In `extcodehash` function, after getting the account, add:
```python
    # ACCESS LOG
    if evm.access_log is not None:
        log_code_read(evm.access_log, address, account.code)
```

**Step 4: Verify pyspec still imports**

Run: `uv run python -c "from ethereum.forks.amsterdam.vm.instructions import environment; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add src/ethereum/forks/amsterdam/vm/instructions/environment.py tests/unit/test_state_access_log_integration.py
git commit -m "feat(amsterdam): hook BALANCE/EXTCODE* to StateAccessLog

Add access logging to environment instructions:
- BALANCE logs AccountRead with full account state
- EXTCODESIZE logs CodeRead
- EXTCODECOPY logs CodeRead
- EXTCODEHASH logs CodeRead

Code is deduplicated in the log's codes dictionary."
```

---

## Task 6: Hook Block Instructions (BLOCKHASH)

**Files:**
- Modify: `src/ethereum/forks/amsterdam/vm/instructions/block.py`
- Modify: `tests/unit/test_state_access_log_integration.py`

**Step 1: Add test for BLOCKHASH logging**

Append to `tests/unit/test_state_access_log_integration.py`:

```python
from ethereum.forks.amsterdam.state_access_log import (
    BlockHashRead,
    log_blockhash_read,
)


def test_blockhash_logs_header_read():
    """BLOCKHASH should log BlockHashRead when access_log is set."""
    log = StateAccessLog()
    block_hash = bytes.fromhex("ab" * 32)

    log_blockhash_read(log, Uint(12345), block_hash)

    op = log.operations[0]
    assert isinstance(op, BlockHashRead)
    assert op.block_number == Uint(12345)
    assert log.headers[Uint(12345)] == block_hash
```

**Step 2: Run test**

Run: `uv run pytest tests/unit/test_state_access_log_integration.py::test_blockhash_logs_header_read -v`
Expected: PASS

**Step 3: Modify block.py to add logging**

In `src/ethereum/forks/amsterdam/vm/instructions/block.py`:

Add import at top:
```python
from ...state_access_log import log_blockhash_read
```

In `block_hash` function (the BLOCKHASH opcode), after getting the hash value and before push, add:
```python
    # ACCESS LOG
    if evm.access_log is not None and hash != 0:
        log_blockhash_read(
            evm.access_log,
            block_number,
            evm.message.block_env.block_hashes[index],
        )
```

**Step 4: Verify pyspec still imports**

Run: `uv run python -c "from ethereum.forks.amsterdam.vm.instructions import block; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add src/ethereum/forks/amsterdam/vm/instructions/block.py tests/unit/test_state_access_log_integration.py
git commit -m "feat(amsterdam): hook BLOCKHASH to StateAccessLog

Add access logging to BLOCKHASH instruction.
Only logs when a valid hash is returned (not out of range).
Block hashes are stored in the log's headers dictionary."
```

---

## Task 7: Add Frame Handling to Interpreter

**Files:**
- Modify: `src/ethereum/forks/amsterdam/vm/interpreter.py`
- Modify: `tests/unit/test_state_access_log_integration.py`

**Step 1: Add test for frame handling**

Append to `tests/unit/test_state_access_log_integration.py`:

```python
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
```

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_state_access_log_integration.py -v -k frame`
Expected: PASS

**Step 3: Modify interpreter.py to add frame handling**

In `src/ethereum/forks/amsterdam/vm/interpreter.py`:

Add import at top:
```python
from ..state_access_log import start_frame, end_frame
```

In the `process_message_call` function (or equivalent that creates child EVMs), wrap the child execution:

Before creating child EVM:
```python
    # ACCESS LOG - start frame
    child_frame_id = None
    if message.block_env.access_log is not None:
        child_frame_id = start_frame(message.block_env.access_log)
```

After child execution completes (success or failure):
```python
    # ACCESS LOG - end frame
    if message.block_env.access_log is not None and child_frame_id is not None:
        end_frame(
            message.block_env.access_log,
            child_frame_id,
            child_evm.error is None,
        )
```

**Step 4: Verify pyspec still imports**

Run: `uv run python -c "from ethereum.forks.amsterdam.vm import interpreter; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add src/ethereum/forks/amsterdam/vm/interpreter.py tests/unit/test_state_access_log_integration.py
git commit -m "feat(amsterdam): add frame handling to interpreter

Wrap message calls with FrameStart/FrameEnd markers:
- start_frame called before child EVM execution
- end_frame called after with success status based on error

This enables builders to filter reverted writes."
```

---

## Task 8: Create ExecutionWitness Builder

**Files:**
- Create: `src/ethereum/forks/amsterdam/execution_witness.py`
- Test: `tests/unit/test_execution_witness.py`

**Step 1: Create test file**

Create `tests/unit/test_execution_witness.py`:

```python
"""Tests for ExecutionWitness builder."""

import pytest
from ethereum_types.bytes import Bytes32
from ethereum_types.numeric import U256, Uint

from ethereum.crypto.hash import keccak256
from ethereum.forks.amsterdam.state_access_log import (
    StateAccessLog,
    log_account_read,
    log_code_read,
    log_storage_read,
    log_blockhash_read,
    start_frame,
    end_frame,
)
from ethereum.forks.amsterdam.execution_witness import (
    ExecutionWitness,
    build_execution_witness,
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_execution_witness.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Create execution_witness.py**

Create `src/ethereum/forks/amsterdam/execution_witness.py`:

```python
"""
ExecutionWitness Builder.

Builds an ExecutionWitness from a StateAccessLog. The witness contains
preimages needed for stateless verification of block execution.

Matches Geth/Reth debug_executionWitness format.
"""

from dataclasses import dataclass, field
from typing import List, Set

from ethereum_types.bytes import Bytes
from ethereum_types.numeric import Uint

from ethereum.crypto.hash import Hash32

from .fork_types import Address
from .state_access_log import (
    AccountRead,
    BlockHashRead,
    CodeRead,
    Operation,
    StateAccessLog,
    StorageRead,
    StorageWrite,
)


@dataclass
class ExecutionWitness:
    """
    Witness data for stateless block verification.

    Matches Geth/Reth debug_executionWitness format.

    Attributes
    ----------
    state : List[Bytes]
        Trie node preimages (not yet implemented).
    codes : List[Bytes]
        Contract code preimages.
    keys : List[Bytes]
        Address and storage slot preimages.
    headers : List[Bytes]
        Block headers for BLOCKHASH verification.
    """

    state: List[Bytes] = field(default_factory=list)
    codes: List[Bytes] = field(default_factory=list)
    keys: List[Bytes] = field(default_factory=list)
    headers: List[Bytes] = field(default_factory=list)


def build_execution_witness(log: StateAccessLog) -> ExecutionWitness:
    """
    Build ExecutionWitness from StateAccessLog.

    All operations are included, even from reverted frames, because
    stateless verification needs all accessed preimages to prove
    the execution path.

    Parameters
    ----------
    log :
        The state access log from block execution.

    Returns
    -------
    witness : ExecutionWitness
        The witness containing all preimages.
    """
    seen_codes: Set[Hash32] = set()
    seen_addresses: Set[Address] = set()
    seen_slots: Set[tuple] = set()
    seen_blocks: Set[Uint] = set()

    codes: List[Bytes] = []
    keys: List[Bytes] = []
    headers: List[Bytes] = []

    for op in log.operations:
        # Collect code preimages
        if isinstance(op, (AccountRead, CodeRead)):
            if op.code_hash not in seen_codes:
                seen_codes.add(op.code_hash)
                if op.code_hash in log.codes:
                    codes.append(log.codes[op.code_hash])

        # Collect address preimages
        if isinstance(op, (AccountRead, StorageRead, StorageWrite)):
            if op.address not in seen_addresses:
                seen_addresses.add(op.address)
                keys.append(bytes(op.address))

        # Collect storage slot preimages
        if isinstance(op, (StorageRead, StorageWrite)):
            slot_key = (op.address, op.slot)
            if slot_key not in seen_slots:
                seen_slots.add(slot_key)
                keys.append(bytes(op.slot))

        # Collect block headers
        if isinstance(op, BlockHashRead):
            if op.block_number not in seen_blocks:
                seen_blocks.add(op.block_number)
                if op.block_number in log.headers:
                    # Store hash for now; full header encoding TODO
                    headers.append(bytes(log.headers[op.block_number]))

    return ExecutionWitness(
        state=[],  # TODO: trie node preimages
        codes=codes,
        keys=keys,
        headers=headers,
    )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_execution_witness.py -v`
Expected: PASS (all 6 tests)

**Step 5: Commit**

```bash
git add src/ethereum/forks/amsterdam/execution_witness.py tests/unit/test_execution_witness.py
git commit -m "feat(amsterdam): add ExecutionWitness builder

Implements ExecutionWitness that matches Geth/Reth format:
- codes: contract code preimages
- keys: address and storage slot preimages
- headers: block headers for BLOCKHASH

All operations included (even reverted) for stateless verification.
Trie node preimages (state field) not yet implemented."
```

---

## Summary

After completing all 8 tasks, you will have:

1. **Core data structures** - `StateAccessLog` with all operation types
2. **Helper functions** - For logging each type of operation
3. **EVM integration** - `access_log` field in `BlockEnvironment` and `Evm`
4. **Opcode hooks** - SLOAD, SSTORE, BALANCE, EXTCODE*, BLOCKHASH
5. **Frame handling** - Interpreter wraps calls with FrameStart/FrameEnd
6. **ExecutionWitness builder** - Derives witness from log

**Not yet implemented (future tasks):**
- BAL builder (derives from same log, filters reverted writes)
- Trie node preimage collection (requires trie integration)
- Integration with t8n tool for test generation
