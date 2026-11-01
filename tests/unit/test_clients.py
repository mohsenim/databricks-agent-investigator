from incident_agent.clients import convert_value, rows_from_response, sql_type


def test_convert_value_types():
    assert convert_value("5", "LONG") == 5
    assert convert_value("1.5", "DOUBLE") == 1.5
    assert convert_value("true", "BOOLEAN") is True
    assert convert_value('{"numOutputRows":"0"}', "MAP") == {"numOutputRows": "0"}
    assert convert_value(None, "INT") is None
    assert convert_value("x", "STRING") == "x"


def test_rows_from_response():
    rows = rows_from_response([("id", "INT"), ("m", "MAP")], [["1", '{"a":"b"}']])
    assert rows == [{"id": 1, "m": {"a": "b"}}]


def test_sql_type_uses_bigint_for_job_ids():
    assert sql_type(5) == "INT"
    assert sql_type(1_234_567_890_123) == "BIGINT"
    assert sql_type("x") == "STRING" and sql_type(True) == "BOOLEAN"
