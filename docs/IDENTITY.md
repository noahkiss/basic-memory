# Note Identity: the Permalink Contract

A note's **permalink** is its identity in this fork. Edges bind to it, `memory://` addresses it,
and `bm doctor` checks it. Normalization decides what a permalink *is*, so the normalization rules
below are the identity contract, not a formatting detail.

This document recovers that contract **from the code**. Every rule cites `file:line`. Citations
were read against the working tree on **2026-08-16**; line numbers drift, function and constant
names do not, so both are given. [DOMAIN_MODEL.md](DOMAIN_MODEL.md) states what identity *means*;
this file states what the code *does*.

Recorded because it was undocumented and nearly lost: `docs/character-handling.md` held the only
written spec and was deleted (`GAPS.md` W21). Nothing recovered here comes from that file.

## 1. What is identity, and what is not

| Field | Identity? | Why |
|---|---|---|
| `permalink` | **Yes** | Every relation, `memory://` URI, and search row resolves through it. `src/basic_memory/models/knowledge.py:79` |
| `id` (frontmatter) | **Yes — the same string** | A governed record must carry `id` and `permalink` byte-for-byte equal. `src/basic_memory/vocabulary/checker.py:178-192` |
| `external_id` | No — a stable handle | A UUID that survives updates and moves. Nothing in a note's text points at it. `src/basic_memory/models/knowledge.py:69` |
| `file_path` | No — a location | Unique per project, changes on every move. `src/basic_memory/models/knowledge.py:81` |
| `title` | No — display metadata | Mutable. The resolver will *match* on it, which is not the same as binding to it. |
| `Entity.id` | No — a database row number | Internal, never crosses a boundary. `src/basic_memory/models/knowledge.py:67` |

A frontmatter key that is not the permalink is **not an edge target**. `[[some-custom-id]]` against
a note whose `id` lives only in a custom field parses, stores, and never resolves (`GAPS.md` T9,
finding 2, captured with a positive control).

## 2. Derivation

`generate_permalink()` (`src/basic_memory/utils.py:30`) turns a path into a permalink. It runs only
when a note has no explicit `permalink:` — see §3.

### 2.1 Before the character rules

1. **The path is POSIX-normalized.** Backslashes become forward slashes. `utils.py:56-57`
2. **A real file extension is split off and discarded.** "Real" means `mimetypes.guess_type()`
   returns a type, so `.md` goes and `2.0.0` stays. `utils.py:59-72`
3. **`/` is a segment separator and survives** every later step. A permalink is a path, not a flat
   slug. `utils.py:146`, `utils.py:151-155`

### 2.2 The ASCII path

This branch runs when the input carries no CJK characters (`utils.py:128-146`), in this order:

| Step | Rule | Line |
|---|---|---|
| 1 | **Unicode transliterates to ASCII** via `unidecode`. `café` becomes `cafe`, `naïve` becomes `naive`. | `utils.py:131` |
| 2 | **camelCase splits on a hyphen.** `myFeature` becomes `my-feature`. | `utils.py:134` |
| 3 | **Everything lowercases.** Permalinks are lowercase in this branch, always. | `utils.py:137` |
| 4 | **`_` becomes `-`.** Underscores never survive derivation. | `utils.py:140` |
| 5 | **Apostrophes are dropped, not hyphenated.** `don't` becomes `dont`, never `don-t`. | `utils.py:143` |
| 6 | **Every other character outside `[a-z0-9/.-]` becomes `-`.** Spaces, brackets, commas, `&`, `#`, `:` all collapse to hyphens. | `utils.py:146` |
| 7 | **Periods survive.** They are in the keep-set at step 6, which is what preserves `version-2.0.0`. | `utils.py:146` |

### 2.3 The CJK path

When any character falls in the CJK ranges `\u4e00-\u9fff` (unified ideographs),
`\u3000-\u303f` (symbols), `\u3400-\u4dbf` (extension A), or `\uff00-\uffef` (fullwidth
forms), a different branch runs (`utils.py:85-127`). The test itself is `utils.py:77-83`.

