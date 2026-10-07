// Package logging configures JSON logs for the Go sandbox services.
//
// Field names deliberately match the Java services' logstash layout
// (@timestamp, level, message, trace_id, span_id) so a single LogQL query such
// as `{service_name="payments"} | json | level="ERROR"` works across the fleet.
package logging

import (
	"context"
	"log/slog"
	"os"
	"strings"

	"go.opentelemetry.io/otel/trace"
)

// New returns a JSON logger tagged with the service name. The level comes from
// LOG_LEVEL (debug, info, warn, error); the default is info.
func New(service string) *slog.Logger {
	handler := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: levelFromEnv(),
		ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
			if len(groups) > 0 {
				return a
			}
			switch a.Key {
			case slog.TimeKey:
				a.Key = "@timestamp"
			case slog.MessageKey:
				a.Key = "message"
			}
			return a
		},
	})
	return slog.New(traceHandler{handler}).With("service", service)
}

func levelFromEnv() slog.Level {
	switch strings.ToLower(os.Getenv("LOG_LEVEL")) {
	case "debug":
		return slog.LevelDebug
	case "warn":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

// traceHandler stamps every record with the active span's IDs so a log line
// can be joined to its trace in Grafana.
type traceHandler struct{ slog.Handler }

func (h traceHandler) Handle(ctx context.Context, r slog.Record) error {
	if sc := trace.SpanContextFromContext(ctx); sc.IsValid() {
		r.AddAttrs(
			slog.String("trace_id", sc.TraceID().String()),
			slog.String("span_id", sc.SpanID().String()),
		)
	}
	return h.Handler.Handle(ctx, r)
}

func (h traceHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return traceHandler{h.Handler.WithAttrs(attrs)}
}

func (h traceHandler) WithGroup(name string) slog.Handler {
	return traceHandler{h.Handler.WithGroup(name)}
}
