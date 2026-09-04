# 0023 A screenshot is a file and a reading, not an image in the transcript

Decided 2026-09-03.

## Decision

`screenshot` writes the PNG under `~/.harness/screenshots/` and returns text: the page's
title, its document size against the viewport, headings, landmarks, images without alt,
the body's font and colours, console errors, and requests that failed. An image an MCP
server returns is treated the same way: written to that folder, and the result names the
path. The model never receives image bytes. A person opens the file.

## Context

The model in daily use can read a picture; the harness cannot carry one. `Message.content`
is a string, and every transcript row, provider request, compaction note, server event and
editor update is built on that. Carrying an image is a change to the type the whole
harness is made of, and it would land in `store/codec.py`, every provider, `compaction.view`
and both front ends at once.

The ladder's discipline is the second reason. A rung runs the artifact and never reads the
answer; a model judging its own screenshot is the verification `loop.py` refuses, with the
failure it was refused for -- a confident false pass. The visual rung (`21-site`) is
therefore graded by DOM, CSS and behaviour in a real browser, and the screenshots it takes
are for a person.

Meanwhile most of what a screenshot would tell a model is checkable without seeing it: a
document 900 pixels wide at a 390-pixel viewport scrolls sideways, an image without alt
text is a fact, a console error is text already. The reading is that part, and it is the
part a text-only model can act on.

## Consequences

The tool works with every provider and every model the harness runs today, and it costs
one row of text per call rather than a megabyte of base64 in the context. What is given
up is the model noticing what only a picture shows: that two colours clash, that a card is
misaligned by four pixels, that the page looks like a template.

What would change this: a measurement on `21-site` showing the reading is not enough --
runs that pass the checks and still produce pages a person would not ship, where the
transcript shows the model asked for the picture it could not see. The PNG is already on
disk and the result already names it, so the change would be to `Message` and its
carriers, not to the tool.
