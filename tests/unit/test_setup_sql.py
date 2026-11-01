import pytest

from incident_agent.setup_sql import render


def test_render_splits_and_substitutes():
    s = render("create_objects.sql", {"catalog": "c", "schema": "s"})
    assert any("c.s.incidents" in x for x in s) and all("${" not in x for x in s)
    assert any(x.startswith("CREATE VOLUME") for x in s)


def test_missing_placeholder_fails():
    with pytest.raises(ValueError, match="investigator"):
        render("grants.sql", {"catalog": "c", "schema": "s", "app": "a"})


def test_unsafe_values_rejected():
    with pytest.raises(ValueError, match="unsafe"):
        render("create_objects.sql", {"catalog": "c; DROP", "schema": "s"})


def test_grants_never_include_write_on_monitored_data():
    s = " ".join(render("grants.sql", {"catalog": "c", "schema": "s", "investigator": "i", "app": "a"}))
    assert "MODIFY ON SCHEMA c.raw" not in s and "ALL PRIVILEGES" not in s
