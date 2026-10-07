package main

import (
	"context"
	"math"
	"runtime"
	"sync/atomic"
	"time"

	"relay.dev/sandbox/golib/flags"
)

// faultRunner applies the resource faults that aren't tied to the PSP:
//
//	cpu-burn        {"workers": 4}                 busy goroutines -> CPU throttling
//	goroutine-leak  {"per_request": 20, "kb": 64}  blocked goroutines -> memory growth, OOMKill
type faultRunner struct {
	flags   *flags.Store
	burners []context.CancelFunc
}

func newFaultRunner(f *flags.Store) *faultRunner {
	return &faultRunner{flags: f}
}

func (f *faultRunner) run(ctx context.Context) {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		f.reconcileCPU(ctx)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (f *faultRunner) reconcileCPU(ctx context.Context) {
	want := 0
	if p, ok := f.flags.Active("cpu-burn"); ok {
		want = p.Int("workers", 4)
	}
	for len(f.burners) < want {
		burnCtx, cancel := context.WithCancel(ctx)
		f.burners = append(f.burners, cancel)
		go burn(burnCtx)
	}
	for len(f.burners) > want {
		last := len(f.burners) - 1
		f.burners[last]()
		f.burners = f.burners[:last]
	}
}

var burnSink atomic.Uint64

func burn(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		x := 0.0
		for i := 1; i < 200_000; i++ {
			x += math.Sqrt(float64(i))
		}
		burnSink.Add(uint64(x))
	}
}

// blockForever is never closed or written to; leaked goroutines park on it.
var blockForever chan struct{}

// onCharge runs on every charge request.
func (f *faultRunner) onCharge() {
	p, ok := f.flags.Active("goroutine-leak")
	if !ok {
		return
	}
	n, kb := p.Int("per_request", 20), p.Int("kb", 64)
	for range n {
		go func() {
			buf := make([]byte, kb<<10)
			for i := range buf {
				buf[i] = byte(i)
			}
			<-blockForever
			runtime.KeepAlive(buf)
		}()
	}
}
