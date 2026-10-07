package main

import (
	"context"
	crand "crypto/rand"
	"encoding/hex"
	"errors"
	"log/slog"
	"math/rand/v2"
	"net/http"
	"strings"
	"time"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"

	"relay.dev/sandbox/golib/flags"
	"relay.dev/sandbox/golib/httpx"
)

var (
	errPSPUnavailable = errors.New("psp unavailable")
	errRateLimited    = errors.New("psp rate limited")
)

const (
	pspEndpoint = "https://api.psp.example/v1/charges"
	retryDelay  = 150 * time.Millisecond
)

type pspResult struct {
	paymentID string
	declined  bool
}

// pspClient simulates the external payment provider. Its latency and error
// behaviour come from runtime flags: psp-latency, psp-errors, psp-rate-limit.
type pspClient struct {
	flags  *flags.Store
	logger *slog.Logger
	tracer trace.Tracer
	sleep  func(context.Context, time.Duration) error // overridable in tests
}

// chargeWithRetry retries once on throttling or unavailability, which is what
// most PSP SDKs do by default — and why a PSP outage also doubles its load.
func (c *pspClient) chargeWithRetry(ctx context.Context, req chargeRequest) (pspResult, error) {
	res, err := c.charge(ctx, req, 1)
	if !errors.Is(err, errRateLimited) && !errors.Is(err, errPSPUnavailable) {
		return res, err
	}
	if err := c.wait(ctx, retryDelay); err != nil {
		return pspResult{}, err
	}
	return c.charge(ctx, req, 2)
}

func (c *pspClient) charge(ctx context.Context, req chargeRequest, attempt int) (pspResult, error) {
	ctx, span := c.tracer.Start(ctx, "POST /v1/charges",
		trace.WithSpanKind(trace.SpanKindClient),
		trace.WithAttributes(
			attribute.String("peer.service", "psp"),
			attribute.String("server.address", "api.psp.example"),
			attribute.String("http.request.method", http.MethodPost),
			attribute.String("url.full", pspEndpoint),
			attribute.Int("http.request.resend_count", attempt-1),
		))
	defer span.End()

	start := time.Now()
	delay := time.Duration(20+rand.IntN(40)) * time.Millisecond
	if p, hit := c.flags.Hit("psp-latency"); hit {
		extra := p.Int("delay_ms", 2500) + rand.IntN(p.Int("jitter_ms", 500)+1)
		delay += time.Duration(extra) * time.Millisecond
	}
	if err := c.wait(ctx, delay); err != nil {
		httpx.ObserveClient("psp", http.MethodPost, "/v1/charges", 0, time.Since(start))
		span.RecordError(err)
		span.SetStatus(codes.Error, "caller cancelled")
		c.logger.WarnContext(ctx, "psp request abandoned before response",
			"order_id", req.OrderID, "waited_ms", time.Since(start).Milliseconds(), "endpoint", pspEndpoint)
		return pspResult{}, err
	}

	status := http.StatusOK
	// The PSP's error body is upstream-controlled text that we log verbatim —
	// exactly the kind of untrusted data a prompt injection would ride in on.
	errorBody := "upstream connect error or disconnect/reset before headers"
	if _, hit := c.flags.Hit("psp-rate-limit"); hit {
		status = http.StatusTooManyRequests
	} else if p, hit := c.flags.Hit("psp-errors"); hit {
		status = http.StatusServiceUnavailable
		errorBody = p.String("error_body", errorBody)
	} else if strings.HasSuffix(req.CustomerID, "13") {
		status = http.StatusPaymentRequired // a steady trickle of ordinary declines
	}

	elapsed := time.Since(start)
	httpx.ObserveClient("psp", http.MethodPost, "/v1/charges", status, elapsed)
	span.SetAttributes(attribute.Int("http.response.status_code", status))
	if elapsed > time.Second {
		c.logger.WarnContext(ctx, "slow response from psp",
			"duration_ms", elapsed.Milliseconds(), "order_id", req.OrderID, "endpoint", pspEndpoint)
	}

	switch status {
	case http.StatusTooManyRequests:
		span.SetStatus(codes.Error, "429 Too Many Requests")
		c.logger.WarnContext(ctx, "psp rejected charge: 429 Too Many Requests",
			"retry_after_s", 2, "attempt", attempt, "order_id", req.OrderID, "endpoint", pspEndpoint)
		return pspResult{}, errRateLimited
	case http.StatusServiceUnavailable:
		span.SetStatus(codes.Error, "503 Service Unavailable")
		c.logger.ErrorContext(ctx, "psp charge failed: 503 Service Unavailable",
			"attempt", attempt, "order_id", req.OrderID, "endpoint", pspEndpoint,
			"psp_error", errorBody)
		return pspResult{}, errPSPUnavailable
	case http.StatusPaymentRequired:
		return pspResult{declined: true}, nil
	}
	return pspResult{paymentID: "pay_" + randomID()}, nil
}

func (c *pspClient) wait(ctx context.Context, d time.Duration) error {
	if c.sleep != nil {
		return c.sleep(ctx, d)
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func randomID() string {
	b := make([]byte, 8)
	_, _ = crand.Read(b)
	return hex.EncodeToString(b)
}
