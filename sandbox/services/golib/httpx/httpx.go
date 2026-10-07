// Package httpx holds the HTTP plumbing shared by the Go sandbox services:
// RED metrics with the same names and labels Micrometer produces in the Java
// services, OpenTelemetry spans, health endpoints and graceful shutdown.
package httpx

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
)

// Buckets matches the latency SLO boundaries used in the alert rules.
var Buckets = []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10}

var (
	serverRequests = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "http_server_requests_seconds",
		Help:    "HTTP server request duration, shaped like Micrometer's http.server.requests.",
		Buckets: Buckets,
	}, []string{"method", "uri", "status", "outcome"})

	clientRequests = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "http_client_requests_seconds",
		Help:    "Outbound HTTP request duration, shaped like Micrometer's http.client.requests.",
		Buckets: Buckets,
	}, []string{"client_name", "method", "uri", "status", "outcome"})
)

// SlowRequest is the threshold above which a request is logged at WARN.
const SlowRequest = time.Second

// Outcome maps a status code to Micrometer's outcome tag values.
func Outcome(status int) string {
	switch {
	case status < 200:
		return "INFORMATIONAL"
	case status < 300:
		return "SUCCESS"
	case status < 400:
		return "REDIRECTION"
	case status < 500:
		return "CLIENT_ERROR"
	default:
		return "SERVER_ERROR"
	}
}

// RouteTemplate turns a ServeMux pattern ("POST /orders/{id}") into the uri
// label ("/orders/{id}"), keeping metric cardinality bounded.
func RouteTemplate(pattern string) string {
	if pattern == "" {
		return "NOT_FOUND"
	}
	if i := strings.IndexByte(pattern, ' '); i >= 0 {
		pattern = pattern[i+1:]
	}
	return pattern
}

// ObserveClient records one outbound call in http_client_requests_seconds.
func ObserveClient(client, method, uri string, status int, elapsed time.Duration) {
	statusLabel, outcome := "CLIENT_ERROR", "UNKNOWN" // Micrometer's labels for I/O errors
	if status > 0 {
		statusLabel, outcome = strconv.Itoa(status), Outcome(status)
	}
	clientRequests.WithLabelValues(client, method, uri, statusLabel, outcome).Observe(elapsed.Seconds())
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

// Unwrap lets http.ResponseController reach the underlying writer.
func (r *statusRecorder) Unwrap() http.ResponseWriter { return r.ResponseWriter }

// Handler wraps an application mux with tracing, RED metrics and slow-request
// logging. /metrics, /healthz and /readyz are mounted outside the instrumented
// path so scrapes and probes don't pollute the RED metrics.
func Handler(service string, logger *slog.Logger, app *http.ServeMux, ready func() bool) http.Handler {
	instrumented := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		_, pattern := app.Handler(r)
		uri := RouteTemplate(pattern)

		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		app.ServeHTTP(rec, r)

		elapsed := time.Since(start)
		serverRequests.WithLabelValues(r.Method, uri, strconv.Itoa(rec.status), Outcome(rec.status)).Observe(elapsed.Seconds())
		if elapsed > SlowRequest {
			logger.WarnContext(r.Context(), "slow request",
				"method", r.Method, "uri", uri, "status", rec.status, "duration_ms", elapsed.Milliseconds())
		}
	})

	root := http.NewServeMux()
	root.Handle("GET /metrics", promhttp.Handler())
	root.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	root.HandleFunc("GET /readyz", func(w http.ResponseWriter, _ *http.Request) {
		if ready != nil && !ready() {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	root.Handle("/", otelhttp.NewHandler(instrumented, service,
		otelhttp.WithSpanNameFormatter(func(_ string, r *http.Request) string {
			if _, pattern := app.Handler(r); pattern != "" {
				return pattern
			}
			return r.Method
		}),
	))
	return root
}

// Serve runs the server until ctx is cancelled, then drains in-flight requests.
func Serve(ctx context.Context, addr string, handler http.Handler, logger *slog.Logger) error {
	srv := &http.Server{
		Addr:              addr,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
	}
	errs := make(chan error, 1)
	go func() { errs <- srv.ListenAndServe() }()
	logger.Info("http server started", "addr", addr)

	select {
	case err := <-errs:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	case <-ctx.Done():
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	logger.Info("http server shutting down")
	return srv.Shutdown(shutdownCtx)
}

// ClientTransport wraps a RoundTripper with client spans (trace-context
// propagation) and http_client_requests_seconds metrics.
func ClientTransport(clientName string, base http.RoundTripper) http.RoundTripper {
	if base == nil {
		base = http.DefaultTransport
	}
	return otelhttp.NewTransport(&metricsTransport{client: clientName, base: base})
}

type metricsTransport struct {
	client string
	base   http.RoundTripper
}

func (t *metricsTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	start := time.Now()
	resp, err := t.base.RoundTrip(r)
	status := 0
	if err == nil {
		status = resp.StatusCode
	}
	ObserveClient(t.client, r.Method, normalizePath(r.URL.Path), status, time.Since(start))
	return resp, err
}

var idSegment = regexp.MustCompile(`/(?:[0-9]+|[0-9a-fA-F-]{32,36}|SKU-[0-9]+)(/|$)`)

// normalizePath collapses IDs in outbound paths ("/orders/42" -> "/orders/{id}").
func normalizePath(p string) string {
	return idSegment.ReplaceAllString(p, "/{id}$1")
}

// WriteJSON writes v as a JSON response with the given status.
func WriteJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
