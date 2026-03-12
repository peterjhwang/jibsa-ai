"""Tests for MessageRouter — message parsing and intern routing."""
import pytest

from src.router import MessageRouter, RouteResult


@pytest.fixture
def router():
    return MessageRouter(known_names=["alex", "mia", "dev"])


# ---------------------------------------------------------------------------
# Basic routing to Jibsa (no intern match)
# ---------------------------------------------------------------------------

def test_plain_message_routes_to_jibsa(router):
    result = router.route("what tasks do I have?")
    assert result.intern_name is None
    assert result.message == "what tasks do I have?"
    assert result.is_hire is False


def test_unknown_name_routes_to_jibsa(router):
    result = router.route("bob write a report")
    assert result.intern_name is None


# ---------------------------------------------------------------------------
# Routing to interns
# ---------------------------------------------------------------------------

def test_name_prefix_routes_to_intern(router):
    result = router.route("alex write 3 LinkedIn posts")
    assert result.intern_name == "alex"
    assert result.message == "write 3 LinkedIn posts"


def test_name_with_comma_routes_to_intern(router):
    result = router.route("mia, research competitors in CRM space")
    assert result.intern_name == "mia"
    assert result.message == "research competitors in CRM space"


def test_ask_pattern_routes_to_intern(router):
    result = router.route("ask alex to write a blog post")
    assert result.intern_name == "alex"
    assert result.message == "write a blog post"


def test_routing_is_case_insensitive(router):
    result = router.route("Alex write something")
    assert result.intern_name == "alex"


def test_ask_pattern_case_insensitive(router):
    result = router.route("Ask MIA to do research")
    assert result.intern_name == "mia"
    assert result.message == "do research"


# ---------------------------------------------------------------------------
# Hire flow detection
# ---------------------------------------------------------------------------

def test_hire_keyword_detected(router):
    result = router.route("hire a content marketing intern")
    assert result.is_hire is True
    assert result.intern_name is None
    assert "hire" in result.message.lower()


def test_create_intern_keyword_detected(router):
    result = router.route("create intern for dev ops")
    assert result.is_hire is True


def test_new_intern_keyword_detected(router):
    result = router.route("new intern who writes LinkedIn posts")
    assert result.is_hire is True


# ---------------------------------------------------------------------------
# Slack mention stripping
# ---------------------------------------------------------------------------

def test_strips_slack_mention_prefix(router):
    result = router.route("<@U123ABC> alex do something")
    assert result.intern_name == "alex"
    assert result.message == "do something"


def test_strips_mention_for_jibsa_message(router):
    result = router.route("<@U123ABC> what's my schedule?")
    assert result.intern_name is None
    assert result.message == "what's my schedule?"


# ---------------------------------------------------------------------------
# update_names
# ---------------------------------------------------------------------------

def test_update_names_adds_new_intern(router):
    result = router.route("sam do something")
    assert result.intern_name is None  # sam not known yet

    router.update_names(["alex", "mia", "dev", "sam"])
    result = router.route("sam do something")
    assert result.intern_name == "sam"
