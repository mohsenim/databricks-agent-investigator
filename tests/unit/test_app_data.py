import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "app"))
from data import Data, sanitize_markdown


class Sql:
    def __init__(self, rows=None):
        self.calls, self.rows = [], rows or []

    def run(self, statement, params=None):
        self.calls.append((statement, params or {}))
        return self.rows


def test_filters_are_whitelisted_and_bound():
    sql = Sql()
    Data(sql, "c", "s").incidents(job="x'; DROP", classes=["permission_change", "evil"],
                                  confidence=["HIGH"], status=["bogus"])
    statement, params = sql.calls[0]
    assert "DROP" not in statement and "evil" not in statement and "bogus" not in statement
    assert params["root_cause_class_0"] == "permission_change"
    assert "root_cause_class_1" not in params and "status_0" not in params
    assert params["job"] == "%x'; drop%"


def test_mix_rejects_unknown_columns():
    with pytest.raises(ValueError):
        Data(Sql(), "c", "s").mix("job_name; DROP TABLE x")


def test_set_setting_rejects_unknown_keys():
    with pytest.raises(ValueError):
        Data(Sql(), "c", "s").set_setting("anything", "1")


def test_is_monitored():
    assert Data(Sql([{"x": 1}]), "c", "s").is_monitored(5)
    assert not Data(Sql([]), "c", "s").is_monitored(5)


def test_sanitize_markdown_strips_html_and_links():
    out = sanitize_markdown('<script>alert(1)</script>See [docs](http://evil.example) ![x](http://i)')
    assert "<" not in out and "evil.example" not in out and "http" not in out
    assert "docs" in out


def test_cron_helpers_round_trip():
    from data import cron_for, minutes_from

    for minutes in (5, 10, 15, 30, 60):
        assert minutes_from(cron_for(minutes)) == minutes
    assert minutes_from("0 15 3 * * ?") is None


def test_model_endpoint_is_a_saved_setting():
    sql = Sql()
    Data(sql, "c", "s").set_setting("model_endpoint", "my-endpoint")
    assert sql.calls[0][1] == {"k": "model_endpoint", "v": "my-endpoint"}
