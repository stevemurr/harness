The folder holds a small catalogue site under `site/`, and `serve.py`, which serves it:
`python3 serve.py 8765` listens on `http://127.0.0.1:8765/` until it is stopped. The page
builds itself in the browser from `site/api/catalog.json`, so what a person sees is not in
the HTML file.

Find out what the page shows and write `answers.json` at the top level of this folder:

```json
{
  "title": "<the text of the page's h1>",
  "grand_total": <the number after "Grand total:">,
  "line_totals": {"<item name>": <the number after "=" on that item's line>, ...}
}
```

Every item on the page goes in `line_totals`, keyed by its name exactly as shown. Numbers
are numbers, not strings. For this task the harness lets `open_url` reach this machine, so
you can read the page the way a browser would. Stop the server when you are done.
