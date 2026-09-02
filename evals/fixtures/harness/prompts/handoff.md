You are compacting the context of a coding agent that is part-way through a task, so it can
keep working in a smaller context. Write the note the agent will wake up holding.

Use exactly these headings, in this order. Omit a heading only if it would be empty.

MODE:
One line, copied from here exactly: {mode}

REQUEST:
One line for each separate thing the user asked for: a few words naming it, then whether it
is done, partly done, or not started. Do not quote them. The user's own words -- the request
they opened with, and anything they sent while you worked -- are carried into the new context
verbatim and sit above this note, so quoting them here writes the same thing twice in two
places that can drift apart. Say what became of each one; the words are already kept.

CHANGED:
Only what was actually altered: files written or edited, and commands that changed something
or produced a result worth keeping. If nothing has been changed, write "nothing yet" and move
on. Discoveries do not belong here even though finding them took work.

FOUND:
What is now known about the code that was expensive to discover, one line each: where things
live, what a function does, exact line numbers, measurements, what a command reported, why an
approach was ruled out. Spend most of the note here.

STATE:
Two or three sentences on where the work stands.

NEXT:
One sentence naming the single next action.

USER:
Anything the user corrected, refused, or committed you to that is not already there in their
own words above.

Rules:

  * One line per item. No sub-bullets, no nested lists, no paragraphs inside a section.
  * Each fact appears exactly once, under one heading. A line number recorded in FOUND is not
    repeated in CHANGED or STATE; repeating it wastes the context this note exists to save.
  * Name files, symbols, commands and numbers instead of describing them: "serveAudio at
    main.go:156 is 82.6% covered, os.Open error path untested" not "some handlers lack
    coverage".
  * Record findings, not reasoning. Do not weigh options, estimate difficulty, judge whether
    something is worth doing, or narrate a chain of thought. If you find yourself writing
    "this is tricky" or "actually", delete the line.
  * Include nothing you were not told.
  * No preamble and no closing summary. Start at MODE and stop after the last heading.
