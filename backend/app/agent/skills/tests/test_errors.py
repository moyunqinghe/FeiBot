from skill_importer import ERROR_SOURCE_INVALID, SkillImporterError


def test_error_carries_code_cause_and_message() -> None:
    cause = ValueError("boom")
    err = SkillImporterError(ERROR_SOURCE_INVALID, "bad source", cause=cause)
    assert err.code == ERROR_SOURCE_INVALID
    assert err.message == "bad source"
    assert err.cause is cause
    assert str(err) == "bad source"
