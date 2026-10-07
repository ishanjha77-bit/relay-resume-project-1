package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httputil"
	"net/url"
	"time"

	"relay.dev/sandbox/golib/httpx"
)

// newRouter maps the public API onto upstream services. Every route proxies
// with the same upstream timeout, so a misconfigured UPSTREAM_TIMEOUT_MS shows
// up as 504s on all of them at once.
func newRouter(cfg config, logger *slog.Logger) *http.ServeMux {
	transport := &http.Transport{
		MaxIdleConns:        200,
		MaxIdleConnsPerHost: 100,
		IdleConnTimeout:     90 * time.Second,
	}
	mux := http.NewServeMux()

	route := func(pattern, upstream string, base *url.URL, path func(*http.Request) string) {
		proxy := &httputil.ReverseProxy{
			Rewrite: func(pr *httputil.ProxyRequest) {
				pr.SetURL(base)
				pr.Out.URL.Path = path(pr.In)
				pr.Out.URL.RawPath = ""
				pr.SetXForwarded()
			},
			Transport: httpx.ClientTransport(upstream, transport),
			ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
				status, msg := http.StatusBadGateway, "upstream request failed"
				if errors.Is(err, context.DeadlineExceeded) {
					status, msg = http.StatusGatewayTimeout, "upstream request timed out"
				}
				logger.ErrorContext(r.Context(), msg,
					"upstream", upstream, "method", r.Method, "path", r.URL.Path,
					"timeout_ms", cfg.upstreamTimeout.Milliseconds(), "error", err.Error())
				httpx.WriteJSON(w, status, map[string]string{
					"error":    http.StatusText(status),
					"upstream": upstream,
				})
			},
		}
		mux.Handle(pattern, withTimeout(cfg.upstreamTimeout, proxy))
	}

	route("GET /api/products", "inventory", cfg.inventoryURL, fixed("/inventory/products"))
	route("GET /api/products/{sku}", "inventory", cfg.inventoryURL, func(r *http.Request) string {
		return "/inventory/products/" + url.PathEscape(r.PathValue("sku"))
	})
	route("POST /api/orders", "orders", cfg.ordersURL, fixed("/orders"))
	route("GET /api/orders", "orders", cfg.ordersURL, fixed("/orders"))
	route("GET /api/orders/{id}", "orders", cfg.ordersURL, func(r *http.Request) string {
		return "/orders/" + url.PathEscape(r.PathValue("id"))
	})
	return mux
}

func fixed(path string) func(*http.Request) string {
	return func(*http.Request) string { return path }
}

func withTimeout(d time.Duration, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), d)
		defer cancel()
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}
