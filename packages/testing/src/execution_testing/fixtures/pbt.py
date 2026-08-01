"""Fork-aware Partitioned Binary Tree fixture definitions."""

from typing import ClassVar, List, Literal

from pydantic import Field

from execution_testing.base_types import Bytes, CamelModel, Hash
from execution_testing.forks import Fork, TransitionFork

from .base import BaseFixture


class PBTEntry(CamelModel):
    """A key/value entry inserted into a Partitioned Binary Tree."""

    key: Bytes
    value: Hash


class PBTTreeRootInput(CamelModel):
    """Inputs for a raw PBT root calculation."""

    entries: List[PBTEntry]


class PBTTreeRootExpected(CamelModel):
    """Expected result of a raw PBT root calculation."""

    root: Hash


class PBTFixture(BaseFixture):
    """A fork-aware PBT conformance fixture."""

    format_name: ClassVar[str] = "pbt_test"
    description: ClassVar[str] = (
        "Tests raw Partitioned Binary Tree operations."
    )

    fork: Fork | TransitionFork = Field(..., alias="network")
    operation: Literal["tree_root"]
    input: PBTTreeRootInput
    expected: PBTTreeRootExpected

    def get_fork(self) -> Fork | TransitionFork:
        """Return the protocol fork defining the PBT behavior."""
        return self.fork
