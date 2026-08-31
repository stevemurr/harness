package text

import "testing"

func TestWords(t *testing.T) {
	if len(Words("a b  c")) != 3 {
		t.Fatal("expected three words")
	}
}