- **CJK ideographs and symbols are preserved verbatim.** They are not transliterated.
  `utils.py:89-95`
- **Fullwidth punctuation (`\uff00-\uffef`) is deleted outright**, not hyphenated. `utils.py:96-98`
- **Latin accented characters still transliterate** through `unidecode`, one character at a time.
  `utils.py:99-101`
- **A hyphen is inserted at every CJK↔Latin boundary.** `utils.py:105-110`
- Steps 2 through 7 of §2.2 then run, with the CJK ranges added to the keep-set so they survive the
  unsafe-character sweep. `utils.py:112-127`

So `中文/测试文档.md` yields `中文/测试文档`. **A permalink is not guaranteed ASCII.**

### 2.4 After the character rules

1. **Runs of hyphens collapse to one.** `utils.py:148-149`
2. **Each `/`-separated segment is stripped of leading and trailing hyphens.** `utils.py:151-155`

There is **no length limit** and **no leading-digit rule**. A permalink may be empty for an input
that reduces to nothing; nothing in the derivation guards that.

### 2.5 The project prefix

`build_canonical_permalink()` (`utils.py:178`) prefixes the note's normalized path with the
project's own slug, unless the path already starts with it (`utils.py:195-204`).

- Controlled by `permalinks_include_project`, **default `True`**.
  `src/basic_memory/config_models.py:404-407`
- Applied at derivation, in `resolve_permalink()`.
  `src/basic_memory/services/note_preparation.py:219-234`
- The project slug is itself `generate_permalink(project.name)`.
  `src/basic_memory/models/project.py:96-97`

**Consequence:** a derived permalink embeds the project name, so it is not portable across a
rename. In practice it is also frozen. The indexer writes the derived permalink back into the
note's frontmatter at first index (§3). A later rename therefore leaves every permalink unchanged,
silently carrying the old project name (`GAPS.md` T9, "the rename cost is BACKWARDS").

## 3. Explicit permalinks win, byte-for-byte

An explicit `permalink:` in frontmatter **is not normalized**. It is stored exactly as written.

- If the note declares one and no other note holds it, that string is the permalink and the
  function returns before any derivation.
  `src/basic_memory/services/note_preparation.py:205-211`
- So `tnd_uuuu1111` stays underscored. §2.2 step 4 never sees it. Verified in `GAPS.md` T9's
  amendment ("finding 3 is FALSE for an explicit `permalink:`").

**Underscores in an explicit permalink are still a trap**, and `bm doctor` flags them
(`src/basic_memory/repository/entity_repository.py:130-138`). Relation permalinks are re-slugified
(§6), so `a_b` and `a-b` collapse into the same relation row, and `memory://` normalization
(§6.3) cannot address the underscored form. Keep explicit ids hyphenated.

**An already-stored permalink wins over derivation.** If the file path is already indexed and the
note declares no explicit `permalink:`, `resolve_permalink` returns the stored value and never
re-derives. `src/basic_memory/services/note_preparation.py:213-217`. This is what freezes a
permalink against a later project rename or a change in the derivation rules.

The indexer then **stamps the resolved permalink into the file's frontmatter** on first index
(`src/basic_memory/indexing/batch_indexer.py:340-367`). Under the default flags (§5.5) every
indexed markdown note carries a `permalink:` key afterwards. That is why every later move is a
*change* to a set-once field rather than a first set (§5).

## 4. Collisions

Derivation can produce a permalink another note already owns. Two independent suffix loops resolve
that, both by appending `-1`, `-2`, `-3` … until the string is free:

- **Single writes** check the database: `while permalink_exists(...)`.
  `src/basic_memory/services/note_preparation.py:236-240`
- **Batch indexing** checks an in-flight reservation set as well, because the colliding sibling may
  not be committed yet. `src/basic_memory/indexing/batch_indexer.py:398-409`

The suffix is part of the permalink and therefore part of identity. It is never recomputed.

**The database is the backstop.** `permalink` + `project_id` is unique for markdown notes with a
non-null permalink. `src/basic_memory/models/knowledge.py:51-57`

