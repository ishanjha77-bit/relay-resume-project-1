package main

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"
)

func testConfig(t *testing.T, orders, inventory string, timeout time.Duration) config {
	t.Helper()
	o, _ := url.Parse(orders)
	i, _ := url.Parse(inventory)
	return config{ordersURL: o, inventoryURL: i, upstreamTimeout: timeout}
}

func TestRoutesRewriteToUpstreamPaths(t *testing.T) {
	var gotPath, gotQuery string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath, gotQuery = r.URL.Path, r.URL.RawQuery
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	router := newRouter(testConfig(t, upstream.URL, upstream.URL, time.Second), slog.New(slog.DiscardHandler))

	cases := []struct{ method, in, wantPath string }{
		{"GET", "/api/products", "/inventory/products"},
		{"GET", "/api/products/SKU-0042", "/inventory/products/SKU-0042"},
		{"POST", "/api/orders", "/orders"},
		{"GET", "/api/orders/7f1c", "/orders/7f1c"},
	}
	for _, tc := range cases {
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, httptest.NewRequest(tc.method, tc.in+"?limit=5", nil))
		if rec.Code != http.StatusOK {
			t.Fatalf("%s %s: status %d", tc.method, tc.in, rec.Code)
		}
		if gotPath != tc.wantPath || gotQuery != "limit=5" {
			t.Errorf("%s %s: upstream got %q?%s, want %q?limit=5", tc.method, tc.in, gotPath, gotQuery, tc.wantPath)
		}
	}
}

func TestSlowUpstreamReturns504(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-time.After(500 * time.Millisecond):
		case <-r.Context().Done():
		}
	}))
	defer upstream.Close()

	router := newRouter(testConfig(t, upstream.URL, upstream.URL, 50*time.Millisecond), slog.New(slog.DiscardHandler))
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest("POST", "/api/orders", nil))

	if rec.Code != http.StatusGatewayTimeout {
		body, _ := io.ReadAll(rec.Body)
		t.Fatalf("status = %d, want 504 (body %s)", rec.Code, body)
	}
}

func TestDeadUpstreamReturns502(t *testing.T) {
	upstream := httptest.NewServer(http.NotFoundHandler())
	deadURL := upstream.URL
	upstream.Close()

	router := newRouter(testConfig(t, deadURL, deadURL, time.Second), slog.New(slog.DiscardHandler))
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest("GET", "/api/products", nil))

	if rec.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502", rec.Code)
	}
}
