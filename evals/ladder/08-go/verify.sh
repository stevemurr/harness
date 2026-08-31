#!/bin/sh
set -eu
go test ./... > /dev/null
cat > wrap_check_test.go <<'EOF'
package text

import "reflect"
import "testing"

func TestWrapChecked(t *testing.T) {
	cases := []struct {
		name  string
		text  string
		width int
		want  []string
	}{
		{"simple", "the quick brown fox", 10, []string{"the quick", "brown fox"}},
		{"exact fit", "abc def", 7, []string{"abc def"}},
		{"long word alone", "a supercalifragilistic b", 5, []string{"a", "supercalifragilistic", "b"}},
		{"collapses spaces", "a    b", 10, []string{"a b"}},
		{"trims", "   padded words   ", 20, []string{"padded words"}},
		{"empty", "", 5, nil},
		{"whitespace only", "   ", 5, nil},
		{"zero width", "abc", 0, nil},
		{"negative width", "abc", -3, nil},
	}
	for _, c := range cases {
		got := Wrap(c.text, c.width)
		if len(got) == 0 && len(c.want) == 0 {
			continue
		}
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: Wrap(%q, %d) = %#v, want %#v", c.name, c.text, c.width, got, c.want)
		}
	}
}
EOF
go test ./... > /dev/null
