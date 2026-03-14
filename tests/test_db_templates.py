"""Tests for database template validity."""
import pytest

from src.integrations.notion_db_templates import DB_TEMPLATES


def test_all_templates_have_properties():
    for name, template in DB_TEMPLATES.items():
        assert "properties" in template, f"Template '{name}' missing 'properties'"


def test_all_templates_have_title_property():
    for name, template in DB_TEMPLATES.items():
        props = template["properties"]
        has_title = any(
            "title" in v for v in props.values()
        )
        assert has_title, f"Template '{name}' has no title property"


def test_all_templates_have_keywords():
    for name, template in DB_TEMPLATES.items():
        assert "keywords" in template, f"Template '{name}' missing 'keywords'"
        assert len(template["keywords"]) > 0, f"Template '{name}' has empty keywords"


def test_known_templates_exist():
    expected = ["Tasks", "Projects", "Notes", "Interns", "Journal Entries",
                "Expense Record", "Workouts", "Contacts"]
    for name in expected:
        assert name in DB_TEMPLATES, f"Expected template '{name}' not found"


def test_tasks_template_has_status():
    props = DB_TEMPLATES["Tasks"]["properties"]
    assert "Status" in props
    assert "status" in props["Status"]


def test_interns_template_has_required_fields():
    props = DB_TEMPLATES["Interns"]["properties"]
    expected_fields = ["Name", "Role", "Responsibilities", "Tone",
                       "Tools Allowed", "Autonomy Rules", "Active"]
    for field in expected_fields:
        assert field in props, f"Interns template missing '{field}'"
