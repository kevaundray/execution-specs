"""
Stateless validation interfaces.
"""

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from ethereum_rlp import rlp
from ethereum_types.bytes import Bytes
from ethereum_types.frozen import slotted_freezable
from ethereum_types.numeric import U64

from ethereum.crypto.hash import Hash32, keccak256
from ethereum.exceptions import InvalidBlock
from ethereum.state import Root

from .blocks import Block, Header
from .execution_engine.new_payload import verify_and_notify_new_payload
from .execution_engine.types import NewPayloadRequest
from .execution_engine.validation_helpers import _payload_block
from .fork import ChainContext
from .fork_types import VersionedHash
from .stateless_types import ExecutionWitness
from .witness_state import WitnessState, build_code_db, build_node_db

# Amsterdam currently carries execution requests as raw bytes in order.
ExecutionRequests = Tuple[Bytes, ...]


@slotted_freezable
@dataclass
class ExecutionPayloadHeader:
    """
    Execution payload header for stateless input scaffolding.

    TODO: Replace with the fork-specific execution payload header container.
    """


@slotted_freezable
@dataclass
class NewPayloadRequestHeader:
    """
    Header-only form of ``NewPayloadRequest`` for stateless flows.

    We expect ``hash_tree_root(execution_payload_header)`` equals
    ``hash_tree_root(execution_payload)``.
    """

    execution_payload_header: ExecutionPayloadHeader
    versioned_hashes: Sequence[VersionedHash]
    parent_beacon_block_root: Root
    execution_requests: ExecutionRequests


@slotted_freezable
@dataclass
class ChainConfig:
    """
    Chain configuration needed for stateless validation.

    TODO: Since we do not want the client to hold all possible chains,
    we may want to add more to the chain config, like a genesis file.
    """

    chain_id: U64


@slotted_freezable
@dataclass
class StatelessInput:
    """
    Input to stateless validation.
    """

    new_payload_request: NewPayloadRequest
    """
    Consensus-layer payload request to validate statelessly. See
    ``execution_engine.NewPayloadRequest`` for structure and links to
    consensus-specs.
    """

    witness: ExecutionWitness
    """
    Execution witness material required to re-execute the core
    state transition function statelessly.
    """

    chain_config: ChainConfig
    """
    Chain configuration values needed during stateless validation.
    """

    public_keys: Tuple[Bytes, ...]
    """
    Recovered transaction public keys, in transaction order.
    """


@slotted_freezable
@dataclass
class StatelessValidationResult:
    """
    Result returned by stateless validation.
    """

    new_payload_request_root: Hash32
    successful_validation: bool


def compute_new_payload_request_root(
    stateless_input: StatelessInput,
) -> Hash32:
    """
    Compute the request root for a stateless input.

    TODO: Replace this with ``new_payload_request.tree_hash_root``.

    # For readability, we can convert to NewPayloadRequestHeader and
    # then the payload request root.
    """
    raise NotImplementedError


def new_payload_request_to_block(
    new_payload_request: NewPayloadRequest,
) -> Block:
    """Convert a ``NewPayloadRequest`` into a ``Block``."""
    return _payload_block(
        new_payload_request.execution_payload,
        new_payload_request.parent_beacon_block_root,
        new_payload_request.execution_requests,
    )


def _parent_header_from_witness(
    witness_headers: Tuple[Bytes, ...],
    parent_hash: Hash32,
) -> Header:
    """
    Extract the parent header from witness headers.

    Find the header whose ``keccak256(rlp)`` matches
    ``parent_hash`` and decode it.
    """
    for header_rlp in witness_headers:
        if keccak256(header_rlp) == parent_hash:
            return rlp.decode_to(Header, header_rlp)
    raise InvalidBlock("Parent header not found in witness")


def _block_hashes_from_witness(
    witness_headers: Tuple[Bytes, ...],
) -> List[Hash32]:
    """
    Build block hash list from witness headers.

    Compute ``keccak256`` of each header RLP. Headers are in
    ascending block number order. The last entry is the parent
    hash (hash of the last header).
    """
    hashes: List[Hash32] = []
    for header_rlp in witness_headers:
        header = rlp.decode_to(Header, header_rlp)
        hashes.append(header.parent_hash)
    if len(witness_headers) > 0:
        hashes.append(keccak256(witness_headers[-1]))
    return hashes


def verify_stateless_new_payload(
    stateless_input: StatelessInput,
) -> StatelessValidationResult:
    """
    Statelessly validate the execution payload.

    Re-execute the block using a witness-backed ``PreState`` and
    verify that the resulting roots and hashes match the block
    header.
    """
    request_root = compute_new_payload_request_root(stateless_input)

    try:
        block = new_payload_request_to_block(
            stateless_input.new_payload_request
        )

        parent_header = _parent_header_from_witness(
            stateless_input.witness.headers,
            block.header.parent_hash,
        )

        node_db = build_node_db(stateless_input.witness.state)
        code_db = build_code_db(stateless_input.witness.codes)
        block_hashes = _block_hashes_from_witness(
            stateless_input.witness.headers,
        )

        chain_context = ChainContext(
            chain_id=stateless_input.chain_config.chain_id,
            block_hashes=block_hashes,
            parent_header=parent_header,
        )

        witness_state = WitnessState(
            _node_db=node_db,
            _state_root=parent_header.state_root,
            _code_db=code_db,
        )

        verify_and_notify_new_payload(
            new_payload_request=stateless_input.new_payload_request,
            pre_state=witness_state,
            chain_context=chain_context,
        )

        return StatelessValidationResult(
            new_payload_request_root=request_root,
            successful_validation=True,
        )

    except (InvalidBlock, AssertionError):
        return StatelessValidationResult(
            new_payload_request_root=request_root,
            successful_validation=False,
        )
