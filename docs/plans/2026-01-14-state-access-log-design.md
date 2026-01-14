# State Access Log Design

## Overview

A unified state access collector that captures all state operations during EVM execution. This log serves as the foundation for deriving both:

- **ExecutionWitness**: Preimages for stateless verification (matches Geth/Reth `debug_executionWitness`)
- **Block Access Lists (BAL)**: EIP-7928 block-level access lists

## Architecture

```
┌─────────────────────────────┐
│   StateAccessLog (core)     │  ← Flat operation log in EVM
└─────────────┬───────────────┘
              │
       ┌──────┴──────┐
       ▼             ▼
┌──────────────┐ ┌──────────────┐
│ Execution    │ │ Block Access │
│ Witness      │ │ List Builder │
│ Builder      │ │              │
└──────────────┘ └──────────────┘
```

## Design Decisions

1. **Flat log with frame markers** - Operations are logged sequentially with `FrameStart`/`FrameEnd` markers to track call frame boundaries and success/failure status.

2. **Captures values, not just keys** - Each operation includes the actual data (balance, storage value, code) to support both witness preimage collection and BAL diff computation.

3. **Deduplicated storage** - Large data (code bytes, block headers) stored separately and referenced by hash to avoid duplication.

4. **Optional tracking** - `access_log` field is `Optional`, so forks that don't need tracking have zero overhead.

5. **Replaces PR 1719's StateChanges** - This design supersedes the hierarchical `StateChanges` tracker, providing a more general foundation.

## Data Structures

### Core Types

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from ethereum_types.bytes import Bytes, Bytes32
from ethereum_types.numeric import U256, Uint

from ethereum.crypto.hash import Hash32
from .fork_types import Address


# Frame markers
@dataclass
class FrameStart:
    frame_id: Uint

@dataclass
class FrameEnd:
    frame_id: Uint
    success: bool


# State access operations
@dataclass
class AccountRead:
    address: Address
    balance: U256
    nonce: Uint
    code_hash: Hash32

@dataclass
class AccountWrite:
    address: Address
    field: str  # "balance" | "nonce" | "code"
    new_value: Union[U256, Uint, Hash32]

@dataclass
class StorageRead:
    address: Address
    slot: Bytes32
    value: U256

@dataclass
class StorageWrite:
    address: Address
    slot: Bytes32
    old_value: U256
    new_value: U256

@dataclass
class CodeRead:
    address: Address
    code_hash: Hash32

@dataclass
class BlockHashRead:
    block_number: Uint


Operation = Union[
    FrameStart, FrameEnd,
    AccountRead, AccountWrite,
    StorageRead, StorageWrite,
    CodeRead, BlockHashRead,
]


@dataclass
class StateAccessLog:
    """Flat log of state operations with deduplicated storage."""

    operations: List[Operation] = field(default_factory=list)

    # Deduplicated storage
    codes: Dict[Hash32, Bytes] = field(default_factory=dict)
    headers: Dict[Uint, Hash32] = field(default_factory=dict)

    # Frame ID counter
    _next_frame_id: Uint = field(default=Uint(0))
```

### Helper Functions

```python
from ethereum.crypto.hash import keccak256


def start_frame(log: StateAccessLog) -> Uint:
    """Start a new frame, return its ID."""
    frame_id = log._next_frame_id
    log._next_frame_id = Uint(frame_id + 1)
    log.operations.append(FrameStart(frame_id))
    return frame_id


def end_frame(log: StateAccessLog, frame_id: Uint, success: bool) -> None:
    """End a frame with success/failure status."""
    log.operations.append(FrameEnd(frame_id, success))


def log_account_read(
    log: StateAccessLog,
    address: Address,
    balance: U256,
    nonce: Uint,
    code: Bytes,
) -> None:
    """Log an account read, deduplicating code."""
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
    """Log an account field write."""
    if field == "code":
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
    """Log a storage read."""
    log.operations.append(StorageRead(address, slot, value))


def log_storage_write(
    log: StateAccessLog,
    address: Address,
    slot: Bytes32,
    old_value: U256,
    new_value: U256,
) -> None:
    """Log a storage write."""
    log.operations.append(StorageWrite(address, slot, old_value, new_value))


def log_code_read(
    log: StateAccessLog,
    address: Address,
    code: Bytes,
) -> None:
    """Log a code read (e.g., EXTCODECOPY), deduplicating."""
    code_hash = keccak256(code)
    if code_hash not in log.codes:
        log.codes[code_hash] = code
    log.operations.append(CodeRead(address, code_hash))


def log_blockhash_read(
    log: StateAccessLog,
    block_number: Uint,
    block_hash: Hash32,
) -> None:
    """Log a BLOCKHASH read."""
    if block_number not in log.headers:
        log.headers[block_number] = block_hash
    log.operations.append(BlockHashRead(block_number))
```

## EVM Integration

### Modified Structures

```python
# In vm/__init__.py

@dataclass
class BlockEnvironment:
    # ... existing fields ...
    access_log: Optional[StateAccessLog] = None  # None if not tracking


