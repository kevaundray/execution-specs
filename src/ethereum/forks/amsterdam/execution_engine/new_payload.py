"""
Payload verification.
"""

from typing import Dict, Optional, Sequence, Tuple

from ethereum_rlp import rlp
from ethereum_types.bytes import Bytes, Bytes32
from ethereum_types.numeric import U256

from ethereum.crypto.hash import Hash32, keccak256
from ethereum.exceptions import InvalidBlock
from ethereum.state import Account, Address, PreState, Root

from ..blocks import Block
from ..fork import (
    ChainContext,
    execute_block,
    get_last_256_block_hashes,
)
from ..fork_types import VersionedHash
from ..state import apply_changes_to_state
from ..transactions import BlobTransaction, decode_transaction
from .types import ExecutionEngine, ExecutionPayload, NewPayloadRequest
from .validation_helpers import _payload_block, _payload_header


def is_valid_block_hash(
    execution_payload: ExecutionPayload,
    parent_beacon_block_root: Root,
    execution_requests_list: Sequence[bytes],
) -> bool:
    """
    Return ``True`` if and only if ``execution_payload.block_hash`` is
    computed correctly.
    """
    try:
        header = _payload_header(
            execution_payload,
            parent_beacon_block_root,
            tuple(execution_requests_list),
        )
    except Exception:
        # Any decoding or conversion failure means the payload
        # cannot produce a valid header.
        return False
    return keccak256(rlp.encode(header)) == execution_payload.block_hash


def is_valid_versioned_hashes(
    new_payload_request: NewPayloadRequest,
) -> bool:
    """
    Return ``True`` if and only if the versioned hashes computed by blob
    transactions in ``new_payload_request.execution_payload`` match
    ``new_payload_request.versioned_hashes``.
    """
    computed_versioned_hashes: list[VersionedHash] = []

    try:
        for encoded_tx in new_payload_request.execution_payload.transactions:
            tx = decode_transaction(encoded_tx)
            if isinstance(tx, BlobTransaction):
                computed_versioned_hashes.extend(tx.blob_versioned_hashes)
    except Exception:
        # Any decoding failure means versioned hashes cannot be
        # verified.
        return False

    return tuple(computed_versioned_hashes) == (
        new_payload_request.versioned_hashes
    )


def verify_and_notify_new_payload(
    new_payload_request: NewPayloadRequest,
    pre_state: PreState,
    chain_context: ChainContext,
) -> Tuple[
    Block,
    Dict[Address, Optional[Account]],
    Dict[Address, Dict[Bytes32, U256]],
    Dict[Hash32, Bytes],
]:
    """
    Validate a payload and execute the block against a ``PreState``.

    Perform payload-level checks (empty transactions, block hash,
    versioned hashes), then delegate to ``execute_block`` for header
    validation and execution.  Raise ``InvalidBlock`` on any
    validation failure.

    Parameters
    ----------
    new_payload_request :
        The consensus-layer payload request to validate.
    pre_state :
        Pre-execution state provider (``State``, ``WitnessState``, etc.).
    chain_context :
        Chain-level context (chain ID, block hashes, parent header).

    Returns
    -------
    block :
        The block derived from the payload.
    account_changes :
        Per-address account diffs produced by execution.
    storage_changes :
        Per-address storage diffs produced by execution.
    code_changes :
        New bytecodes (keyed by code hash) introduced by execution.

    """
    payload = new_payload_request.execution_payload
    parent_beacon_block_root = new_payload_request.parent_beacon_block_root
    execution_requests_list = new_payload_request.execution_requests

    if b"" in payload.transactions:
        raise InvalidBlock("Empty transaction in payload")

    if not is_valid_block_hash(
        payload,
        parent_beacon_block_root,
        execution_requests_list,
    ):
        raise InvalidBlock("Invalid block hash")

    if not is_valid_versioned_hashes(new_payload_request):
        raise InvalidBlock("Invalid versioned hashes")

    block = _payload_block(
        payload,
        parent_beacon_block_root,
        tuple(execution_requests_list),
    )

    account_changes, storage_changes, code_changes = execute_block(
        block, pre_state, chain_context
    )

    return block, account_changes, storage_changes, code_changes


def chain_verify_and_notify_new_payload(
    chain: ExecutionEngine,
    new_payload_request: NewPayloadRequest,
) -> bool:
    """
    Validate the payload and, if valid, apply it to the chain.

    Build a block from the payload, run chain-level header validation,
    then delegate to ``verify_and_notify_new_payload`` for payload
    checks and execution.  On success, apply the resulting diffs to
    ``chain.state`` and append the block.
    """
    payload = new_payload_request.execution_payload
    parent_beacon_block_root = new_payload_request.parent_beacon_block_root
    execution_requests_list = new_payload_request.execution_requests

    try:
        block = _payload_block(
            payload,
            parent_beacon_block_root,
            tuple(execution_requests_list),
        )
    except Exception:
        return False

    chain_context = ChainContext(
        chain_id=chain.chain_id,
        block_hashes=get_last_256_block_hashes(chain),
        parent_header=chain.blocks[-1].header,
    )

    try:
        block, account_changes, storage_changes, code_changes = (
            verify_and_notify_new_payload(
                new_payload_request=new_payload_request,
                pre_state=chain.state,
                chain_context=chain_context,
            )
        )
    except Exception:
        return False

    apply_changes_to_state(
        chain.state,
        account_changes,
        storage_changes,
        code_changes,
    )

    chain.blocks.append(block)
    if len(chain.blocks) > 255:
        chain.blocks = chain.blocks[-255:]

    return True
