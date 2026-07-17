"""
Tests for the EIP-8297 embedding of state into the binary tree.

The key-derivation tests rebuild the expected keys bit by bit from
BLAKE3 output, independently of the implementation, following the
vectors in the EIP's "Test Cases" section.
"""

from typing import List

import pytest
from blake3 import blake3
from ethereum_types.bytes import Bytes, Bytes20, Bytes32
from ethereum_types.numeric import U256, Uint

from ethereum.binary_trie.embedding import (
    ACCOUNT_ZONE,
    BASIC_DATA_LEAF_KEY,
    CODE_HASH_LEAF_KEY,
    CODE_OFFSET,
    CODE_ZONE,
    EMPTY_CODE_HASH,
    HEADER_STORAGE_OFFSET,
    STEM_SUBTREE_WIDTH,
    Address32,
    address20_to_address32,
    chunkify_code,
    encode_basic_data,
    get_tree_key_for_basic_data,
    get_tree_key_for_code_chunk,
    get_tree_key_for_code_hash,
    get_tree_key_for_storage_slot,
    key_hash,
    zone_stem,
)
from ethereum.state import EMPTY_CODE_HASH as MPT_STATE_EMPTY_CODE_HASH

ADDRESS = Address32(b"\x00" * 12 + b"\xaa" * 20)


# These helpers re-derive bit manipulation independently
# of `ethereum.binary_trie`, to try and catch bugs.
# TODO: maybe it is simple enough that we can delete?
def _bits(data: bytes) -> List[int]:
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]


def _pack_bits(bits: List[int]) -> bytes:
    return bytes(
        sum(bits[i + j] << (7 - j) for j in range(8))
        for i in range(0, len(bits), 8)
    )


def _header_stem(address: Address32) -> bytes:
    """
    Build `0x0 || H(address)[:244]`, the account header stem, from
    scratch.
    """
    digest_bits = _bits(blake3(bytes(address)).digest())
    return _pack_bits([0, 0, 0, 0] + digest_bits[:244])


def test_embedding_constants() -> None:
    """
    The embedding constants match the EIP's parameter table.
    """
    assert BASIC_DATA_LEAF_KEY == Uint(0)
    assert CODE_HASH_LEAF_KEY == Uint(1)
    assert HEADER_STORAGE_OFFSET == Uint(64)
    assert CODE_OFFSET == Uint(128)
    assert STEM_SUBTREE_WIDTH == Uint(256)
    assert ACCOUNT_ZONE == Uint(0)
    assert CODE_ZONE == Uint(1)


def test_address20_to_address32_prepends_zeros() -> None:
    """
    Legacy addresses convert by prepending 12 zero bytes.
    """
    address = Bytes20(b"\xaa" * 20)
    assert address20_to_address32(address) == ADDRESS


