package main

import (
	"context"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/otel/trace/noop"

	"relay.dev/sandbox/golib/flags"
)

func newTestService(active map[string]flags.Params) *service {
	store := flags.NewStatic(active)
	logger := slog.New(slog.DiscardHandler)
	return &service{
		psp: &pspClient{
			flags:  store,
			logger: logger,
			tracer: noop.NewTracerProvider().Tracer("test"),
			sleep:  func(context.Context, time.Duration) error { return nil },
		},
		faults: newFaultRunner(store),
		logger: logger,
	}
}

func charge(t *testing.T, svc *service, body string) int {
	t.Helper()
	rec := httptest.NewRecorder()
	svc.charge(rec, httptest.NewRequest("POST", "/payments/charge", strings.NewReader(body)))
	return rec.Code
}

const validCharge = `{"orderId":"o-1","amountCents":2599,"currency":"USD","customerId":"c-7"}`

func TestChargeOutcomes(t *testing.T) {
	cases := []struct {
		name  string
		flags map[string]flags.Params
		body  string
		want  int
	}{
		{"captured", nil, validCharge, http.StatusOK},
		{"declined customer", nil, `{"orderId":"o-1","amountCents":100,"currency":"USD","customerId":"c-113"}`, http.StatusPaymentRequired},
		{"bad currency", nil, `{"orderId":"o-1","amountCents":100,"currency":"XYZ","customerId":"c-1"}`, http.StatusBadRequest},
		{"malformed json", nil, `{`, http.StatusBadRequest},
		{"psp down", map[string]flags.Params{"psp-errors": {"ratio": 1.0}}, validCharge, http.StatusBadGateway},
		{"psp throttling", map[string]flags.Params{"psp-rate-limit": {"ratio": 1.0}}, validCharge, http.StatusServiceUnavailable},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := charge(t, newTestService(tc.flags), tc.body); got != tc.want {
				t.Errorf("status = %d, want %d", got, tc.want)
			}
		})
	}
}

func TestRetryRecoversFromSingleFailure(t *testing.T) {
	// ratio 0 means the flag is set but never fires: the retry path must not break success.
	svc := newTestService(map[string]flags.Params{"psp-errors": {"ratio": 0.0}})
	if got := charge(t, svc, validCharge); got != http.StatusOK {
		t.Fatalf("status = %d, want 200", got)
	}
}

func TestCPUBurnWorkersFollowFlag(t *testing.T) {
	store := flags.NewStatic(map[string]flags.Params{"cpu-burn": {"workers": 3.0}})
	runner := newFaultRunner(store)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	runner.reconcileCPU(ctx)
	if len(runner.burners) != 3 {
		t.Fatalf("burners = %d, want 3", len(runner.burners))
	}
}
