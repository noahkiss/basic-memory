<!-- mcp-name: io.github.noahkiss/basic-memory -->
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![](https://badge.mcpx.dev?type=server 'MCP Server')
![](https://badge.mcpx.dev?type=dev 'MCP Dev')

# Basic Memory

### Your AI never forgets again.

Pick up right where you left off — in Claude, Codex, Cursor, ChatGPT, or
anything that speaks [MCP](https://modelcontextprotocol.io). Your knowledge
lives as Markdown files that both you and your AI can read, write, and
search.

- **Local-first.** Plain text on your disk. Forever.
- **Two-way.** AI and humans write to the same files; sync keeps them in step.
- **A real knowledge graph.** Observations and wikilinks compound into context.
- **Semantic search.** Find notes by meaning, not just keywords.
- **MCP-native.** Works with every major AI client and IDE.
- **Progressive tool discovery.** Every tool is tagged with behavior hints
  (read-only, destructive, idempotent) so agents pick the right tool on
  demand — no wasted context trying things to see what they do.

## Get started

**2 minutes.** Install, configure your AI client, run.

- Free forever (AGPL-3.0)
- All data on your disk
- Air-gapped friendly

Install from the Homebrew tap:

```bash
brew install noahkiss/tap/basic-memory
```

Upgrade later with `brew upgrade basic-memory`.

Without the tap, install from the repository with
[`uv`](https://docs.astral.sh/uv/) — append `@<tag>` or `@<sha>` to pin, or omit
it to track `main`:

```bash
uv tool install git+https://github.com/noahkiss/basic-memory@v0.1.0
```

This fork is not published to any package index. `uv tool install basic-memory`
installs the *upstream* project of the same name, not this one.

[**Configure your client ↓**](#connect-your-ai-client)

## Works with the tools you already use

| Client | Transport | Notes |
|---|---|---|
| [Claude Desktop](#claude-desktop) | stdio/https | macOS / Windows / Linux |
| [Claude Code](#claude-code) | stdio/https | `claude mcp add` |
| [Codex](#codex-cli) | stdio/https | OpenAI's coding agent |
| [Cursor](#cursor) | stdio/https | `.cursor/mcp.json` |
| [VS Code](#vs-code) | stdio/https | Native MCP support |
| [ChatGPT](#chatgpt) | https | Custom GPT actions (`search` / `fetch`) |
| [Obsidian](#obsidian) | — | Reads/writes the same Markdown directly |
| Anything MCP | stdio/https | If it speaks MCP, it works |

## Working with records

A record is one note with a type, an id of its own, and a file named after that
id. `bm` is the short name for `basic-memory`; both run the same commands.

| Command | What it does |
|---|---|
| `bm new <type> "<title>"` | Write a record and print its id. `--rel type:id` links it to a record the project already holds, repeatably. A type this project does not declare is filed as an inbox note proposing it — unless the project declares no `inbox` type, when the write is refused and says so. `--body -` reads the body from stdin, which is what a body holding backticks or `$(` needs (below). |
| `bm ls` | List records: id, type, status, title. Filter with `--type`, `--status`, `--area`. A record some other record supersedes reads `superseded` in the status column. |
| `bm show <id>` | Print a record's file, exactly as it is on disk. |
| `bm path <id>` | Print a record's file path and nothing else, for `$EDITOR "$(bm path <id>)"`. |
| `bm edit <id>` | Change a record's title, body or declared fields. Every type takes an edit — a task in any status, `open` through `done` included — except a finding, which is evidence: correct one by writing its successor with `bm new finding --supersedes <id>`, or pass `--override` to rewrite it in place (the store's history keeps the old text). `--body` replaces the prose and keeps the record's `## Relations` section. `--set name=value` writes a field the project declares, on a profile. `--rel type:id` adds a link to another record, and on its own works on every type. `status` never moves here — that is `bm mark` and `bm done`. |
| `bm mark <id> <status>` | Set a task's status: `open`, `doing`, `blocked`, `shelved`, `done`, or `dropped`. `shelved` parks a task — `bm brief` counts it instead of listing it, and `bm mark <id> open` revives it. |
| `bm done <id>` | Close a task. Exactly `bm mark <id> done`. |
| `bm types` | Show the types, statuses, areas, and relation types this project allows. |
| `bm brief` | Print what is open, as a session-start orientation (below). |
| `bm mine "<text>"` | Find where something was said in this project's Claude Code transcripts. |
| `bm history dirty` | List note files whose changes are not recorded yet. |
| `bm history commit` | Record note changes that `bm` did not make itself. |
| `bm undo` | Put the note store back to the content it held before its last change. |
| `bm doctor` | Check the notes against the index and report what needs a person. Exits 1 when integrity found something; hygiene alone exits 0, `--strict` exits 1 on either. |
| `bm status` | Report what is indexed and what is not. |
| `bm project list` | List projects. `project info` describes one. |
| `bm project add <name>` | Create a project, homed in the store. `--governed` turns the schema checks on; `--here` also writes a `.bm.yml` in the current directory pointing at it, and `--only-here` keeps that marker from covering subdirectories. |
| `bm project mark [<name>]` | Point the current directory at a project by writing its `.bm.yml`. With no name it refreshes the marker that is already there, which is how a marker written before `bm` recorded ids gains one. `--only-here` writes `scope: here`, so subdirectories do not inherit the marker. |

### A body with backticks or `$(`

The shell expands `` `code` `` and `$(…)` before `bm` ever starts, so a body
typed inline arrives with its code spans gone or replaced by command output.
`bm` cannot prevent that — it is downstream of it — so read the body from stdin
with a quoted heredoc instead:

```bash
bm new finding "title" --body - <<'EOF'
body with `code` and $(literal)
EOF
```

Quote the delimiter. `<<'EOF'` passes the text through untouched; `<<EOF`
expands it exactly as double quotes would. The same form works for
`bm edit <id> --body -`. When a body that arrived on the command line still
carries `$(` or an unpaired backtick, the write prints a notice saying so.

### Dates on a record

A record that carries a date carries the two fields that say where the date came
from, so a guess is never mistaken for a fact.

| Flag | On | What it means |
|---|---|---|
| `--opened YYYY-MM-DD` | task | The day the task was opened. |
| `--event-date YYYY-MM-DD` | finding | The day the thing you learned happened. |
| `--review-by YYYY-MM-DD` | finding, guide | The day it needs a second look. Defaults to the project's `review_months` out from today. |
| `--date-source` | task, finding | How you know the date: `inline`, `transcript`, `git`, `mtime`, `inferred`. **Required whenever you state a date.** |
| `--date-confidence` | task, finding | How precise it is: `exact`, `day`, `month`, `unknown`. Defaults to `day`. |
| `--date-ref` | task, finding | The evidence: a commit sha for `git`, `<session-id>#L<line>` for `transcript`. Required for those two rungs, refused for the rest. |

State the real date when you know it:

```bash
bm new finding "Backups failed under the memory limit" \
    --event-date 2026-08-05 --date-source inline --source "NOTES.md#L12-L20"
```

With no date flag the record gets today's date declared `date-source: inferred`,
which `bm doctor` reports for review. That is deliberate: bm read the date off a
clock, not out of your source, and the record says so. `bm new` refuses a date
flag the record's type does not carry — a guide, a state, and an inbox note have
no date field at all.

Notes live under `<data dir>/store`, which is a local git repository — see
[Reading a project's files without running `bm`](#reading-a-projects-files-without-running-bm)
for how `bm` resolves the data dir. Every write there is committed, which is what
gives `bm undo` something to put back. A project you added with a path of its own
still works; it keeps no history, and says so on each write.

Which project a command uses: `--project`, then the nearest `.bm.yml` above the
working directory — skipping any that carries `scope: here`, which counts only
in its own directory. A read with neither covers every project; a write with
neither goes to the default project.

### Reading a project's files without running `bm`

A `.bm.yml` carries three keys — `project:`, the name; `id:`, the project's
store directory; and an optional `scope: here`, which stops the marker covering
subdirectories. Any `bm` command costs at least 0.15 s, which is too slow for a
statusline, so a script reads the store directly instead:

```bash
if [ -n "$BASIC_MEMORY_CONFIG_DIR" ]; then
    root="$BASIC_MEMORY_CONFIG_DIR"
elif [ -n "$XDG_CONFIG_HOME" ]; then
    root="$XDG_CONFIG_HOME/basic-memory"
else
    root="$HOME/.basic-memory"
fi
id=$(sed -n 's/^id: //p' .bm.yml)
cat "$root/store/$id/headline.md"
```

`bm` resolves the store root the same way, in that order — note that
`XDG_CONFIG_HOME` counts only when it is set, and does not fall back to
`~/.config`. Do not read the `projects` map in `config.json`: it is legacy import
state, not the registry.

A marker written before `bm` recorded ids carries `project:` alone. Run
`bm project mark` in that directory once to fill the `id:` in.

## Session briefings

`bm brief` prints a session-start orientation. Its sections come from each
project's own record vocabulary, so a type you add shows up and a type you remove
does not: open tasks, current state, findings whose review-by has passed, and a
count of the unfiled inbox. Guides and profiles get no rows — they are consulted
on demand, and listing them would make the brief a table of contents.

It reads every project unless `--project` or a `.bm.yml` above the working
directory pins one. When nothing is open it says so in one line
(`nothing open in 'myproject'`); use `--verbose` to see on stderr why a brief
came back empty.

`bm brief --query "<text>"` searches instead, printing one line per hit —
permalink and title, never note content. It prints at most five hits, and the
count is the corpus's rather than the cap's: forty matches head the list
`Matches for "x" (40, showing 5)` and close it with `40 results, showing 5`. A
search that matches nothing says `0 results`.

Wire it into your agent by hand; this fork ships no plugin package. For Claude
Code, add it as a `SessionStart` hook in `~/.claude/settings.json`.

Every command that reads a project ends by naming what needs attention — records
that break the vocabulary, reviews that expired, an unfiled inbox, uncommitted
note files — with the command that answers it. At most two lines, after the
payload. `--quiet` drops them. A project whose `vocabulary.yml` cannot be parsed
is named there too, with its file: its records are left out of the counts, and
the other projects still report theirs.

## The board in a browser

`bm web` serves a read-only board over every project on this machine, at
`http://127.0.0.1:2749/`. The front page is one card per project: its headline,
a stacked bar of its statuses, its counts, and the records that are `doing` or
`blocked` — the only ones worth naming when forty projects are on screen at
once.

Pick a project and you get its lane: one column per status in that project's own
vocabulary order, task and plan cards (a plan is badged), and a collapsed list of
everything else grouped by type. Click a card to read the record — its id, type,
status and project as a strip of chips, the body rendered, its relations linked,
and the full frontmatter one fold below. There is a search box, and the board
refreshes itself every 30 seconds.

Nothing on it writes. Marking a card is still `bm mark <id> <status>`; the board
picks the change up on its next refresh.

```bash
bm web                  # foreground, ctrl-c to stop
bm web install          # systemd user unit, enabled and started
bm web install --print  # show the unit, write nothing
bm web uninstall
```

`install` writes `bm-web.service` under `$XDG_CONFIG_HOME/systemd/user` (or
`~/.config/systemd/user`) and bakes `--port` into it. It is systemd-only; on
other platforms run `bm web` in the foreground.

The server binds localhost and has no authentication, because it shows every
project on the machine. Reaching it from anywhere else is the operator's job —
put a reverse proxy or a tunnel in front of it, and use `--host` only when you
mean to.

## Pick up where you left off

https://github.com/user-attachments/assets/a55d8238-8dd0-454a-be4c-8860dbbd0ddc

## Connect your AI client

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "basic-memory",
      "args": ["mcp"]
    }
  }
}
```

Restart Claude Desktop. Notes live in `~/basic-memory` by default.

<details>
<summary><b>Claude Code, Codex CLI, Cursor, VS Code, ChatGPT, Obsidian</b></summary>

### Claude Code

```bash
claude mcp add basic-memory -- basic-memory mcp
```

For session-start briefings, see [Session briefings](#session-briefings).

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.basic-memory]
command = "basic-memory"
args = ["mcp"]
```

Codex can keep its default MCP approval behavior, or you can pre-approve eligible
Basic Memory tools by adding this server-scoped setting to the same table:

```toml
[mcp_servers.basic-memory]
command = "basic-memory"
args = ["mcp"]
default_tools_approval_mode = "approve"
```

This does not disable Codex approvals globally or expand which Basic Memory
projects the server can access. Codex still requires approval for tools that
advertise a destructive annotation, including Basic Memory's writes, edits, and
deletes.

### Cursor

Add to `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "basic-memory",
      "args": ["mcp"]
    }
  }
}
```

### VS Code

Add to your User Settings (JSON):

```json
{
  "mcp": {
    "servers": {
      "basic-memory": {
        "command": "basic-memory",
        "args": ["mcp"]
      }
    }
  }
}
```

### ChatGPT

Basic Memory exposes OpenAI-compatible `search` and `fetch` tools for Custom
GPT actions. Point the action at the MCP server over https and ChatGPT can
read your notes.

### Obsidian

No setup. Point Obsidian at `~/basic-memory` (or your project folder) and the
same wikilinks, frontmatter, and Markdown your AI writes appear in your graph
view. Edit either side — sync handles the rest.

</details>

Try a prompt:

```
"Create a note about our project architecture decisions."
"Find information about JWT auth in my notes."
"What have I been working on this week?"
```

## Why Basic Memory

Most LLM conversations are ephemeral. You ask a question, get an answer, then
everything is forgotten. Workarounds have limits:

- **Chat history** captures conversations but isn't structured knowledge.
- **RAG** lets the LLM query your documents but not write back to them.
- **Vector DBs** need complex infra and usually live in someone else's cloud.
- **Knowledge graphs** need specialized tooling to maintain.

Basic Memory takes a simpler path: **structured Markdown files that humans
and LLMs both read and write.**

- All knowledge stays in plain files you control.
- Both sides read and write to the same files.
- Familiar Markdown with semantic patterns — no new format to learn.
- A traversable graph the LLM can follow link by link.
- Works with the editors you already use (Obsidian, VS Code, anything).
- Just files plus a local SQLite index. No servers required.

## How it works

You're chatting normally about coffee:

> I've been experimenting with brewing methods. Pour over gives more clarity
> than French press, water at 205°F seems best, and freshly ground beans
> make a huge difference.

Ask the LLM to capture it:

> "Make a note on coffee brewing methods."

A Markdown file appears in your project directory in real time:

```markdown
---
title: Coffee Brewing Methods
permalink: coffee-brewing-methods
tags: [coffee, brewing]
---

# Coffee Brewing Methods

## Observations
- [method] Pour over highlights subtle flavors over body
- [technique] Water at 205°F (96°C) extracts optimal compounds
- [principle] Freshly ground beans preserve aromatics

## Relations
- relates_to [[Coffee Bean Origins]]
- requires [[Proper Grinding Technique]]
- affects [[Flavor Extraction]]
```

Next session, the LLM picks up the thread. It follows the relations to
surface what you already know about Ethiopian beans and burr grinders, and
builds on it instead of starting over. You see the same files in Obsidian or
your editor. Edit them by hand — the AI sees your changes too.

Real two-way flow: humans edit Markdown, LLMs read/write through MCP, sync
keeps everything consistent, and the source of truth is always your files.

## The Markdown format

Each file is an `Entity`. Entities have `Observations` (facts about them) and
`Relations` (links to other entities). That's the whole grammar.

### Frontmatter

```markdown
---
title: <Entity title>
type: note
permalink: <uri-slug>
tags: [optional, list]
---
```

### Observations

Facts about the entity. Categories in `[brackets]`, tags with `#`, optional
context in parens.

```markdown
- [method] Pour over highlights subtle flavors
- [tip] Grind medium-fine for V60 #brewing
- [fact] Lighter roasts contain more caffeine than dark
- [resource] James Hoffmann's V60 technique on YouTube
- [question] How does temperature affect compound extraction?
```

### Relations

Wiki-style links that form the graph. Single-token relation types, or quote
multi-word ones.

```markdown
- pairs_well_with [[Chocolate Desserts]]
- grown_in [[Ethiopia]]
- requires [[Burr Grinder]]
- "pairs well with" [[Dark Chocolate]]
```

Bare `- [[Target]]` and prose `- Worth checking out [[Target]]` index as
`links_to`. Full reference in [docs/NOTE-FORMAT.md](docs/NOTE-FORMAT.md).

## MCP tools

Basic Memory exposes these tools to any MCP client. Every tool is annotated
with MCP behavior hints (read-only, destructive, idempotent, open-world) so
agents can pick the right one without trial-and-error:

- **Content:** `write_note`, `read_note`, `edit_note`, `move_note`,
  `delete_note`, `read_content`, `view_note`
- **Search & discovery:** `search`, `search_notes`, `recent_activity`,
  `list_directory`
- **Knowledge graph:** `build_context` (navigates `memory://` URLs)
- **Projects:** `list_memory_projects`, `create_memory_project`,
  `get_current_project`, `sync_status`
- **Schema:** `schema_infer`, `schema_validate`, `schema_diff`

All MCP tools default to text output; pass `output_format="json"` for
structured responses. Exact signatures live in
[`src/basic_memory/mcp/tools/`](src/basic_memory/mcp/tools/).

## CLI essentials

```bash
# Projects
basic-memory project list
basic-memory project add research ~/research

# Config
basic-memory config list                        # all settings, effective values, env overrides
basic-memory config set cli_output_style plain  # validated through the config model
basic-memory config unset cli_output_style      # revert to default

# Board in a browser (read-only)
basic-memory web                 # serve on 127.0.0.1:2749
basic-memory web install         # install and start the systemd user unit

# Health & maintenance
basic-memory status
basic-memory doctor              # check your notes; --self-test checks the install
basic-memory tool edit-note ...  # CLI access to MCP tools
```

`basic-memory --help` (or `--help` on any subcommand) is the full CLI
reference.

## Logging

Basic Memory uses [Loguru](https://github.com/Delgan/loguru). Defaults vary
by entry point:

| Entry point | Default | Why |
|---|---|---|
| CLI commands | File only | Doesn't interfere with command output |
| MCP server | File only | Stdout would corrupt JSON-RPC |
| API server | File only | Same sink as the CLI |

Log file: `<data dir>/basic-memory.log` (10MB rotation, 10 days retention).
`bm` resolves the data dir as shown in
[Reading a project's files without running `bm`](#reading-a-projects-files-without-running-bm).

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `BASIC_MEMORY_LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |
| `BASIC_MEMORY_ENV` | `dev` | Set to `test` for test mode (stderr only) |

```bash
BASIC_MEMORY_LOG_LEVEL=DEBUG basic-memory reindex
tail -f "$root/basic-memory.log"   # $root from that same block
```

## Development

SQLite is the only database backend; the test suite needs no external services.

```bash
just install            # uv sync (dev dependencies included)
just test               # Unit + integration (semantic and benchmark excluded)
just test-unit-sqlite   # Unit tests
just test-int-sqlite    # Integration tests (semantic and benchmark excluded)
just fast-check         # fix/format/typecheck (no tests)
just fast-test          # Impacted tests (pytest-testmon)
just gate               # lint + typecheck + unit tests (pre-push gate)
just doctor             # File <-> DB self-test (temp config)
just lint               # ruff check --fix
just typecheck          # ty (primary)
just typecheck-pyright  # Pyright (supplemental)
just format             # ruff format
just check              # lint + format + typecheck
just migration "msg"    # New Alembic migration
just release v0.23.0    # Cut a release tag (publishes nothing)
```

Tests use pytest markers: `benchmark`, `slow`, `windows`, `smoke`, `semantic` —
declared in [pyproject.toml](pyproject.toml). `just test-benchmark`,
`just test-semantic`, and `just test-windows` run the excluded sets.

## License

[AGPL-3.0](LICENSE).

A hard fork of [Basic Machines' basic-memory](https://github.com/basicmachines-co/basic-memory),
maintained at [noahkiss/basic-memory](https://github.com/noahkiss/basic-memory). It does not
track upstream.
