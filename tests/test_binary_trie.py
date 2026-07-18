"""
Tests for the raw binary tree structure.

The tree is a compressed binary radix trie: branch, extension, and
leaf nodes, with domain-separated hashing. The tests verify the
spec-style implementation in `ethereum.binary_trie.trie` against
hand-computed hashes and against an independent insertion-based
reference. Because the spec rebuilds the canonical form from scratch
while the reference builds it incrementally, agreement on every root
is also a check that both constructions produce the same canonical
structure.
"""

import random
from typing import Dict, List, Optional

import pytest
from blake3 import blake3
from ethereum_types.bytes import Bytes, Bytes32

from ethereum.binary_trie.trie import (
    EMPTY_TRIE_ROOT,
    BinaryTrie,
    bytes_to_bit_list,
    copy_trie,
    root,
    trie_get,
    trie_set,
)

# These helpers re-derive bit manipulation and node hashing
# independently of `ethereum.binary_trie`, to try and catch bugs.


def _bits(data: bytes) -> List[int]:
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]


def _pack_padded(bits: List[int]) -> bytes:
    packed = bytearray((len(bits) + 7) // 8)
    for i, bit in enumerate(bits):
        packed[i // 8] |= bit << (7 - i % 8)
    return bytes(packed)


def _leaf_hash(key: bytes, value: bytes) -> bytes:
    return blake3(b"\x00" + key + value).digest()


def _extension_hash(prefix: List[int], child: bytes) -> bytes:
    return blake3(
        b"\x01" + len(prefix).to_bytes(2, "big") + _pack_padded(prefix) + child
    ).digest()


def _branch_hash(left: bytes, right: bytes) -> bytes:
    return blake3(b"\x02" + left + right).digest()


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


def test_single_key_is_a_leaf_at_the_root() -> None:
    """
    A trie with one key commits to a single leaf, with no extension
    above it: the leaf carries its full key.
    """
    key = Bytes(b"\x00" + b"\x42" * 32 + b"\x07")
    value = Bytes32(b"\x11" * 32)

    trie = BinaryTrie()
    trie_set(trie, key, value)

    assert root(trie) == _leaf_hash(key, value)


def test_keys_sharing_a_stem_split_under_one_extension() -> None:
    """
    Two keys sharing a 33-byte stem diverge in their final byte's
    first bit: one extension over the whole stem, then a branch over
    two leaves.
    """
    stem = b"\x00" + b"\x42" * 32
    low_key = Bytes(stem + b"\x00")
    high_key = Bytes(stem + b"\xff")
    low_value = Bytes32(b"\x01" * 32)
    high_value = Bytes32(b"\x02" * 32)

    trie = BinaryTrie()
    trie_set(trie, low_key, low_value)
    trie_set(trie, high_key, high_value)

    assert root(trie) == _extension_hash(
        _bits(stem),
        _branch_hash(
            _leaf_hash(low_key, low_value),
            _leaf_hash(high_key, high_value),
        ),
    )


def test_first_bit_divergence_has_no_extension() -> None:
    """
    Keys differing in their first bit branch at the root with no
    extension above the branch.
    """
    zero_key = Bytes(b"\x00" * 34)
    one_key = Bytes(b"\xff" * 66)
    value = Bytes32(b"\x33" * 32)

    trie = BinaryTrie()
    trie_set(trie, zero_key, value)
    trie_set(trie, one_key, value)

    assert root(trie) == _branch_hash(
        _leaf_hash(zero_key, value), _leaf_hash(one_key, value)
    )


def test_canonical_form_example() -> None:
    """
    Three keys sharing a stem, with sub-indices 0, 1, and 128: an
    extension over the stem, a branch on the first sub-index bit, a
    six-bit extension and branch over the two low leaves, and the
    high leaf sitting directly under the top branch with no extension
    above it.
    """
    stem = b"\xff" + b"\xab" * 32
    key_0 = Bytes(stem + b"\x00")
    key_1 = Bytes(stem + b"\x01")
    key_128 = Bytes(stem + b"\x80")
    value = Bytes32(b"\x44" * 32)

    trie = BinaryTrie()
    for key in (key_0, key_1, key_128):
        trie_set(trie, key, value)

    low_side = _extension_hash(
        [0] * 6,
        _branch_hash(
            _leaf_hash(key_0, value),
            _leaf_hash(key_1, value),
        ),
    )
    assert root(trie) == _extension_hash(
        _bits(stem),
        _branch_hash(low_side, _leaf_hash(key_128, value)),
    )


def test_zero_value_is_not_absence() -> None:
    """
    Storing 32 zero bytes commits differently from storing nothing.
    """
    key = Bytes32(b"\x07" * 31 + b"\x00")

    trie = BinaryTrie()
    trie_set(trie, key, Bytes32(b"\x00" * 32))

    assert root(trie) != EMPTY_TRIE_ROOT


def test_prefix_key_violation_is_rejected() -> None:
    """
    A key that is a prefix of another key makes the tree ill-defined,
    and computing the root fails the prefix-freeness assertion.
    """
    trie = BinaryTrie()
    trie_set(trie, Bytes(b"\xaa" * 34), Bytes32(b"\x01" * 32))
    trie_set(trie, Bytes(b"\xaa" * 34 + b"\xbb" * 32), Bytes32(b"\x02" * 32))

    with pytest.raises(AssertionError):
        root(trie)


class ReferenceRadixTree:
    """
    Insertion-based compressed binary radix tree, used only to
    cross-check `ethereum.binary_trie`.

    Follows the standard descend/split insertion algorithm and hashes
    with independently written tagged rules, so agreement with the
    rebuild-from-scratch spec implementation also checks that both
    produce the same canonical structure.
    """

    class Leaf:
        """
        Terminal node of the reference implementation.
        """

        def __init__(self, key: bytes, value: bytes) -> None:
            self.key = key
            self.value = value

    class Extension:
        """
        Compression node of the reference implementation.
        """

        def __init__(self, prefix: List[int], child: object) -> None:
            self.prefix = prefix
            self.child = child

    class Branch:
        """
        Binary branch node of the reference implementation.
        """

        def __init__(self, left: object, right: object) -> None:
            self.left = left
            self.right = right

    def __init__(self) -> None:
        self.root: Optional[object] = None

    def insert(self, key: bytes, value: bytes) -> None:
        """
        Insert `key` and `value`, splitting nodes as needed.
        """
        assert len(value) == 32
        if self.root is None:
            self.root = self.Leaf(key, value)
            return
        self.root = self._insert(self.root, _bits(key), key, value, 0)

    def _insert(  # type: ignore[no-untyped-def]
        self, node, bits, key, value, depth
    ):
        if isinstance(node, self.Leaf):
            if node.key == key:
                node.value = value
                return node
            other_bits = _bits(node.key)
            run = 0
            while True:
                position = depth + run
                assert position < len(bits) and position < len(other_bits)
                if bits[position] != other_bits[position]:
                    break
                run += 1
            leaf = self.Leaf(key, value)
            if bits[depth + run] == 0:
                branch = self.Branch(leaf, node)
            else:
                branch = self.Branch(node, leaf)
            if run > 0:
                return self.Extension(bits[depth : depth + run], branch)
            return branch

        if isinstance(node, self.Extension):
            matched = 0
            while matched < len(node.prefix):
                position = depth + matched
                assert position < len(bits)
                if bits[position] != node.prefix[matched]:
                    break
                matched += 1
            if matched == len(node.prefix):
                node.child = self._insert(
                    node.child, bits, key, value, depth + matched
                )
                return node
            # Split the extension at the first mismatched bit.
            remaining = node.prefix[matched + 1 :]
            if remaining:
                existing = self.Extension(remaining, node.child)
            else:
                existing = node.child
            leaf = self.Leaf(key, value)
            if bits[depth + matched] == 0:
                branch = self.Branch(leaf, existing)
            else:
                branch = self.Branch(existing, leaf)
            if matched > 0:
                return self.Extension(node.prefix[:matched], branch)
            return branch

        assert depth < len(bits)
        if bits[depth] == 0:
            node.left = self._insert(node.left, bits, key, value, depth + 1)
        else:
            node.right = self._insert(node.right, bits, key, value, depth + 1)
        return node

    def merkelize(self) -> bytes:
        """
        Compute the root hash of the reference tree.
        """
        if self.root is None:
            return b"\x00" * 32

        def _hash(node: object) -> bytes:
            if isinstance(node, self.Leaf):
                return _leaf_hash(node.key, node.value)
            if isinstance(node, self.Extension):
                return _extension_hash(node.prefix, _hash(node.child))
            assert isinstance(node, self.Branch)
            return _branch_hash(_hash(node.left), _hash(node.right))

        return _hash(self.root)


def random_entries(rng: random.Random) -> Dict[bytes, bytes]:
    """
    Generate a random key/value set mixing three key shapes: fully
    random keys, keys sharing an existing 31-byte prefix, and keys
    sharing a shorter prefix (forcing splits at every depth).
    """
    entries: Dict[bytes, bytes] = {}
    for _ in range(rng.randrange(1, 40)):
        key = rng.randbytes(32)
        entries[key] = rng.randbytes(32)

        # Same first 31 bytes, different final byte: long shared
        # prefixes compressed by one extension.
        for _ in range(rng.randrange(0, 3)):
            entries[key[:31] + rng.randbytes(1)] = rng.randbytes(32)

        # Same first 1, 7, or 30 bytes: splits 8, 56, or 240 bits
        # deep.
        for prefix_length in (1, 7, 30):
            if rng.random() < 0.2:
                cousin = (
                    key[:prefix_length]
                    + rng.randbytes(31 - prefix_length)
                    + rng.randbytes(1)
                )
                entries[cousin] = rng.randbytes(32)
    return entries


def test_root_matches_reference_implementation() -> None:
    """
    Randomized key/value sets produce the same root in the spec-style
    rebuild and the insertion-based reference.
    """
    rng = random.Random(8297)

    for trial in range(20):
        entries = random_entries(rng)

        reference = ReferenceRadixTree()
        trie = BinaryTrie()
        for key, value in entries.items():
            reference.insert(key, value)
            trie_set(trie, Bytes(key), Bytes32(value))

        assert root(trie) == reference.merkelize(), f"trial {trial}"


def test_root_matches_reference_with_variable_length_keys() -> None:
    """
    Keys shaped like the embedding's, 34-byte account and code keys,
    66-byte storage keys produce the same root in both
    implementations when mixed in one tree.
    """
    rng = random.Random(11832)

    for trial in range(10):
        entries: Dict[bytes, bytes] = {}
        for _ in range(rng.randrange(1, 30)):
            if rng.random() < 0.5:
                # Account or code zone: 34-byte keys.
                prefix = bytes([rng.choice((0, 1))]) + rng.randbytes(32)
            else:
                # Storage zone: 66-byte keys.
                prefix = b"\xff" + rng.randbytes(64)
            for _ in range(rng.randrange(1, 4)):
                entries[prefix + rng.randbytes(1)] = rng.randbytes(32)

        reference = ReferenceRadixTree()
        trie = BinaryTrie()
        for key, value in entries.items():
            reference.insert(key, value)
            trie_set(trie, Bytes(key), Bytes32(value))

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
