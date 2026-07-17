"""
Tests for the raw EIP-8297 binary tree structure.

The tests verify the spec-style implementation in
`ethereum.binary_trie.trie` against hand-computed hashes and against
a near-verbatim port of the EIP's insertion-based reference
implementation.
"""

import random
from typing import Dict, List, Optional

import pytest
from blake3 import blake3
from ethereum_types.bytes import Bytes, Bytes32

from ethereum.binary_trie.trie import (
    EMPTY_TRIE_ROOT,
    BinaryTrie,
    bit_list_to_bytes,
    bytes_to_bit_list,
    copy_trie,
    root,
    trie_get,
    trie_set,
)


def reference_hash(data: Optional[bytes]) -> bytes:
    """
    Hash node data following EIP-8297's merkleization rules.

    `None` is an absent leaf value; 64 zero bytes is a pairing of two
    empty commitments. Both commit to 32 zero bytes so that empty
    subtrees stay all-zero at every level of the tree.
    """
    if data is None or data == b"\x00" * 64:
        return b"\x00" * 32
    assert len(data) in (32, 64)
    return blake3(data).digest()


def reference_stem_node_hash(stem: bytes, values: Dict[int, bytes]) -> bytes:
    """
    Hash a stem node holding `values` (sub-index to 32-byte value) by
    following the EIP merkleization rules directly.
    """
    level = [reference_hash(values.get(i)) for i in range(256)]
    while len(level) > 1:
        level = [
            reference_hash(level[i] + level[i + 1])
            for i in range(0, len(level), 2)
        ]
    return reference_hash(stem + b"\x00" + level[0])


def test_bytes_to_bit_list_is_msb_first() -> None:
    """
    Bytes expand to bits most significant bit first.
    """
    assert bytes_to_bit_list(Bytes(b"\x80")) == Bytes(
        bytes([1, 0, 0, 0, 0, 0, 0, 0])
    )
    assert bytes_to_bit_list(Bytes(b"\x01")) == Bytes(
        bytes([0, 0, 0, 0, 0, 0, 0, 1])
    )
    assert bytes_to_bit_list(Bytes(b"\xa5")) == Bytes(
        bytes([1, 0, 1, 0, 0, 1, 0, 1])
    )


def test_bit_list_round_trip() -> None:
    """
    Packing the bits of any byte sequence reproduces the sequence.
    """
    data = Bytes(bytes(range(256)))
    assert bit_list_to_bytes(bytes_to_bit_list(data)) == data


def test_bit_list_to_bytes_rejects_partial_bytes() -> None:
    """
    A bit list whose length is not a multiple of eight is rejected.
    """
    with pytest.raises(AssertionError):
        bit_list_to_bytes(Bytes(bytes([1] * 9)))


def test_empty_trie_root_is_all_zeros() -> None:
    """
    An empty trie commits to 32 zero bytes.
    """
    trie: BinaryTrie = BinaryTrie(_data={})
    assert root(trie) == b"\x00" * 32
    assert EMPTY_TRIE_ROOT == b"\x00" * 32


def test_trie_set_and_get() -> None:
    """
    Values can be stored, retrieved, and overwritten.
    """
    trie = BinaryTrie()
    key = Bytes32(b"\x01" * 32)
    value = Bytes32(b"\x02" * 32)

    assert trie_get(trie, key) is None
    trie_set(trie, key, value)
    assert trie_get(trie, key) == value

    replacement = Bytes32(b"\x03" * 32)
    trie_set(trie, key, replacement)
    assert trie_get(trie, key) == replacement


def test_copy_trie_is_independent() -> None:
    """
    Mutating a copy leaves the original untouched, and vice versa.
    """
    key = Bytes32(b"\x01" * 32)
    other_key = Bytes32(b"\x02" * 32)
    value = Bytes32(b"\x03" * 32)

    original = BinaryTrie()
    trie_set(original, key, value)

    duplicate = copy_trie(original)
    assert trie_get(duplicate, key) == value

    trie_set(duplicate, other_key, value)
    assert trie_get(original, other_key) is None
    assert root(duplicate) != root(original)


def test_single_key_root() -> None:
    """
    A trie with one key is a single stem node at the root.
    """
    stem = b"\x00" * 31
    sub_index = 5
    key = Bytes32(stem + bytes([sub_index]))
    value = Bytes32(b"\x11" * 32)

    trie = BinaryTrie()
    trie_set(trie, key, value)

    assert root(trie) == reference_stem_node_hash(stem, {sub_index: value})


