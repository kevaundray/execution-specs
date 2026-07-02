"""Differential tests: eth_trie_rs state root vs the pure-Python spec."""

import pytest

from ethereum.merkle_patricia_trie import EMPTY_TRIE_ROOT

eth_trie_rs = pytest.importorskip("eth_trie_rs")


def test_empty_state_root() -> None:
    assert eth_trie_rs.state_root([], {}) == bytes(EMPTY_TRIE_ROOT)
