package flags

import (
	"encoding/json"
	"testing"
)

func TestParamsDecodeNumbersAndStrings(t *testing.T) {
	var all map[string]Params
	raw := `{"psp-latency": {"delay_ms": 2500, "ratio": "0.5"}}`
	if err := json.Unmarshal([]byte(raw), &all); err != nil {
		t.Fatal(err)
	}
	p := all["psp-latency"]
	if got := p.Int("delay_ms", 0); got != 2500 {
		t.Errorf("delay_ms = %d, want 2500", got)
	}
	if got := p.Float("ratio", 1); got != 0.5 {
		t.Errorf("ratio = %v, want 0.5", got)
	}
	if got := p.Int("missing", 7); got != 7 {
		t.Errorf("missing = %d, want default 7", got)
	}
}

func TestHitHonoursRatio(t *testing.T) {
	always := NewStatic(map[string]Params{"f": {"ratio": 1.0}})
	never := NewStatic(map[string]Params{"f": {"ratio": 0.0}})
	unset := NewStatic(map[string]Params{})

	for range 100 {
		if _, hit := always.Hit("f"); !hit {
			t.Fatal("ratio 1.0 must always hit")
		}
		if _, hit := never.Hit("f"); hit {
			t.Fatal("ratio 0.0 must never hit")
		}
		if _, hit := unset.Hit("f"); hit {
			t.Fatal("unset flag must never hit")
		}
	}
}