def test_same_stem_values_share_a_stem_node() -> None:
    """
    Keys sharing a stem land in one stem node's group of values.
    """
    stem = b"\x42" * 31
    first_value = Bytes32(b"\x01" * 32)
    second_value = Bytes32(b"\x02" * 32)

    trie = BinaryTrie()
    trie_set(trie, Bytes32(stem + b"\x00"), first_value)
    trie_set(trie, Bytes32(stem + b"\xff"), second_value)

    assert root(trie) == reference_stem_node_hash(
        stem, {0: first_value, 255: second_value}
    )


def test_stems_split_at_first_differing_bit() -> None:
    """
    Stems sharing a prefix split under a chain of internal nodes, one
    per shared bit, each with an empty sibling subtree.
    """
    # Both first bytes are 0b0000000x, so the stems share their first
    # 7 bits and the split sits at depth 7.
    left_stem = b"\x00" + b"\xaa" * 30
    right_stem = b"\x01" + b"\xbb" * 30
    value = Bytes32(b"\x33" * 32)

    trie = BinaryTrie()
    trie_set(trie, Bytes32(left_stem + b"\x00"), value)
    trie_set(trie, Bytes32(right_stem + b"\x00"), value)

    expected = reference_hash(
        reference_stem_node_hash(left_stem, {0: value})
        + reference_stem_node_hash(right_stem, {0: value})
    )
    for _ in range(7):
        expected = reference_hash(expected + b"\x00" * 32)

    assert root(trie) == expected


def test_storage_zone_splits_at_depth_one() -> None:
    """
    A storage-zone stem and a non-storage stem differ in bit 0, so
    the root is an internal node over the two stem nodes directly.
    """
    account_stem = b"\x00" * 31
    storage_stem_ = b"\x80" + b"\x00" * 30
    value = Bytes32(b"\x01" * 32)

    trie = BinaryTrie()
    trie_set(trie, Bytes32(account_stem + b"\x00"), value)
    trie_set(trie, Bytes32(storage_stem_ + b"\x00"), value)

    assert root(trie) == reference_hash(
        reference_stem_node_hash(account_stem, {0: value})
        + reference_stem_node_hash(storage_stem_, {0: value})
    )


def test_zero_value_is_not_absence() -> None:
    """
    Storing 32 zero bytes commits differently from storing nothing.
    """
    key = Bytes32(b"\x07" * 31 + b"\x00")

    trie = BinaryTrie()
    trie_set(trie, key, Bytes32(b"\x00" * 32))

    assert root(trie) != EMPTY_TRIE_ROOT