**Conflict detection is advisory only.** Before deriving, `resolve_permalink` asks
`detect_potential_file_conflicts()` for paths that differ only by case, by Unicode normalization,
or by collapsing to the same permalink, and logs a WARNING. It does not block the write.
`src/basic_memory/utils.py:583-622`, `src/basic_memory/services/note_preparation.py:194-203`.
The comparison lowercases and applies NFD. `src/basic_memory/utils.py:556-580`

## 5. Set-once, and what happens on move

### 5.1 The rule

`permalink` is a set-once field on a governed project, and the strictest member of the list.
`src/basic_memory/vocabulary/checker.py:89-103` (`_SET_ONCE`)

A write that changes **or drops** a set-once field already present in the previous frontmatter
raises a `set-once-changed` violation. A field absent before and present now is a *first set*, not
a change. `src/basic_memory/vocabulary/checker.py:501-527`

"Governed" means the project has a `vocabulary.yml`. Without one, no rule runs at all — an absent
file is not the defaults. `src/basic_memory/services/vocabulary_enforcement.py:95-98`

### 5.2 Three write paths, two enforcement modes

| Path | Mode | On a permalink rewrite |
|---|---|---|
| Accepted writes (verbs, MCP, API) — `src/basic_memory/indexing/accepted_note_mutation_runner.py:899-912` | `reject` | The move fails with 400. Nothing is written. |
| Sync / reindex — `EntityService` | `record` | Logged as a WARNING, indexed anyway. |
| Watcher move planner — `src/basic_memory/index/local_moves.py:193-213` | `record` | Logged, **and the rewrite is skipped.** |

The funnel is one rule engine with two entry points. `enforce_vocabulary()` loads the project's
vocabulary per write (`src/basic_memory/services/vocabulary_enforcement.py:46`).
`apply_vocabulary()` takes an already-loaded one, so the sync path reads `vocabulary.yml` once per
reindex rather than once per note (`src/basic_memory/services/vocabulary_enforcement.py:79`). A
write path that calls neither is a bug, not a policy choice.

### 5.3 Governed move: the permalink does not change

On a governed project the watcher's move planner logs the `set-once-changed` violation as a WARNING
and then returns `None`, which is the pre-existing "no content update" signal. The file and the
entity row keep the permalink the human already had. `src/basic_memory/index/local_moves.py:200-213`

Nothing is persisted on that branch. The violation was judged against a permalink this branch
declines to write, so a stored `violation` row would describe a file that does not exist. Move
violations that *do* get written go through `_persist_move_violations`, reached only when the
rewrite proceeds. `src/basic_memory/index/local_moves.py:215`

The skip is deliberately narrow: **only** a `set-once-changed` violation on `permalink` stops the
rewrite. An off-vocabulary note — bad `type`, missing required field — is recorded like any other
hand-edit and still gets its permalink kept current.
`src/basic_memory/index/local_moves.py:110-120` (`_changes_set_once_permalink`)

The check has to run at plan time. The move batch stamps the rows with the *planned* content's
checksum, so a rewritten file never presents as modified and no later index pass would ever see it
(`GAPS.md` T23).

### 5.4 Ungoverned move: the permalink follows the path

With no `vocabulary.yml`, the checker returns no violations, so the planner rewrites the permalink
to match the new path — subject to the config flags below. This is upstream's behavior and it is
what makes a permalink drift silently, which is the whole reason §5 exists.

### 5.5 The config flags that gate all of it

| Flag | Default | Effect | Line |
|---|---|---|---|
| `update_permalinks_on_move` | `False` | When off, a move never rewrites the permalink, governed or not. | `src/basic_memory/config_models.py:344-347` |
| `disable_permalinks` | `False` | When on, no permalink is generated or updated. Existing ones still resolve. | `src/basic_memory/config_models.py:384-387` |
| `permalinks_include_project` | `True` | Prefix derived permalinks with the project slug (§2.5). | `src/basic_memory/config_models.py:404-407` |
| `ensure_frontmatter_on_sync` | `True` | Adds derived title/type/permalink to files that have no frontmatter. Wins over `disable_permalinks` for that case. | `src/basic_memory/config_models.py:399-402` |

