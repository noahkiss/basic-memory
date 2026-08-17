# Querying Frontmatter

`bm tool search-notes` searches frontmatter two ways: as **text**, through full-text search, and as
**structure**, through exact-match filters. This document covers the filter grammar, because
`--help` names the flag and none of its operators.

Citations were read against the working tree on **2026-08-16**.
[OUTPUT_CONTRACT.md](OUTPUT_CONTRACT.md) governs how results render; this file governs how the
query is written.

## 1. Two paths to the same frontmatter

| Path | Spelling | Matching | Use it for |
|---|---|---|---|
| Full-text | `bm tool search-notes "zq7-4f21"` | Stemmed, fuzzy, ranked | Discovery, when you do not know the key |
| Filters | `--meta`, `--filter`, `--tag`, `--status`, `--type` | Exact | A query whose answer must be complete |

Frontmatter keys and scalar values are flattened into the note's FTS terms
(`SearchService._frontmatter_search_terms`, `src/basic_memory/services/search_service.py:682`).
A value that appears nowhere in the body is therefore still reachable by a plain text query
(`GAPS.md` W18). Filters remain the exact-match path.

**Terms are inserted ahead of the body**, and `content_stems` truncates from the tail, so on a very
large note the body may fall out of the index rather than the frontmatter.

## 2. A query needs no text

A metadata-only query is supported and is the normal shape for a staleness sweep:

```
bm tool search-notes --filter '{"review-by":{"$lt":"2026-08-16"}}'
```

Omit the query argument entirely. Do **not** reach for the old `"**"` idiom — it now raises
`sqlite3.OperationalError` (`GAPS.md` T7).

## 3. The convenience flags

| Flag | Repeatable | Becomes |
|---|---|---|
| `--tag python --tag async` | yes | `{"tags": ["python", "async"]}` — a note must carry **all** of them |
| `--status draft` | no | `{"status": "draft"}` |
| `--type finding` | yes | A separate note-type predicate, **case-insensitive**, not a metadata filter |
| `--meta key=value` | yes | `{"key": "value"}`, value always a string |

`--tag` and `--status` merge into the metadata filters with `setdefault`, so an explicit
`--filter` entry for the same key wins. `src/basic_memory/services/search_service.py:155-161`

`--type` takes a different route: it compares `LOWER(...)` on both sides against the indexed
`note_type`, so `--type Chapter` matches a stored `chapter`.
`src/basic_memory/repository/sqlite_search_repository.py:846-855`

## 4. `--filter`: the full grammar

`--filter` takes one JSON object. Every entry is parsed by `parse_metadata_filters()`
(`src/basic_memory/repository/metadata_filters.py:152`).

**Keys** match `^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$`. A dot descends into nested frontmatter:
`schema.confidence` reads `$."schema"."confidence"`.
`src/basic_memory/repository/metadata_filters.py:11`, `:267-272`

**Entries combine with AND.** There is no `$or` and no top-level negation.
`src/basic_memory/repository/sqlite_search_repository.py:987`

| Form | Meaning | Line |
|---|---|---|
| `{"status": "draft"}` | Equality. Also matches when the stored value is a **list containing** `draft`. | `metadata_filters.py:261-262`, `sqlite_search_repository.py:909-924` |
| `{"tags": ["a", "b"]}` | A bare list means **contains all of**. | `metadata_filters.py:244-253` |
| `{"tags": {"$contains": "a"}}` | Element-wise: the list holds this element. A list argument means all of them. | `metadata_filters.py:227-237` |
| `{"priority": {"$in": ["high", "critical"]}}` | **Any of.** Requires a non-empty list. | `metadata_filters.py:190-196` |
| `{"score": {"$gt": 0.7}}` | Also `$gte`, `$lt`, `$lte`. Numeric when the value parses as a number, text otherwise. | `metadata_filters.py:198-213` |
| `{"score": {"$between": [0.3, 0.6]}}` | Inclusive. Requires exactly `[min, max]`. | `metadata_filters.py:215-225` |

**Three keys take a shortcut column** when the schema has one: `status` reads
`entity.frontmatter_status`, `type` reads `entity.frontmatter_type`, and `tags` reads
`entity.tags_json`. `src/basic_memory/repository/sqlite_search_repository.py:884-901`. The first two
are plain text and can never hold a list, so the list-containing behaviour of equality does not
apply to them.

An operator object must hold **exactly one** key. Two operators on one field is an error, not an
implicit AND. `src/basic_memory/repository/metadata_filters.py:180-182`

Any other `$`-operator is rejected by name, with the supported set in the message.
`src/basic_memory/repository/metadata_filters.py:239-242`

### Dates and booleans

Dates compare as **text**, so ISO-8601 (`2026-08-16`) sorts correctly and nothing else does.
`datetime` and `date` values are stringified to ISO before comparison.
`src/basic_memory/repository/metadata_filters.py:114-123`

A boolean equality matches **both stored spellings**. An unquoted YAML `true` is indexed as
`"True"`, a quoted one keeps what the author typed, and `--meta draft=true` arrives as `"true"`;
the filter becomes a set membership over all of them so neither spelling silently misses.
`src/basic_memory/repository/metadata_filters.py:126-144`, `:255-259`

## 5. Traps

### 5.1 Database column names are rejected

`--filter '{"updated_at": {"$lt": "..."}}'` reads the **frontmatter** key `updated_at`, not the
`entity.updated_at` column. Notes carry no such frontmatter key, so the query used to return
`0 results` at exit 0 over a corpus where every note qualifies — a wrong answer indistinguishable
from a true empty result. The same held for `created_at`.

**T21 closed this: those names now error.** `validate_metadata_filter_keys()` rejects a
bare key that names an `Entity` column and names the parameter to use instead
(`src/basic_memory/repository/metadata_filters.py:71-87`, called from `:166` and from
`src/basic_memory/mcp/tools/search.py:894`). The refusal reaches every caller: HTTP 400 on the
API, a tool error on MCP, and exit 1 with the message on stderr for `bm tool search-notes`.
A dotted key such as
`schema.updated_at` is genuine frontmatter nesting and still works. `title`, `type`, and `permalink`
are deliberately not rejected: basic-memory writes all three into frontmatter, so filtering on them
is correct.

The repository does have a real `before_date` predicate
(`src/basic_memory/repository/sqlite_search_repository.py:869-871`), but the filter grammar still
cannot reach it.

Use `--after_date` for recency. There is no CLI flag for the staleness direction yet.

### 5.2 A bare list is AND, not OR

`{"tags": ["a", "b"]}` requires both. For either, write `{"tags": {"$in": ["a", "b"]}}`. The two
spellings look almost alike and return different result sets, so the wrong one reads as a real
answer.

### 5.3 Mode flags are exclusive

`--permalink`, `--title`, `--vector`, and `--hybrid` select the retrieval mode and only one may be
given. The command fails rather than picking.
`src/basic_memory/cli/commands/tool.py:778-780`

`--permalink` makes the text query a **permalink glob**, not a content search. Dropping it turns
the same string into a text search, silently.

## Related documentation

- [Note Identity](IDENTITY.md) — what a permalink is, and why `--permalink` globs are exact
- [Note Format](NOTE-FORMAT.md) — the frontmatter these queries read
- [CLI Output Contract](OUTPUT_CONTRACT.md) — how results render