class ReferenceBinaryTree:
    """
    Near-verbatim port of EIP-8297's insertion-based reference
    implementation, used only to cross-check `ethereum.binary_trie`.
    """

    class StemNode:
        """
        Stem node of the reference implementation.
        """

        def __init__(self, stem: bytes) -> None:
            assert len(stem) == 31
            self.stem = stem
            self.values: List[Optional[bytes]] = [None] * 256

        def set_value(self, index: int, value: bytes) -> None:
            """
            Store `value` at `index` within this stem's group.
            """
            self.values[index] = value

    class InternalNode:
        """
        Internal (binary branch) node of the reference implementation.
        """

        def __init__(self) -> None:
            self.left: Optional[object] = None
            self.right: Optional[object] = None

    def __init__(self) -> None:
        self.root: Optional[object] = None

    @staticmethod
    def _bytes_to_bits(data: bytes) -> List[int]:
        return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]

    @staticmethod
    def _bits_to_bytes(bits: List[int]) -> bytes:
        return bytes(
            sum(bits[i + j] << (7 - j) for j in range(8))
            for i in range(0, len(bits), 8)
        )

    def insert(self, key: bytes, value: bytes) -> None:
        """
        Insert `key` and `value`, splitting stem nodes as needed.
        """
        assert len(key) == 32
        assert len(value) == 32
        stem = key[:31]
        subindex = key[31]

        if self.root is None:
            self.root = self.StemNode(stem)
            self.root.set_value(subindex, value)
            return

        self.root = self._insert(self.root, stem, subindex, value, 0)

    def _insert(  # type: ignore[no-untyped-def]
        self, node, stem, subindex, value, depth
    ):
        assert depth < 248

        if node is None:
            node = self.StemNode(stem)
            node.set_value(subindex, value)
            return node

        stem_bits = self._bytes_to_bits(stem)
        if isinstance(node, self.StemNode):
            if node.stem == stem:
                node.set_value(subindex, value)
                return node
            existing_stem_bits = self._bytes_to_bits(node.stem)
            return self._split_leaf(
                node, stem_bits, existing_stem_bits, subindex, value, depth
            )

        bit = stem_bits[depth]
        if bit == 0:
            node.left = self._insert(
                node.left, stem, subindex, value, depth + 1
            )
        else:
            node.right = self._insert(
                node.right, stem, subindex, value, depth + 1
            )
        return node

    def _split_leaf(  # type: ignore[no-untyped-def]
        self, leaf, stem_bits, existing_stem_bits, subindex, value, depth
    ):
        if stem_bits[depth] == existing_stem_bits[depth]:
            new_internal = self.InternalNode()
            bit = stem_bits[depth]
            if bit == 0:
                new_internal.left = self._split_leaf(
                    leaf,
                    stem_bits,
                    existing_stem_bits,
                    subindex,
                    value,
                    depth + 1,
                )
            else:
                new_internal.right = self._split_leaf(
                    leaf,
                    stem_bits,
                    existing_stem_bits,
                    subindex,
                    value,
                    depth + 1,
                )
            return new_internal
        else:
            new_internal = self.InternalNode()
            bit = stem_bits[depth]
            stem = self._bits_to_bytes(stem_bits)
            if bit == 0:
                new_internal.left = self.StemNode(stem)
                new_internal.left.set_value(subindex, value)
                new_internal.right = leaf
            else:
                new_internal.right = self.StemNode(stem)
                new_internal.right.set_value(subindex, value)
                new_internal.left = leaf
            return new_internal

    def merkelize(self) -> bytes:
        """
        Compute the root hash of the reference tree.
        """

        def _merkelize(node):  # type: ignore[no-untyped-def]
            if node is None:
                return b"\x00" * 32
            if isinstance(node, self.InternalNode):
                left_hash = _merkelize(node.left)
                right_hash = _merkelize(node.right)
                return reference_hash(left_hash + right_hash)

            level = [reference_hash(x) for x in node.values]
            while len(level) > 1:
                new_level = []
                for i in range(0, len(level), 2):
                    new_level.append(reference_hash(level[i] + level[i + 1]))
                level = new_level
            return reference_hash(node.stem + b"\0" + level[0])

        return _merkelize(self.root)


def random_entries(rng: random.Random) -> Dict[bytes, bytes]:
    """
    Generate a random key/value set mixing three key shapes: fully
    random keys, keys sharing an existing stem, and keys sharing a
    long stem prefix (forcing deep splits in the outer trie).
    """
    entries: Dict[bytes, bytes] = {}
    for _ in range(rng.randrange(1, 40)):
        key = rng.randbytes(32)
        entries[key] = rng.randbytes(32)

        # Same stem, different sub-index: lands in the same stem node.
        for _ in range(rng.randrange(0, 3)):
            entries[key[:31] + rng.randbytes(1)] = rng.randbytes(32)

        # Same first 1, 7, or 30 stem bytes: splits 8, 56, or 240
        # bits deep.
        for prefix_length in (1, 7, 30):
            if rng.random() < 0.2:
                cousin = (
                    key[:prefix_length]
                    + rng.randbytes(31 - prefix_length)
                    + rng.randbytes(1)
                )
                entries[cousin] = rng.randbytes(32)
    return entries


def test_root_matches_eip_reference_implementation() -> None:
    """
    Randomized key/value sets produce the same root in the spec-style
    implementation and the EIP's insertion-based reference.
    """
    rng = random.Random(8297)

    for trial in range(20):
        entries = random_entries(rng)

        reference = ReferenceBinaryTree()
        trie = BinaryTrie()
        for key, value in entries.items():
            reference.insert(key, value)
            trie_set(trie, Bytes32(key), Bytes32(value))

        assert root(trie) == reference.merkelize(), f"trial {trial}"


def test_root_is_insertion_order_independent() -> None:
    """
    The root depends only on the contents, not insertion order.
    """
    rng = random.Random(1234)
    entries = [
        (Bytes32(rng.randbytes(32)), Bytes32(rng.randbytes(32)))
        for _ in range(16)
    ]

    forward = BinaryTrie()
    for key, value in entries:
        trie_set(forward, key, value)

    backward = BinaryTrie()
    for key, value in reversed(entries):
        trie_set(backward, key, value)

    assert root(forward) == root(backward)
