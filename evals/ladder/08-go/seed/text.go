// Package text holds small string helpers.
package text

import "strings"

// Words splits text on whitespace.
func Words(s string) []string {
	return strings.Fields(s)
}
