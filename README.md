# tag-my-mail

Tag the emails in your [msgvault](https://msgvault.io/) archive with the
[Gist](https://desertant.com/models/gist/) on-device topic model (36 topics,
101 languages), from a Python command line.

Pipeline:

```
SQL (your query) → msgvault query (read-only, DuckDB over Parquet)
                 → da gist (desertant CLI, local inference)
                 → JSONL / CSV
```

Python is only the orchestrator: it holds no model and touches no database.
Both tools are external CLIs, so your archive stays read-only.

## Requirements

- Python >= 3.9, standard library only
- `msgvault` on PATH (already the case if you use msgvault)
- `desertant` (`da`) on PATH: `brew install desert-ant-labs/tap/desertant`
  (macOS and Linux; the CLI does not ship for Windows)

The first `da gist` call downloads the 74 MB model weights once, into the
desertant cache.

## Install

```sh
pip install -e .
tag-my-mail --help
```

Or run it without installing: `python3 -m tag_my_mail ...`

## Usage

Pick a time range and a SQL query; every row the query returns gets tagged:

```sh
tag-my-mail \
  --from 2026-11-01 --to 2026-11-13 \
  --sql "SELECT id, subject, snippet, sent_at
         FROM messages
         WHERE sent_at >= '{from}' AND sent_at <= '{to}'
         ORDER BY sent_at DESC" \
  --out tags.jsonl
```

- `{from}` / `{to}` in the SQL are replaced by the `--from` / `--to` bounds
  (push-down into DuckDB — this is the scalable path on a large archive).
  The quotes are optional: `{from}` and `'{from}'` both work.
- `--from` without `--to` ends the range at **today** (inclusive), so
  "since this date" needs no extra flag.
- If the query selects a `sent_at` column (see `--time-column`), rows are
  also filtered client-side, so a forgotten placeholder cannot widen the
  range. If neither is possible, a warning says the range was not applied.
- `--to` is inclusive; a date-only value means the end of that day.

### No SQL? A default query

Without `--sql`, `--sql-file`, or piped stdin, the tool runs a generic
query over `v_messages` — the 1000 most recent messages with the fields
Gist needs, excluding what Gmail filed as `SPAM` — and prints it on
stderr:

```sql
SELECT id, subject, snippet, sent_at, from_email
FROM v_messages
WHERE NOT list_contains(labels::VARCHAR[], 'SPAM')
ORDER BY sent_at DESC LIMIT 1000
```

So `tag-my-mail --from 2026-11-01` tags everything since November 1st
with no SQL at all. Raise or remove the cap, or drop the spam filter, by
passing your own query.

No time range needed? Drop `--from`/`--to` and the placeholders:

```sh
tag-my-mail --sql "SELECT id, subject, snippet FROM messages WHERE subject LIKE '%invoice%'" --quiet
```

The SQL can also come from a file (`--sql-file query.sql`) or stdin
(`--sql -`, or just pipe it).

### Useful flags

| Flag | Effect |
| --- | --- |
| `--dry-run` | Print the final SQL, columns and a row preview; tag nothing |
| `--text-columns subject,snippet` | Columns joined into the text Gist reads (default shown) |
| `--top 3` | Topics per message (default 3) |
| `--max-rows 500` | Stop after tagging 500 rows (safety cap) |
| `--format csv` | CSV instead of JSONL (`tags` column holds a JSON array) |
| `--out FILE` | Write to a file instead of stdout |
| `--no-text` | Drop the tagged text from each record (smaller output) |
| `--cache PATH` / `--no-cache` | Tag cache (default `.tag-my-mail.cache.json`) |
| `--fresh` | Ask msgvault to wait for analytics to include recent writes |
| `-q` / `-v` | Less / more stderr detail |

Defaults worth remembering: `--to` becomes today when only `--from` is
given, and the SQL falls back to the `v_messages` query above.

### Output

JSONL, one record per message:

```json
{"row": {"id": 561893, "subject": "...", "snippet": "...", "sent_at": "2026-11-13T09:12:31Z"},
 "text": "...",
 "tags": [{"slug": "technology", "name": "Technology & Software", "score": 0.92}]}
```

A failed tag keeps the row and adds an `"error"` field; the run exits 1 if
any row failed, so pipelines can tell.

Count topics across a run with `jq`:

```sh
jq -r '.tags[].slug' tags.jsonl | sort | uniq -c | sort -rn
```

## Performance

About 0.3 s per message (`da gist` loads the model per call) plus the query
itself. The cache makes reruns on the same texts free — a second identical
run of 5 messages took 0.0 s here. On thousands of rows, let the SQL do the
filtering (`{from}`/`{to}` + `LIMIT`) rather than fetching the whole table.

## Development

```sh
python3 -m unittest discover -s tests   # 35 tests, stdlib only
```

Layout: `core.py` (pure helpers: time bounds, placeholders, text assembly),
`msgvault.py` (query runner), `gist.py` (tagger + cache), `cli.py`
(argparse wiring). No test needs the network or either CLI.

## Limitations (POC)

- `da gist` is invoked once per message; there is no batch stdin mode.
- Windows: msgvault and desertant are the constraints, not this script.
- Tags are written to your output file only — nothing is pushed back into
  msgvault (its query CLI is read-only by design).
