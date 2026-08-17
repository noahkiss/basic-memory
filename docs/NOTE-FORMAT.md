# Note Format Reference

Every document in Basic Memory is a plain Markdown file. Files are the source of truth — changes to files automatically update the knowledge graph in the database. You maintain complete ownership, files work with git, and knowledge persists independently of any AI conversation.

## Document Structure

A note has three parts: YAML frontmatter, content (observations), and relations.

```markdown
---
title: Coffee Brewing Methods
type: note
tags: [coffee, brewing]
permalink: coffee-brewing-methods
---

# Coffee Brewing Methods

## Observations
- [method] Pour over provides more flavor clarity than French press
- [technique] Water temperature at 205°F extracts optimal compounds #brewing
- [preference] Ethiopian beans work well with lighter roasts (personal experience)

## Relations
- relates_to [[Coffee Bean Origins]]
- requires [[Proper Grinding Technique]]
- contrasts_with [[Tea Brewing Methods]]
```

The `## Observations` and `## Relations` headings are conventional but not required — the parser detects observations and relations by their syntax patterns anywhere in the document.

## Frontmatter

YAML metadata between `---` fences at the top of the file.

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `title` | No | filename stem | Used for linking and references. Auto-set from filename if missing. |
| `type` | No | `note` | Entity type. Used for filtering. |
| `tags` | No | `[]` | List or comma-separated string. Used for organization and search. |
| `permalink` | No | generated from title | Stable identifier. Persists even if the file moves. |
| `created` | No | file ctime | Canonical semantic creation timestamp. Accepts ISO 8601 dates or datetimes. |
| `modified` | No | file mtime | Canonical semantic modification timestamp. Accepts ISO 8601 dates or datetimes. |

Custom fields are allowed. Any key not in the standard set is stored as `entity_metadata` and indexed for search and filtering.

```yaml
---
title: Paul Graham
type: Person
tags: [startups, essays, lisp]
permalink: paul-graham
status: active
source: wikipedia
---
```

Here `status` and `source` are custom fields stored in `entity_metadata`.

### Frontmatter Value Handling

YAML automatically converts some values to native types. Basic Memory normalizes them:

- Date strings (`2025-10-24`) → kept as ISO format strings
- Numbers (`1.0`) → converted to strings
- Booleans (`true`) → converted to strings (`"True"`)
- Lists and dicts → preserved, items normalized recursively

This prevents errors when downstream code expects string values.

### Canonical Note Timestamps

`created` and `modified` describe the note, not the current file object. Basic Memory parses these
canonical fields once when it accepts or indexes Markdown and carries the resulting typed values
through entity, search, and directory projections.

- Date-only values use midnight in the machine's local timezone.
- Datetimes without an offset use the machine's local timezone.
- Explicit UTC or numeric offsets remain unchanged.
- A missing or null field falls back independently to the file's ctime or mtime. When file stats
  are unavailable, both missing values use one timestamp from the current operation.
- An invalid canonical value is an indexing error for that field; Basic Memory does not silently
  replace it with a file timestamp.

Filesystem mtime, checksum, size, and path remain physical synchronization bookkeeping. A move,
materialization, or incidental bookkeeping update does not change a note's semantic timestamps.
Passive indexing does not add or rewrite `created` or `modified`; notes without them remain
compatible through the filesystem fallback. Timestamp aliases are not canonical fields.

## Observations

An observation is a categorized fact about the entity. Written as a Markdown list item.

**Syntax:**

```
- [category] content text #tag1 #tag2 (context)
```

| Part | Required | Description |
|------|----------|-------------|
| `[category]` | Yes | Classification in square brackets. Any text except `[]()` chars. |
| content | Yes | The fact or statement. |
| `#tags` | No | Inline tags. Space-separated, each starting with `#`. |
| `(context)` | No | Parenthesized text at end of line. Supporting details or source. |

### Examples

```markdown
- [tech] Uses SQLite for storage #database
- [design] Follows local-first architecture #architecture
- [decision] Selected bcrypt for passwords #security (based on OWASP audit)
- [name] Paul Graham
- [expertise] Startups
- [expertise] Lisp
- [expertise] Essay writing
```