def test_empty_code_hash_is_keccak_of_empty() -> None:
    """
    The empty-code leaf value is the classic Keccak empty-code hash,
    and agrees with the shared MPT state module's definition.
    """
    # The well-known keccak256("") vector, quoted in EIP-1052 and
    # stored as `codeHash` by every EOA in the current MPT state.
    # Hardcoded so the check does not recompute with the same library
    # it is verifying.
    assert EMPTY_CODE_HASH == bytes.fromhex(
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
    assert EMPTY_CODE_HASH == MPT_STATE_EMPTY_CODE_HASH


def test_key_hash_is_blake3() -> None:
    """
    Key derivation hashes with BLAKE3.
    """
    assert key_hash(ADDRESS) == blake3(bytes(ADDRESS)).digest()


def test_zone_stem_places_zone_in_high_nibble() -> None:
    """
    A zone stem is the 4-bit zone followed by 244 bits of digest.

    The zone displaces 4 of the 248 digest bits a stem carried before
    partitioning; the EIP shows the reduced collision resistance is
    still far beyond reach.
    TODO: Though we will change this for the option in Vitalik's
    change
    """
    digest = blake3(b"digest").digest()
    for zone in range(16):
        stem = zone_stem(Uint(zone), digest)
        assert len(stem) == 31
        assert stem[0] >> 4 == zone
        expected = _pack_bits(
            _bits(bytes([zone << 4]))[:4] + _bits(digest)[:244]
        )
        assert stem == expected


def test_zone_stem_rejects_zone_wider_than_four_bits() -> None:
    """
    A zone identifier that does not fit in the 4 zone bits is
    rejected.
    """
    digest = blake3(b"digest").digest()
    with pytest.raises(AssertionError):
        zone_stem(Uint(16), digest)


def test_header_key_vectors() -> None:
    """
    EIP vector: header keys are `0x0 || H(A)[:244]` plus the leaf's
    sub-index.
    """
    stem = _header_stem(ADDRESS)

    assert get_tree_key_for_basic_data(ADDRESS) == stem + b"\x00"
    assert get_tree_key_for_code_hash(ADDRESS) == stem + b"\x01"


def test_storage_slot_in_header_vector() -> None:
    """
    EIP vector: storage slot 5 lives in the header at sub-index 0x45.
    """
    key = get_tree_key_for_storage_slot(ADDRESS, U256(5))
    assert key == _header_stem(ADDRESS) + bytes([0x45])


def test_storage_slot_overflow_vector() -> None:
    """
    EIP vector: slot 1000 maps to tree index 3, sub-index 0xE8, with
    stem `1 || H(A)[:60] || H(A || 3)[:187]`.
    """
    prefix = _bits(blake3(bytes(ADDRESS)).digest())[:60]
    suffix = _bits(blake3(bytes(ADDRESS) + (3).to_bytes(32, "big")).digest())[
        :187
    ]
    stem = _pack_bits([1] + prefix + suffix)

    key = get_tree_key_for_storage_slot(ADDRESS, U256(1000))
    assert key == stem + bytes([0xE8])
    # Storage stems are in the storage zone: high bit set.
    assert key[0] >> 7 == 1


def test_storage_slot_boundary_is_64() -> None:
    """
    Slot 63 is the last header slot and slot 64 the first overflow
    slot, landing at tree index 0, sub-index 64.
    """
    assert get_tree_key_for_storage_slot(ADDRESS, U256(63)) == _header_stem(
        ADDRESS
    ) + bytes([127])

    suffix = _bits(blake3(bytes(ADDRESS) + (0).to_bytes(32, "big")).digest())[
        :187
    ]
    overflow_stem = _pack_bits(
        [1] + _bits(blake3(bytes(ADDRESS)).digest())[:60] + suffix
    )
    assert get_tree_key_for_storage_slot(
        ADDRESS, U256(64)
    ) == overflow_stem + bytes([64])


def test_code_chunk_in_header_vector() -> None:
    """
    EIP vector: code chunk 5 lives in the header at sub-index 0x85.
    """
    code_hash = Bytes32(blake3(b"some code").digest())

    key = get_tree_key_for_code_chunk(ADDRESS, code_hash, Uint(5))
    assert key == _header_stem(ADDRESS) + bytes([0x85])


def test_code_chunk_overflow_vector() -> None:
    """
    EIP vector: chunk 300 overflows to sub-index 0xAC with stem
    `0x1 || H(C || 0)[:244]`.
    """
    code_hash = Bytes32(blake3(b"some code").digest())
    digest = blake3(code_hash + (0).to_bytes(32, "big")).digest()
    stem = _pack_bits([0, 0, 0, 1] + _bits(digest)[:244])

    key = get_tree_key_for_code_chunk(ADDRESS, code_hash, Uint(300))
    assert key == stem + bytes([0xAC])


def test_overflow_code_is_content_addressed() -> None:
    """
    Overflow chunks depend only on the code hash; header chunks stay
    per-account.
    """
    code_hash = Bytes32(blake3(b"shared bytecode").digest())
    other = Address32(b"\x00" * 12 + b"\xbb" * 20)

    assert get_tree_key_for_code_chunk(
        ADDRESS, code_hash, Uint(200)
    ) == get_tree_key_for_code_chunk(other, code_hash, Uint(200))
    assert get_tree_key_for_code_chunk(
        ADDRESS, code_hash, Uint(5)
    ) != get_tree_key_for_code_chunk(other, code_hash, Uint(5))


def test_chunkify_empty_code() -> None:
    """
    Empty code produces no chunks.
    """
    assert chunkify_code(Bytes(b"")) == []


def test_chunkify_code_without_pushes_pads_to_31_bytes() -> None:
    """
    Code shorter than a chunk is zero-padded to 31 bytes.
    """
    code = Bytes(b"\x01\x02\x03")  # ADD MUL SUB

    chunks = chunkify_code(code)

    # The leading byte is the push-data offset count (zero here), not
    # padding; only the trailing zeros pad the code to 31 bytes.
    assert chunks == [Bytes32(b"\x00" + code + b"\x00" * 28)]


def test_chunkify_code_eip_example() -> None:
    """
    EIP example: push data spanning a chunk boundary is recorded in
    the second chunk's leading byte.
    """
    # `...PUSH4 99 98 | 97 96 PUSH1 128 MSTORE...` where `|` begins a
    # new chunk; the second chunk records that its first 2 bytes are
    # push data.
    push4 = 0x63
    push1 = 0x60
    mstore = 0x52
    code = Bytes(
        b"\x00" * 28 + bytes([push4, 99, 98, 97, 96, push1, 128, mstore])
    )

    chunks = chunkify_code(code)

    assert len(chunks) == 2
    assert chunks[0] == Bytes32(b"\x00" * 29 + bytes([push4, 99, 98]))
    assert chunks[1] == Bytes32(
        bytes([2, 97, 96, push1, 128, mstore]) + b"\x00" * 26
    )


def test_chunkify_code_caps_leading_push_data_count_at_31() -> None:
    """
    A chunk consisting entirely of push data reports 31 leading push
    data bytes, the chunk-payload maximum, rather than 32.
    """
    push32 = 0x7F
    push_data = bytes(range(1, 33))
    code = Bytes(b"\x00" * 30 + bytes([push32]) + push_data)

    chunks = chunkify_code(code)

    assert len(chunks) == 3
    assert chunks[0] == Bytes32(b"\x00" * 31 + bytes([push32]))
    assert chunks[1] == Bytes32(bytes([31]) + push_data[:31])
    assert chunks[2] == Bytes32(bytes([1]) + push_data[31:] + b"\x00" * 30)


def test_chunkify_code_push_data_truncated_by_end_of_code() -> None:
    """
    A push instruction with its data cut off by the end of the code
    still chunks cleanly.
    """
    push32 = 0x7F
    code = Bytes(bytes([push32]))

    chunks = chunkify_code(code)

    assert chunks == [Bytes32(b"\x00" + bytes([push32]) + b"\x00" * 30)]


def test_encode_basic_data_layout() -> None:
    """
    Basic data packs version, code size, nonce, and balance at the
    offsets given by the EIP.
    """
    code_size_hex = "11223344"
    nonce_hex = "5566778899aabbcc"
    balance_hex = "0123456789abcdef0123456789abcdef"

    value = encode_basic_data(
        code_size=Uint(int(code_size_hex, 16)),
        nonce=Uint(int(nonce_hex, 16)),
        balance=U256(int(balance_hex, 16)),
    )

    assert len(value) == 32
    assert value[0] == 0  # version
    assert value[1:4] == b"\x00" * 3  # reserved
    assert value[4:8] == bytes.fromhex(code_size_hex)
    assert value[8:16] == bytes.fromhex(nonce_hex)
    assert value[16:32] == bytes.fromhex(balance_hex)
