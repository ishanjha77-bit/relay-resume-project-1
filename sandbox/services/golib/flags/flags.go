// Package flags polls a service's runtime fault flags from Redis.
//
// The sandbox injects failures by flipping these flags instead of redeploying:
// a rollout would itself look like a deploy to the agent and give the answer
// away. Flags live under ops:flags:<service> as JSON, for example
//
//	{"psp-latency": {"delay_ms": 2500, "ratio": 1.0}}
//
// Polling is silent on purpose — fault injection must not leave log lines the
// agent could read instead of diagnosing the real symptoms.
package flags

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"math/rand/v2"
	"strconv"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// Params are a flag's parameters, decoded from JSON.
type Params map[string]any

// Float returns a numeric parameter, or def when absent or malformed.
func (p Params) Float(key string, def float64) float64 {
	switch v := p[key].(type) {
	case float64:
		return v
	case string:
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

// Int returns an integer parameter, or def when absent or malformed.
func (p Params) Int(key string, def int) int {
	return int(p.Float(key, float64(def)))
}

// String returns a string parameter, or def when absent or not a string.
func (p Params) String(key, def string) string {
	if v, ok := p[key].(string); ok {
		return v
	}
	return def
}

// Store holds the current flags for one service.
type Store struct {
	mu     sync.RWMutex
	active map[string]Params
}

// NewStatic returns a Store with fixed flags (used by tests).
func NewStatic(active map[string]Params) *Store {
	return &Store{active: active}
}

// Active reports whether a flag is set and returns its parameters.
func (s *Store) Active(name string) (Params, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	p, ok := s.active[name]
	return p, ok
}

// Hit reports whether a flag is set and a random draw falls under its "ratio"
// parameter (default 1.0, i.e. every call).
func (s *Store) Hit(name string) (Params, bool) {
	p, ok := s.Active(name)
	if !ok {
		return nil, false
	}
	return p, rand.Float64() < p.Float("ratio", 1)
}

func (s *Store) replace(next map[string]Params) {
	s.mu.Lock()
	s.active = next
	s.mu.Unlock()
}

// Start polls Redis every two seconds until ctx is cancelled. With an empty
// addr the store stays empty, which is the normal local-dev mode.
func Start(ctx context.Context, addr, service string, logger *slog.Logger) *Store {
	s := &Store{active: map[string]Params{}}
	if addr == "" {
		return s
	}
	redis.SetLogger(silentLogger{})
	client := redis.NewClient(&redis.Options{
		Addr:         addr,
		DialTimeout:  300 * time.Millisecond,
		ReadTimeout:  300 * time.Millisecond,
		WriteTimeout: 300 * time.Millisecond,
		MaxRetries:   -1,
		PoolSize:     2,
	})
	key := "ops:flags:" + service

	go func() {
		defer client.Close()
		ticker := time.NewTicker(2 * time.Second)
		defer ticker.Stop()
		for {
			s.refresh(ctx, client, key, logger)
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
		}
	}()
	return s
}

func (s *Store) refresh(ctx context.Context, client *redis.Client, key string, logger *slog.Logger) {
	raw, err := client.Get(ctx, key).Result()
	switch {
	case errors.Is(err, redis.Nil):
		s.replace(map[string]Params{})
	case err != nil:
		// Keep the last known flags; Redis being down must not change behaviour.
		logger.DebugContext(ctx, "flag refresh failed", "error", err)
	default:
		next := map[string]Params{}
		if err := json.Unmarshal([]byte(raw), &next); err != nil {
			logger.DebugContext(ctx, "ignoring malformed flags", "error", err)
			return
		}
		s.replace(next)
	}
}

// silentLogger stops go-redis from printing dial errors to stderr.
type silentLogger struct{}

func (silentLogger) Printf(context.Context, string, ...any) {}
