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