Both move paths read the first two before doing anything.
`src/basic_memory/index/local_moves.py:170-176`,
`src/basic_memory/indexing/accepted_note_mutation_runner.py:195-198`

## 6. Where links bind

### 6.1 Wikilinks

`LinkResolver._resolve_in_project()` (`src/basic_memory/services/link_resolver.py:200`) tries, in
order:

1. **Relative path**, when the link contains `/` and a source note is known.
   `link_resolver.py:238-267`
2. **Exact permalink**, over a candidate list. `link_resolver.py:328-341`
3. **Exact title.** `link_resolver.py:343-359`
4. **File path**, then file path + `.md`. `link_resolver.py:361-381`
5. **Fuzzy search**, non-strict callers only. `link_resolver.py:395-415`

The candidate list is built by `build_permalink_resolution_candidates()`
(`src/basic_memory/utils.py:235-280`): the caller's verbatim identifier first, then its slugified
form, then the project-qualified form, then the de-prefixed form. **The verbatim candidate is what
lets an explicit non-slug permalink such as `API_V2` resolve at all** — inferring exactness from
slug shape would wrongly reject it. `link_resolver.py:321-326`

A destructive (strict) resolve refuses to guess between several notes sharing a title, and raises
`AmbiguousIdentifierError`. Only the caller's verbatim permalink candidate bypasses that guard.
`link_resolver.py:315-341`, `link_resolver.py:383-389`

### 6.2 Relation and observation permalinks are re-derived

A relation's permalink is `generate_permalink(f"{from}/{type}/{to}")`
(`src/basic_memory/models/knowledge.py:373-386`). An observation's is
`generate_permalink(f"{entity}/observations/{category}/{content}")`
(`src/basic_memory/models/knowledge.py:314-337`), with a sha256 digest appended when the content
exceeds the prefix budget, so two long observations sharing a 200-character prefix do not collide.

**This is why an underscore in an explicit permalink is unsafe.** The synthetic permalink runs
through §2.2 step 4, so the edge row is keyed on the hyphenated twin.

### 6.3 `memory://` URIs

`ContextService.build_context()` normalizes the URI path through `generate_permalink(...,
split_extension=False)` before looking it up, and normalizes each side of a `*` separately for
wildcard queries. `src/basic_memory/services/context_service.py:125-146`

Extension splitting is off here, so a literal `.md` in the URI is kept rather than stripped.

## 7. What `bm doctor` checks

`EntityRepository.find_permalink_integrity_issues()`
(`src/basic_memory/repository/entity_repository.py:91-139`) reports two conditions, printed by
`src/basic_memory/cli/commands/doctor.py:213-226`:

- **`drift`** — the database permalink and the indexed frontmatter permalink disagree. A hand edit
  to `permalink:` after first index lands here. Identity is no longer set-once.
- **`underscore`** — the permalink contains `_`. See §3 and §6.2.

Both are checkable without re-reading the files. Neither is repaired automatically.

## 8. Practical rules

- **Write ids hyphenated, lowercase, ASCII.** They then survive §2 and §6.2 unchanged, whether they
  are explicit or derived.
- **Do not hand-edit `permalink:` after a note is indexed.** That is exactly the `drift` case.
- **Do not expect a permalink to track a rename.** It does not, by design here (§5.3), and it does
  not upstream either in practice (§2.5).
- **Do not treat a permalink as ASCII, or as free of periods, or as a single flat token.** It may
  contain CJK characters, `.`, and `/`.

## Related documentation

- [Domain Model](DOMAIN_MODEL.md) — what identity means, and which layer owns it
- [Note Format](NOTE-FORMAT.md) — the frontmatter and wikilink syntax that produces these values
- [Metadata Queries](METADATA-QUERIES.md) — searching frontmatter, including by permalink
- `GAPS.md` **T9**, **W4**, **T23**, **W21** — the decisions this file records
