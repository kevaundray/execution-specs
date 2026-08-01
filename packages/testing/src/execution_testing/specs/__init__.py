"""Test spec definitions and utilities."""

from typing import Tuple, Type, TypeAlias

from .base import BaseTest, TestSpec
from .base_direct import BaseDirectTest
from .base_static import BaseStaticTest
from .benchmark import (
    BenchmarkTest,
    BenchmarkTestFiller,
    BenchmarkTestSpec,
    OpcodeTarget,
)
from .blobs import BlobsTest, BlobsTestFiller, BlobsTestSpec
from .blockchain import (
    Block,
    BlockchainTest,
    BlockchainTestFiller,
    BlockchainTestSpec,
    Header,
)
from .pbt import PBTTest, PBTTestFiller
from .state import StateTest, StateTestFiller, StateTestSpec
from .static_state.state_static import StateStaticTest
from .transaction import (
    TransactionTest,
    TransactionTestFiller,
    TransactionTestSpec,
)

FillTestType: TypeAlias = Type[BaseTest] | Type[BaseDirectTest]


def get_fill_test_types() -> Tuple[FillTestType, ...]:
    """Return all test types supported by the fixture filler."""
    return (
        *BaseTest.spec_types.values(),
        *BaseDirectTest.spec_types.values(),
    )


__all__ = (
    "BaseStaticTest",
    "BaseDirectTest",
    "BaseTest",
    "FillTestType",
    "BenchmarkTest",
    "BenchmarkTestFiller",
    "BenchmarkTestSpec",
    "BlobsTest",
    "BlobsTestFiller",
    "BlobsTestSpec",
    "BlockchainTest",
    "BlockchainTestEngineFiller",
    "BlockchainTestEngineSpec",
    "BlockchainTestFiller",
    "BlockchainTestSpec",
    "Block",
    "Header",
    "OpcodeTarget",
    "PBTTest",
    "PBTTestFiller",
    "StateStaticTest",
    "StateTest",
    "StateTestFiller",
    "StateTestSpec",
    "TestSpec",
    "TransactionTest",
    "TransactionTestFiller",
    "TransactionTestSpec",
    "get_fill_test_types",
)
