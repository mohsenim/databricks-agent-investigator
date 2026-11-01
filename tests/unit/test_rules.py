import pytest

from incident_agent.rules import classify_error, mentioned_columns
from incident_agent.taxonomy import FailureClass as C


@pytest.mark.parametrize("text,expected", [
    ("[UNRESOLVED_COLUMN.WITH_SUGGESTION] A column with name `amount` cannot be resolved", C.SCHEMA_CHANGE),
    ("AnalysisException: cannot resolve 'amount' given input columns: [id]", C.SCHEMA_CHANGE),
    ("[CAST_INVALID_INPUT] The value 'abc' cannot be cast to DECIMAL", C.SCHEMA_CHANGE),
    ("[INSUFFICIENT_PERMISSIONS] User does not have SELECT on table", C.PERMISSION),
    ("java.lang.OutOfMemoryError: Java heap space", C.RESOURCE_LIMIT),
    ("ERROR: No matching distribution found for nopkg==9.9.9", C.LIBRARY),
    ("NameError: name 'dfx' is not defined", C.CODE_OR_CONFIG),
])
def test_classify(text, expected):
    assert expected in [c for c, _ in classify_error(text)]


def test_clean_text_matches_nothing():
    assert classify_error("Task completed normally") == []


def test_mentioned_columns():
    assert mentioned_columns("A column with name `amount` cannot be resolved") == {"amount"}
    assert "amount" in mentioned_columns("UNRESOLVED_COLUMN `amount`")
    assert "amount" in mentioned_columns("cannot resolve 'o.amount' given input columns")
