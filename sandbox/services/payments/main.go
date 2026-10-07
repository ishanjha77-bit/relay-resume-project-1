// Command payments charges orders through an external payment service
// provider (PSP). The PSP is simulated in-process but traced as a real client
// call (peer.service=psp), so it appears as a downstream dependency in traces
// and in the service graph.
package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"go.opentelemetry.io/otel"

	"relay.dev/sandbox/golib/flags"
	"relay.dev/sandbox/golib/httpx"
	"relay.dev/sandbox/golib/logging"
	"relay.dev/sandbox/golib/telemetry"
)

var version = "dev"

func main() {
	logger := logging.New("payments")
	if err := run(logger); err != nil {
		logger.Error("payments stopped", "error", err)
		os.Exit(1)
	}
}

func run(logger *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	shutdown, err := telemetry.Setup(ctx, "payments", version)
	if err != nil {
		return err
	}
	defer shutdown(context.Background())

	flagStore := flags.Start(ctx, os.Getenv("FLAGS_REDIS_ADDR"), "payments", logger)
	faults := newFaultRunner(flagStore)
	go faults.run(ctx)

	svc := &service{
		psp:    &pspClient{flags: flagStore, logger: logger, tracer: otel.Tracer("relay.dev/sandbox/payments")},
		faults: faults,
		logger: logger,
	}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /payments/charge", svc.charge)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	logger.Info("payments starting", "version", version)
	return httpx.Serve(ctx, ":"+port, httpx.Handler("payments", logger, mux, nil), logger)
}
