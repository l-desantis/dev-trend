"""Tests that all concrete adapters implement the v4 LLMAdapter interface."""
import inspect

import pytest

from app.llm.base import LLMAdapter
from app.llm.mock_adapter import MockLLMAdapter


def test_concrete_adapters_implement_v4_methods() -> None:
    required = ["extract_pain_point", "label_cluster", "model_name"]
    for name in required:
        assert hasattr(MockLLMAdapter, name), f"MockLLMAdapter missing {name}"


def test_abc_raises_on_missing_method() -> None:
    """A subclass that forgets to implement an abstract method cannot be instantiated."""
    with pytest.raises(TypeError):
        class BadAdapter(LLMAdapter):
            pass
        BadAdapter()


async def test_mock_adapter_model_name() -> None:
    adapter = MockLLMAdapter()
    assert adapter.model_name == "mock-llm-v1"
