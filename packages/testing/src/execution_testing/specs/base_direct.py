"""Base class for fork-aware fixtures generated without EVM execution."""

from abc import abstractmethod
from functools import reduce
from typing import Any, ClassVar, Dict, List, Sequence, Type

import pytest
from pydantic import BaseModel, ConfigDict

from execution_testing.fixtures import (
    BaseFixture,
    FixtureFormat,
    LabeledFixtureFormat,
)
from execution_testing.forks import Fork, TransitionFork
from execution_testing.forks.base_fork import BaseFork


class BaseDirectTest(BaseModel):
    """A fork-aware test that generates a fixture without using ``t8n``."""

    model_config = ConfigDict(extra="forbid")

    fork: Fork | TransitionFork = BaseFork

    spec_types: ClassVar[Dict[str, Type["BaseDirectTest"]]] = {}
    supported_fixture_formats: ClassVar[
        Sequence[FixtureFormat | LabeledFixtureFormat]
    ] = []
    supported_markers: ClassVar[Dict[str, str]] = {}

    def model_post_init(self, __context: Any, /) -> None:
        """Require the filler to provide a concrete protocol fork."""
        super().model_post_init(__context)
        assert self.fork != BaseFork, "Fork was not provided by the filler."

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Register concrete direct test types for the fill command."""
        super().__pydantic_init_subclass__(**kwargs)
        if getattr(cls, "__is_base_test_wrapper__", False):
            return
        if cls.pytest_parameter_name():
            BaseDirectTest.spec_types[cls.pytest_parameter_name()] = cls

    @classmethod
    def pytest_parameter_name(cls) -> str:
        """Return the pytest fixture name used to select this test type."""
        if cls == BaseDirectTest:
            return ""
        return reduce(
            lambda x, y: x + ("_" if y.isupper() else "") + y,
            cls.__name__,
        ).lower()

    @classmethod
    def discard_fixture_format_by_marks(
        cls,
        fixture_format: FixtureFormat,
        markers: List[pytest.Mark],
    ) -> bool:
        """Return whether markers exclude a fixture format."""
        del fixture_format, markers
        return False

    @abstractmethod
    def generate(self, *, fixture_format: FixtureFormat) -> BaseFixture:
        """Generate a fixture directly from the test inputs and fork."""
        pass