@dataclass
class Evm:
    # ... existing fields ...
    access_log: Optional[StateAccessLog] = None  # Reference to block's log
    frame_id: Optional[Uint] = None              # Current frame ID
```

### Opcode Integration

**SLOAD (storage.py):**
```python
def sload(evm: Evm) -> None:
    # STACK
    key = pop(evm.stack).to_be_bytes32()

    # GAS
    if (evm.message.current_target, key) in evm.accessed_storage_keys:
        charge_gas(evm, GAS_WARM_ACCESS)
    else:
        evm.accessed_storage_keys.add((evm.message.current_target, key))
        charge_gas(evm, GAS_COLD_SLOAD)

    # OPERATION
    value = get_storage(
        evm.message.block_env.state, evm.message.current_target, key
    )

    # ACCESS LOG
    if evm.access_log is not None:
        log_storage_read(evm.access_log, evm.message.current_target, key, value)

    push(evm.stack, value)
    evm.pc += Uint(1)
```

**SSTORE (storage.py):**
```python
def sstore(evm: Evm) -> None:
    # ... stack, gas checks ...

    state = evm.message.block_env.state
    current_value = get_storage(state, evm.message.current_target, key)

    # ACCESS LOG
    if evm.access_log is not None:
        log_storage_write(
            evm.access_log,
            evm.message.current_target,
            key,
            current_value,
            new_value,
        )

    set_storage(state, evm.message.current_target, key, new_value)
    evm.pc += Uint(1)
```

**BALANCE (environment.py):**
```python
def balance(evm: Evm) -> None:
    # STACK
    address = Address(pop(evm.stack).to_be_bytes20())

    # GAS
    # ... warm/cold logic ...

    # OPERATION
    account = get_account(evm.message.block_env.state, address)

    # ACCESS LOG
    if evm.access_log is not None:
        log_account_read(
            evm.access_log,
            address,
            account.balance,
            account.nonce,
            account.code,
        )

    push(evm.stack, account.balance)
    evm.pc += Uint(1)
```

**BLOCKHASH (block.py):**
```python
def blockhash(evm: Evm) -> None:
    # STACK
    block_number = pop(evm.stack)

    # OPERATION
    # ... bounds check ...
    hash = evm.message.block_env.block_hashes[index]

    # ACCESS LOG
    if evm.access_log is not None:
        log_blockhash_read(evm.access_log, Uint(block_number), hash)

    push(evm.stack, U256.from_be_bytes(hash))
    evm.pc += Uint(1)
```

### Frame Handling in Interpreter

```python
# In interpreter.py

def process_call(evm: Evm, message: Message) -> Evm:
    # Start frame
    frame_id = None
    if evm.access_log is not None:
        frame_id = start_frame(evm.access_log)

    child_evm = execute_code(message, evm.access_log, frame_id)

    # End frame
    if evm.access_log is not None:
        end_frame(evm.access_log, frame_id, child_evm.error is None)

    # ... incorporate child state ...
    return child_evm
```

## Builders

### ExecutionWitness Builder

```python
from dataclasses import dataclass
from typing import List, Set

from ethereum_types.bytes import Bytes

from ethereum.crypto.hash import Hash32
from .state_access_log import (
    StateAccessLog,
    AccountRead,
    CodeRead,
    StorageRead,
    BlockHashRead,
)


@dataclass
class ExecutionWitness:
    """Matches Geth/Reth debug_executionWitness format."""
    state: List[Bytes]    # Trie node preimages
    codes: List[Bytes]    # Contract code preimages
    keys: List[Bytes]     # Hashed key preimages (addresses, storage slots)
    headers: List[Bytes]  # Block headers for BLOCKHASH


def build_execution_witness(
    log: StateAccessLog,
    # TODO: trie access for state preimages
) -> ExecutionWitness:
    """
    Build ExecutionWitness from access log.

    Note: All operations are included, even from reverted frames,
    because stateless verification needs all accessed preimages.
    """
    seen_codes: Set[Hash32] = set()
    seen_addresses: Set[Address] = set()
    seen_slots: Set[tuple[Address, Bytes32]] = set()

    codes: List[Bytes] = []
    keys: List[Bytes] = []
    headers: List[Bytes] = []

    for op in log.operations:
        # Collect code preimages
        if isinstance(op, (AccountRead, CodeRead)):
            if op.code_hash not in seen_codes:
                seen_codes.add(op.code_hash)
                codes.append(log.codes[op.code_hash])

        # Collect address preimages
        if isinstance(op, (AccountRead, StorageRead)):
            if op.address not in seen_addresses:
                seen_addresses.add(op.address)
                keys.append(bytes(op.address))

        # Collect storage slot preimages
        if isinstance(op, StorageRead):
            slot_key = (op.address, op.slot)
            if slot_key not in seen_slots:
                seen_slots.add(slot_key)
                keys.append(bytes(op.slot))

        # Collect block headers
        if isinstance(op, BlockHashRead):
            headers.append(encode_block_header(op.block_number, log))

    return ExecutionWitness(
        state=[],  # TODO: trie node preimages require trie integration
        codes=codes,
        keys=keys,
        headers=headers,
    )