Array-like fields use repeated categories — multiple `[expertise]` observations above.

### What Is Not an Observation

The parser excludes these list item patterns:

| Pattern | Example | Reason |
|---------|---------|--------|
| Checkboxes | `- [ ] Todo item`, `- [x] Done`, `- [-] Cancelled` | Task list syntax |
| Markdown links | `- [text](url)` | URL link syntax |
| Bare wiki links | `- [[Target]]` | Treated as a `links_to` relation instead |

A list item with `#tags` but no `[category]` is still parsed — the tags are extracted and the category defaults to `Note`.

## Relations

Relations connect documents to form the knowledge graph. There are two kinds.

### Explicit Relations

Written as list items with a relation type and a `[[wiki link]]` target. Unquoted
relation types are single tokens. Quote relation types that contain spaces.

**Syntax:**

```
- relation_type [[Target Entity]] (context)
- "multi word relation type" [[Target Entity]] (context)
- 'multi word relation type' [[Target Entity]] (context)
```

| Part | Required | Description |
|------|----------|-------------|
| `relation_type` | Yes | Single unquoted token before `[[`, or quoted text for multi-word labels. |
| `[[Target]]` | Yes | Wiki link to the target entity. Matched by title or permalink. |
| `(context)` | No | Parenthesized text after `]]`. Supporting details. |

### Examples

Explicit relations:

```markdown
- implements [[Search Design]]
- depends_on [[Database Schema]]
- works_at [[Y Combinator]] (co-founder)
- "based on" [[Customer Interview]]
- 'in response to' [[Incident Review]]
```

Bare wiki links and prose list items create implicit `links_to` relations:

```markdown
- [[Some Entity]]
- some other thing [[Some Entity]]
```

Both examples above create `links_to [[Some Entity]]`. Use quotes when the words before
`[[` are meant to be a multi-word relation type.

Common relation types:
- `implements`, `depends_on`, `relates_to`, `inspired_by`
- `extends`, `part_of`, `contains`, `pairs_with`
- `works_at`, `authored`, `collaborated_with`

Any single-token text or quoted text works as a relation type. These are conventions,
not a fixed set.

### Inline References

Wiki links appearing in regular prose create implicit `links_to` relations. This includes
list items that do not match the explicit relation grammar above.

```markdown
This builds on [[Core Design]] and uses [[Utility Functions]].
- We should revisit [[Search Design]] after the API changes.
```

This creates three relations: `links_to [[Core Design]]`, `links_to [[Utility Functions]]`,
and `links_to [[Search Design]]`.

### Forward References

Relations can link to entities that don't exist yet. Basic Memory resolves them when the target is created.

## Permalinks and memory:// URLs

Every document has a unique **permalink** — the identifier every relation, `memory://` URI, and
search row binds to. You can set one explicitly in frontmatter, or let the system derive one from
the file path.

```yaml
permalink: auth-approaches-2024
```

An explicit permalink is stored byte-for-byte. A derived one is normalized: Unicode transliterates
to ASCII, case folds down, `_` becomes `-`, and the project slug is prefixed by default. On a
governed project the permalink is **set once** and a move never changes it.

**[IDENTITY.md](IDENTITY.md) is the full contract** — every normalization rule, collision
suffixing, and what happens on a move, each cited to the code.

Permalinks form the basis of `memory://` URLs:

```
memory://auth-approaches-2024        # By permalink
memory://Authentication Approaches   # By title (auto-resolves)
memory://project/auth-approaches     # By path
```

Pattern matching is supported:

```
memory://auth*                       # Starts with "auth"
memory://*/approaches                # Ends with "approaches"
memory://project/*/requirements      # Nested wildcard
```

## Complete Example

```markdown
---
title: Project Ideas
type: note
tags: [ideas, brainstorm]
---

# Project Ideas

## Observations
- [idea] Build a CLI tool for markdown linting #tooling
- [idea] Create a recipe knowledge base #cooking
- [priority] Focus on developer tools first (Q1 goal)

## Relations
- inspired_by [[Developer Workflow Research]]
- part_of [[Q1 Planning]]
```
