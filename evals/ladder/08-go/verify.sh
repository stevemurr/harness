#!/bin/sh
set -eu
go test ./... > /dev/null
cat > wrap_check_test.go <<'EOF'
package text

import "testing"

func TestWrapChecked(t *testing.T) {
	got := Wrap("the quick brown fox", 10)
	want := []string{"the quick", "brown fox"}
	if len(got) != len(want) {
		t.Fatalf("got %d lines %q, want %d %q", len(got), got, len(want), want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("line %d: got %q want %q", i, got[i], want[i])
		}
	}
	if len(Wrap("", 5)) > 1 {
		t.Fatalf("empty input should not produce several lines")
	}
}
EOF
go test ./... > /dev/null
