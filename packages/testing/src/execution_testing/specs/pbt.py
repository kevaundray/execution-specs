"""Fork-aware Partitioned Binary Tree test specification."""

from typing import List, Type

from ethereum.binary_trie.trie import BinaryTrie, root, trie_set
from ethereum_types.bytes import Bytes as EELSBytes
from ethereum_types.bytes import Bytes32

from execution_testing.base_types import Hash
from execution_testing.fixtures import FixtureFormat
from execution_testing.fixtures.pbt import (
    PBTEntry,
    PBTFixture,
    PBTTreeRootExpected,
    PBTTreeRootInput,
)

from .base_direct import BaseDirectTest


class PBTTest(BaseDirectTest):
    """Declarative inputs for a raw PBT root test."""

    supported_fixture_formats = (PBTFixture,)

    entries: List[PBTEntry]

    @classmethod
    def pytest_parameter_name(cls) -> str:
        """Return the conventional PBT filler fixture name."""
        return "pbt_test"

    def generate(self, *, fixture_format: FixtureFormat) -> PBTFixture:
        """Fill the expected root using the EELS PBT implementation."""
        assert fixture_format is PBTFixture
        trie = BinaryTrie()
        for entry in self.entries:
            trie_set(
                trie,
                EELSBytes(entry.key),
                Bytes32(entry.value),
            )

        return PBTFixture(
            fork=self.fork,
            operation="tree_root",
            input=PBTTreeRootInput(entries=self.entries),
            expected=PBTTreeRootExpected(root=Hash(root(trie))),
        )


PBTTestFiller = Type[PBTTest]
