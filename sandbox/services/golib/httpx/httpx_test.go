package httpx

import (
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
)

func TestRouteTemplate(t *testing.T) {
	cases := map[string]string{
		"POST /payments/charge":   "/payments/charge",
		"GET /api/products/{sku}": "/api/products/{sku}",
		"/plain":                  "/plain",
		"":                        "NOT_FOUND",
	}
	for in, want := range cases {
		if got := RouteTemplate(in); got != want {
			t.Errorf("RouteTemplate(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestOutcomeMatchesMicrometer(t *testing.T) {
	cases := map[int]string{101: "INFORMATIONAL", 200: "SUCCESS", 302: "REDIRECTION", 404: "CLIENT_ERROR", 503: "SERVER_ERROR"}
	for status, want := range cases {
		if got := Outcome(status); got != want {
			t.Errorf("Outcome(%d) = %q, want %q", status, got, want)
		}
	}
}

func TestNormalizePathCollapsesIDs(t *testing.T) {
	cases := map[string]string{
		"/orders/42": "/orders/{id}",
		"/orders/3f0c9a6e-8b1d-4c55-9a77-0d2f1e6b8c10": "/orders/{id}",
		"/inventory/products/SKU-0042":                 "/inventory/products/{id}",
		"/inventory/products":                          "/inventory/products",
	}
	for in, want := range cases {
		if got := normalizePath(in); got != want {
			t.Errorf("normalizePath(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestHandlerRecordsRouteTemplateNotRawPath(t *testing.T) {
	app := http.NewServeMux()
	app.HandleFunc("GET /things/{id}", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusTeapot)
	})
	h := Handler("test", slog.New(slog.DiscardHandler), app, nil)

	for _, id := range []string{"1", "2", "3"} {
		h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/things/"+id, nil))
	}

	families, err := prometheus.DefaultGatherer.Gather()
	if err != nil {
		t.Fatal(err)
	}
	var samples uint64
	for _, mf := range families {
		if mf.GetName() != "http_server_requests_seconds" {
			continue
		}
		for _, m := range mf.GetMetric() {
			labels := map[string]string{}
			for _, lp := range m.GetLabel() {
				labels[lp.GetName()] = lp.GetValue()
			}
			if strings.HasPrefix(labels["uri"], "/things/") && labels["uri"] != "/things/{id}" {
				t.Fatalf("raw path leaked into uri label: %q", labels["uri"])
			}
			if labels["uri"] == "/things/{id}" && labels["status"] == "418" && labels["outcome"] == "CLIENT_ERROR" {
				samples = m.GetHistogram().GetSampleCount()
			}
		}
	}
	if samples != 3 {
		t.Fatalf("sample count for /things/{id} = %d, want 3", samples)
	}
}

func TestProbesAreNotInstrumented(t *testing.T) {
	app := http.NewServeMux()
	h := Handler("test", slog.New(slog.DiscardHandler), app, func() bool { return false })

	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/readyz", nil))
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("/readyz = %d, want 503 when not ready", rec.Code)
	}

	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/metrics", nil))
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "go_goroutines") {
		t.Fatalf("/metrics did not serve Prometheus metrics (status %d)", rec.Code)
	}
}
