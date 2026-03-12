"""Tests for LLMRunner — LangChain-based LLM runner."""
from unittest.mock import MagicMock, patch
import pytest

from src.llm_runner import LLMRunner, _extract_json, _create_chat_model


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

def test_extract_json_bare():
    result = _extract_json('{"type": "action_plan", "summary": "test"}')
    assert result["type"] == "action_plan"


def test_extract_json_with_code_fence():
    text = '```json\n{"type": "action_plan", "summary": "test"}\n```'
    result = _extract_json(text)
    assert result["type"] == "action_plan"


def test_extract_json_returns_none_for_plain_text():
    result = _extract_json("Hello, how are you?")
    assert result is None


def test_extract_json_returns_none_for_invalid_json():
    result = _extract_json("{invalid json}")
    assert result is None


# ---------------------------------------------------------------------------
# _create_chat_model
# ---------------------------------------------------------------------------

def test_create_chat_model_unknown_provider():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        _create_chat_model({"provider": "skynet"})


@patch("src.llm_runner._create_chat_model")
def test_llm_runner_init(mock_create):
    mock_create.return_value = MagicMock()
    config = {
        "jibsa": {"claude_timeout": 60, "timezone": "UTC"},
        "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
    }
    runner = LLMRunner(config)
    assert runner._timeout == 60


# ---------------------------------------------------------------------------
# LLMRunner.run()
# ---------------------------------------------------------------------------

@patch("src.llm_runner._create_chat_model")
def test_run_returns_string_for_conversation(mock_create):
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Good morning! How can I help?"
    mock_llm.invoke.return_value = mock_response
    mock_create.return_value = mock_llm

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = LLMRunner(config)
    result = runner.run("hello")
    assert isinstance(result, str)
    assert "Good morning" in result


@patch("src.llm_runner._create_chat_model")
def test_run_returns_dict_for_action_plan(mock_create):
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = '{"type": "action_plan", "summary": "Create task", "steps": [], "needs_approval": true}'
    mock_llm.invoke.return_value = mock_response
    mock_create.return_value = mock_llm

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = LLMRunner(config)
    result = runner.run("create a task")
    assert isinstance(result, dict)
    assert result["type"] == "action_plan"


@patch("src.llm_runner._create_chat_model")
def test_run_returns_error_on_llm_failure(mock_create):
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = Exception("API timeout")
    mock_create.return_value = mock_llm

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = LLMRunner(config)
    result = runner.run("hello")
    assert isinstance(result, str)
    assert "⚠️" in result


@patch("src.llm_runner._create_chat_model")
def test_run_with_custom_system_prompt(mock_create):
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "I am a custom intern"
    mock_llm.invoke.return_value = mock_response
    mock_create.return_value = mock_llm

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = LLMRunner(config, system_prompt_template="You are {name}, a custom bot.\n{history}")
    result = runner.run("hello")
    assert result == "I am a custom intern"


@patch("src.llm_runner._create_chat_model")
def test_run_with_extra_replacements(mock_create):
    """Verify extra_replacements are injected into the system prompt."""
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "ok"
    mock_llm.invoke.return_value = mock_response
    mock_create.return_value = mock_llm

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    template = "You are {intern_name}. Tools: {tools}.\n{history}"
    runner = LLMRunner(config, system_prompt_template=template)
    runner.run("test", extra_replacements={"{intern_name}": "Alex", "{tools}": "notion"})

    # Verify the system prompt was built with the replacements
    call_args = mock_llm.invoke.call_args[0][0]
    system_msg = call_args[0]
    assert "Alex" in system_msg.content
    assert "notion" in system_msg.content
