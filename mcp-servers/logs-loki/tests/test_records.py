import json
from datetime import UTC, datetime

from logs_loki.loki import LogEntry
from logs_loki.records import parse, trim_stack

LABELS = {
    "service_name": "orders",
    "k8s_pod_name": "orders-abc",
    "service_version": "1.4.0",
    "detected_level": "error",
}
TS = datetime(2026, 10, 4, 6, 10, tzinfo=UTC)


def test_parses_java_logstash_line() -> None:
    line = json.dumps(
        {
            "@timestamp": "2026-10-04T06:10:00Z",
            "@version": "1",
            "message": "POST /orders failed: unhandled exception",
            "logger_name": "dev.relay.sandbox.orders.api.ApiErrorHandler",
            "thread_name": "http-nio-8080-exec-1",
            "level": "ERROR",
            "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
            "stack_trace": "java.lang.NullPointerException: boom\n\tat a.B.c(B.java:21)",
        }
    )
    record = parse(LogEntry(TS, line, LABELS)).to_dict()
    assert record["level"] == "ERROR"
    assert record["message"] == "POST /orders failed: unhandled exception"
    assert record["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert record["stack"].startswith("java.lang.NullPointerException")
    assert record["version"] == "1.4.0"
    assert "thread_name" not in record.get("attrs", {})


def test_go_attributes_are_kept_and_redacted() -> None:
    line = json.dumps(
        {
            "@timestamp": "2026-10-04T06:10:00Z",
            "level": "ERROR",
            "message": "psp charge failed",
            "attempt": 2,
            "endpoint": "https://api.psp.example/v1/charges",
            "psp_error": "token=abcdef123456 rejected",
        }
    )
    record = parse(LogEntry(TS, line, LABELS)).to_dict()
    assert record["attrs"]["attempt"] == "2"
    assert "abcdef123456" not in record["attrs"]["psp_error"]


def test_plain_text_lines_keep_detected_level() -> None:
    entry = LogEntry(
        TS,
        "2026-10-04 06:10:00 UTC [61] LOG:  duration: 812.3 ms  statement: SELECT 1",
        {"service_name": "postgres", "detected_level": "info", "k8s_pod_name": "postgres-0"},
    )
    record = parse(entry).to_dict()
    assert record["service"] == "postgres"
    assert record["level"] == "INFO"
    assert "duration: 812.3 ms" in record["message"]


def test_trim_stack_keeps_every_cause() -> None:
    frames = "\n".join(f"\tat x.Y.m{i}(Y.java:{i})" for i in range(20))
    stack = (
        f"org.springframework.jdbc.CannotGetJdbcConnectionException: no conn\n{frames}\n"
        f"Caused by: java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available\n{frames}"
    )
    trimmed = trim_stack(stack)
    assert "Caused by: java.sql.SQLTransientConnectionException" in trimmed
    assert trimmed.count("\tat") == 0  # frames are stripped of indentation
    assert "more frames" in trimmed
