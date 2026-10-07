import anyio
import pytest
from relay_mcp_kit import StaticTokenVerifier, clip, normalize_message, redact


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("Authorization: Bearer abcdefghijklmnop", "abcdefghijklmnop"),
        ('{"password":"hunter2hunter2"}', "hunter2hunter2"),
        ("connecting to postgresql://orders:s3cretpw@postgres:5432/orders", "s3cretpw"),
        (
            "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
        ),
        ("key=AKIAABCDEFGHIJKLMNOP leaked", "AKIAABCDEFGHIJKLMNOP"),
        ("using sk-ant-api03-abcdefghijklmnopqrstuv", "abcdefghijklmnopqrstuv"),
    ],
)
def test_redact_masks_secrets(raw: str, leaked: str) -> None:
    assert leaked not in redact(raw)


def test_redact_keeps_ordinary_text() -> None:
    line = "POST /orders failed: payments POST /payments/charge timed out: no response within 2000 ms"
    assert redact(line) == line


def test_normalize_groups_similar_messages() -> None:
    a = normalize_message(
        "HikariPool-1 - Connection is not available, request timed out after 2000ms (total=10, active=10)"
    )
    b = normalize_message(
        "HikariPool-1 - Connection is not available, request timed out after 2001ms (total=10, active=9)"
    )
    assert a == b
    assert "<duration>" in a


def test_normalize_uses_first_line_only() -> None:
    assert normalize_message("boom\n\tat x.y.Z(Z.java:1)") == "boom"


def test_clip_reports_dropped_length() -> None:
    assert clip("abcdef", 4) == "abcd…[+2 chars]"
    assert clip("abc", 4) == "abc"


def test_static_verifier_accepts_only_the_secret() -> None:
    verifier = StaticTokenVerifier("s3cret", ["logs:read"], resource="http://x/mcp")

    async def check() -> None:
        ok = await verifier.verify_token("s3cret")
        assert ok is not None
        assert ok.scopes == ["logs:read"]
        assert ok.resource == "http://x/mcp"
        assert await verifier.verify_token("wrong") is None

    anyio.run(check)


def test_verifier_refuses_empty_secret() -> None:
    with pytest.raises(ValueError):
        StaticTokenVerifier("", ["logs:read"], resource="http://x/mcp")