```

### BAL Builder

```python
from typing import Dict, List, Set, Tuple

from .block_access_lists.rlp_types import (
    AccountChanges,
    BalanceChange,
    BlockAccessIndex,
    BlockAccessList,
    CodeChange,
    NonceChange,
    SlotChanges,
    StorageChange,
)
from .state_access_log import (
    StateAccessLog,
    FrameStart,
    FrameEnd,
    AccountWrite,
    StorageRead,
    StorageWrite,
)


def build_block_access_list(
    log: StateAccessLog,
    tx_boundaries: List[Uint],  # Frame IDs that are transaction roots
) -> BlockAccessList:
    """
    Build BAL from access log.

    - All reads are included (even from reverted frames)
    - Writes from reverted frames are excluded
    """
    # Track which frames succeeded
    frame_success = _compute_frame_success(log)

    # Collect data, filtering reverted writes
    storage_reads: Set[Tuple[Address, Bytes32]] = set()
    storage_writes: Dict[Tuple[Address, Bytes32, BlockAccessIndex], U256] = {}
    balance_changes: Dict[Tuple[Address, BlockAccessIndex], U256] = {}
    nonce_changes: Dict[Tuple[Address, BlockAccessIndex], Uint] = {}
    code_changes: Dict[Tuple[Address, BlockAccessIndex], Hash32] = {}

    current_frame_stack: List[Uint] = []
    block_access_index = BlockAccessIndex(0)

    for op in log.operations:
        if isinstance(op, FrameStart):
            current_frame_stack.append(op.frame_id)
            # Increment BAL index at tx boundaries
            if op.frame_id in tx_boundaries:
                block_access_index = BlockAccessIndex(block_access_index + 1)

        elif isinstance(op, FrameEnd):
            current_frame_stack.pop()

        elif isinstance(op, StorageRead):
            # Reads always included
            storage_reads.add((op.address, op.slot))

        elif isinstance(op, StorageWrite):
            # Writes only if all ancestor frames succeeded
            if _all_frames_succeeded(current_frame_stack, frame_success):
                key = (op.address, op.slot, block_access_index)
                storage_writes[key] = op.new_value

        elif isinstance(op, AccountWrite):
            if _all_frames_succeeded(current_frame_stack, frame_success):
                if op.field == "balance":
                    balance_changes[(op.address, block_access_index)] = op.new_value
                elif op.field == "nonce":
                    nonce_changes[(op.address, block_access_index)] = op.new_value
                elif op.field == "code":
                    code_changes[(op.address, block_access_index)] = op.new_value

    return _assemble_block_access_list(
        storage_reads, storage_writes,
        balance_changes, nonce_changes, code_changes,
        log,
    )


def _compute_frame_success(log: StateAccessLog) -> Dict[Uint, bool]:
    """Extract frame success status from log."""
    result = {}
    for op in log.operations:
        if isinstance(op, FrameEnd):
            result[op.frame_id] = op.success
    return result


def _all_frames_succeeded(
    frame_stack: List[Uint],
    frame_success: Dict[Uint, bool],
) -> bool:
    """Check if all frames in stack succeeded."""
    return all(frame_success.get(fid, True) for fid in frame_stack)
```

## Open Questions

1. **Trie node preimages** - The `state` field in `ExecutionWitness` requires capturing trie node preimages during trie traversal. This needs trie-level integration not covered here.

2. **Relationship to PR 1719** - This design replaces `StateChanges`. The BAL builder would need to be updated to consume `StateAccessLog` instead.

3. **Pre-state values for BAL** - BAL needs pre-state values for net-zero filtering. Should `AccountRead`/`StorageRead` be logged before writes to capture pre-state, or should we add explicit pre-state capture?

4. **Performance** - Flat log may grow large for blocks with many operations. Consider chunking or streaming if this becomes an issue.

## Files to Create/Modify

### New Files
- `src/ethereum/forks/amsterdam/state_access_log.py` - Core types and helpers

### Modified Files
- `src/ethereum/forks/amsterdam/vm/__init__.py` - Add `access_log` to `BlockEnvironment` and `Evm`
- `src/ethereum/forks/amsterdam/vm/interpreter.py` - Frame start/end handling
- `src/ethereum/forks/amsterdam/vm/instructions/storage.py` - SLOAD/SSTORE logging
- `src/ethereum/forks/amsterdam/vm/instructions/environment.py` - BALANCE/EXTCODE* logging
- `src/ethereum/forks/amsterdam/vm/instructions/block.py` - BLOCKHASH logging
- `src/ethereum/forks/amsterdam/vm/instructions/system.py` - CALL/CREATE frame handling

### Test Framework (optional, later)
- `src/ethereum_test_*/` - ExecutionWitness and BAL builders for test generation
