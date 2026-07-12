from app.core.config import Settings
from app.core.security import create_session_token, session_subject, verify_session_token


def test_signed_session_token_round_trip():
    settings = Settings(
        AUTH_USERNAME="owner",
        AUTH_PASSWORD="a-secure-test-password",
        AUTH_SECRET="a-test-secret-that-is-longer-than-thirty-two-characters",
    )
    token = create_session_token(settings)
    assert verify_session_token(token, settings)
    assert not verify_session_token(token + "tampered", settings)


def test_security_settings_fail_closed_for_weak_credentials():
    settings = Settings(AUTH_PASSWORD="short", AUTH_SECRET="also-short")
    try:
        settings.validate_security()
    except RuntimeError as exc:
        assert "AUTH_PASSWORD" in str(exc)
    else:
        raise AssertionError("weak credentials must not be accepted")


def test_demo_session_has_distinct_subject():
    settings = Settings(
        AUTH_USERNAME="owner",
        AUTH_PASSWORD="a-secure-test-password",
        AUTH_SECRET="a-test-secret-that-is-longer-than-thirty-two-characters",
        DEMO_USERNAME="demo",
        DEMO_PASSWORD="demo123456",
    )
    token = create_session_token(settings, "demo")
    assert session_subject(token, settings) == "demo"
