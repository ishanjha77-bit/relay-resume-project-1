// Command gateway is the sandbox's edge service: it routes the public shop API
// to the orders and inventory services with a per-request upstream timeout.
package main

import (
	"context"
	"log/slog"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"relay.dev/sandbox/golib/httpx"
	"relay.dev/sandbox/golib/logging"
	"relay.dev/sandbox/golib/telemetry"
)

var version = "dev"

type config struct {
	addr            string
	ordersURL       *url.URL
	inventoryURL    *url.URL
	upstreamTimeout time.Duration
}

func loadConfig() (config, error) {
	orders, err := url.Parse(env("ORDERS_URL", "http://orders:8080"))
	if err != nil {
		return config{}, err
	}
	inventory, err := url.Parse(env("INVENTORY_URL", "http://inventory:8080"))
	if err != nil {
		return config{}, err
	}
	timeoutMs, err := strconv.Atoi(env("UPSTREAM_TIMEOUT_MS", "4000"))
	if err != nil {
		return config{}, err
	}
	return config{
		addr:            ":" + env("PORT", "8080"),
		ordersURL:       orders,
		inventoryURL:    inventory,
		upstreamTimeout: time.Duration(timeoutMs) * time.Millisecond,
	}, nil
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func main() {
	logger := logging.New("gateway")
	if err := run(logger); err != nil {
		logger.Error("gateway stopped", "error", err)
		os.Exit(1)
	}
}

func run(logger *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	cfg, err := loadConfig()
	if err != nil {
		return err
	}
	shutdown, err := telemetry.Setup(ctx, "gateway", version)
	if err != nil {
		return err
	}
	defer shutdown(context.Background())

	logger.Info("gateway starting", "version", version,
		"upstream_timeout_ms", cfg.upstreamTimeout.Milliseconds(),
		"orders", cfg.ordersURL.String(), "inventory", cfg.inventoryURL.String())

	handler := httpx.Handler("gateway", logger, newRouter(cfg, logger), nil)
	return httpx.Serve(ctx, cfg.addr, handler, logger)
}
