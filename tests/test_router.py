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


def test_create_intern_keyword_detected(router):
    result = router.route("create intern for dev ops")
    assert result.is_hire is True


def test_new_intern_keyword_detected(router):
    result = router.route("new intern who writes LinkedIn posts")
    assert result.is_hire is True


# ---------------------------------------------------------------------------
# Management commands
# ---------------------------------------------------------------------------

def test_list_interns_not_routed_to_intern(router):
    """'list interns' should go to orchestrator, not to intern named 'list'."""
    result = router.route("list interns")
    assert result.intern_name is None
    assert result.is_hire is False


def test_team_command(router):
    result = router.route("team")
    assert result.intern_name is None


def test_show_command_passthrough(router):
    result = router.route("show alex's jd")
    assert result.intern_name is None
    assert "show" in result.message.lower()


def test_fire_command_passthrough(router):
    result = router.route("fire alex")
    assert result.intern_name is None
    assert "fire" in result.message.lower()


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

# ---------------------------------------------------------------------------
# Team formation
# ---------------------------------------------------------------------------

def test_form_team_basic():
    router = MessageRouter(["alex", "sarah"])
    result = router.route("form team alex, sarah to review the marketing plan")
    assert result.is_team is True
    assert set(result.team_names) == {"alex", "sarah"}
    assert "review" in result.message


def test_form_team_with_and():
    router = MessageRouter(["alex", "bob"])
    result = router.route("form team alex and bob to do research")
    assert result.is_team is True
    assert set(result.team_names) == {"alex", "bob"}


def test_form_team_unknown_intern():
    router = MessageRouter(["alex"])
    result = router.route("form team alex, unknown to do stuff")
    assert result.is_team is False  # needs at least 2 known names


def test_form_team_needs_two_members():
    router = MessageRouter(["alex"])
    result = router.route("form team alex to do stuff")
    assert result.is_team is False


def test_form_team_three_members():
    router = MessageRouter(["alex", "sarah", "bob"])
    result = router.route("form team alex, sarah, bob to write a report")
    assert result.is_team is True
    assert len(result.team_names) == 3
    assert "write a report" in result.message


def test_form_team_with_mention_prefix():
    router = MessageRouter(["alex", "sarah"])
    result = router.route("<@U123ABC> form team alex, sarah to review code")
    assert result.is_team is True
    assert set(result.team_names) == {"alex", "sarah"}


# ---------------------------------------------------------------------------
# update_names
# ---------------------------------------------------------------------------

def test_update_names_adds_new_intern(router):
    result = router.route("sam do something")
    assert result.intern_name is None

    router.update_names(["alex", "mia", "dev", "sam"])
    result = router.route("sam do something")
    assert result.intern_name == "sam"


# ---------------------------------------------------------------------------
# Help command
# ---------------------------------------------------------------------------

def test_help_routes_to_management(router):
    result = router.route("help")
    assert result.intern_name is None
    assert result.message == "help"


def test_help_with_target_routes_to_management(router):
    result = router.route("help alex")
    assert result.intern_name is None
    assert result.message == "help alex"


# ---------------------------------------------------------------------------
# Edit command
# ---------------------------------------------------------------------------

def test_edit_command_routes_to_management(router):
    result = router.route("edit alex's jd")
    assert result.intern_name is None
    assert "edit" in result.message.lower()


def test_edit_command_plain_name(router):
    result = router.route("edit alex")
    assert result.intern_name is None
    assert "edit" in result.message.lower()


# ---------------------------------------------------------------------------
# History command
# ---------------------------------------------------------------------------

def test_history_command_routes_to_management(router):
    result = router.route("history")
    assert result.intern_name is None
    assert result.message == "history"
