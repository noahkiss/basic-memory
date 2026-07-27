<!-- mcp-name: io.github.basicmachines-co/basic-memory -->
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![PyPI version](https://badge.fury.io/py/basic-memory.svg)](https://badge.fury.io/py/basic-memory)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/basicmachines-co/basic-memory/workflows/Tests/badge.svg)](https://github.com/basicmachines-co/basic-memory/actions)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![](https://badge.mcpx.dev?type=server 'MCP Server')
![](https://badge.mcpx.dev?type=dev 'MCP Dev')
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/basicmachines-co/basic-memory)

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
- Requires Python via [`uv`](https://docs.astral.sh/uv/)

```bash
uv tool install basic-memory
```

[**Configure your client ↓**](#connect-your-ai-client)

## What people are saying

> Basic Memory changed my whole relationship with LLMs. I switched from GPT
> and Gemini to exclusively Claude and Claude Code because of this
> integration and am completely revamping all our company's processes around
> a Basic Memory workflow.
>
> — **Alex**, TrainerDay

> Basic Memory is the missing 'wow' factor in AI chatbots. Now I can't
> imagine Claude or Claude Code without it.
>
> — **Caleb**, Caleb Picker Consulting

> I don't code without Basic Memory anymore. It's such a time saver to be
> able to refer to projects I don't currently have active and keep a running
> log of all my learnings and ProTips.
>
> — **@groksrc**, Developer

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

## Session briefings

`bm brief` prints a session-start orientation — open tasks, open decisions, and
recent sessions. Wire it into your agent by hand; this fork ships no plugin
package. For Claude Code, add it as a `SessionStart` hook in
`~/.claude/settings.json`.

## Pick up where you left off

https://github.com/user-attachments/assets/a55d8238-8dd0-454a-be4c-8860dbbd0ddc

## Connect your AI client

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "uvx",
      "args": ["basic-memory", "mcp"]
    }
  }
}
```

Restart Claude Desktop. Notes live in `~/basic-memory` by default.

<details>
<summary><b>Claude Code, Codex CLI, Cursor, VS Code, ChatGPT, Obsidian</b></summary>

### Claude Code

```bash
claude mcp add basic-memory -- uvx basic-memory mcp
```

For session-start briefings, see [Session briefings](#session-briefings).

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.basic-memory]
command = "uvx"
args = ["basic-memory", "mcp"]
```

Codex can keep its default MCP approval behavior, or you can pre-approve eligible
Basic Memory tools by adding this server-scoped setting to the same table:

```toml
[mcp_servers.basic-memory]
command = "uvx"
args = ["basic-memory", "mcp"]
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
      "command": "uvx",
      "args": ["basic-memory", "mcp"]
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
        "command": "uvx",
        "args": ["basic-memory", "mcp"]
      }
    }
  }
}
```

### ChatGPT

Basic Memory exposes OpenAI-compatible `search` and `fetch` tools for Custom
GPT actions. See the [ChatGPT integration
guide](https://docs.basicmemory.com/integrations/chatgpt/?utm_source=github&utm_medium=referral&utm_campaign=readme).

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

## What's New

- **Automatic updates.** Basic Memory keeps itself up to date for `uv tool`
  and Homebrew installs; `bm update` triggers a manual check.
- **Semantic vector search.** Find notes by meaning, not just keywords.
  Hybrid full-text + vector ranking with FastEmbed embeddings, on SQLite or
  Postgres.
- **Schema system.** Infer, validate, and diff the structure of your
  knowledge base with `schema_infer`, `schema_validate`, `schema_diff`.
- **Smarter editing.** `edit_note` append/prepend auto-creates notes when
  missing; `write_note` guards against accidental overwrites.
- **Richer search results.** Matched chunk text is included so the LLM gets
  context, not just hits.
- **FastMCP 3.0 + tool annotations.** Every tool ships with MCP behavior
  hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`,
  `openWorldHint`) so agents can discover capabilities progressively at
  runtime instead of guessing or burning tokens.
- **CLI overhaul.** `--json` output for scripting and an htop-inspired
  project dashboard.

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
`links_to`. Full reference in the
[docs](https://docs.basicmemory.com/concepts/knowledge-format?utm_source=github&utm_medium=referral&utm_campaign=readme).

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
structured responses. Full tool reference in the
[docs](https://docs.basicmemory.com/?utm_source=github&utm_medium=referral&utm_campaign=readme).

## CLI essentials

```bash
# Projects
basic-memory project list
basic-memory project add research ~/research

# Config
basic-memory config list                        # all settings, effective values, env overrides
basic-memory config set cli_output_style plain  # validated through the config model
basic-memory config unset cli_output_style      # revert to default

# Health & maintenance
basic-memory status
basic-memory doctor              # file <-> DB consistency check
basic-memory tool edit-note ...  # CLI access to MCP tools
basic-memory update              # check for and install updates

# Imports
basic-memory import claude conversations
basic-memory import chatgpt
basic-memory import memory-json
```

Full CLI reference in the
[docs](https://docs.basicmemory.com/reference/cli-reference?utm_source=github&utm_medium=referral&utm_campaign=readme).

## Auto-updates

CLI installs check for updates every 24 hours by default and apply them
silently (so the MCP server keeps responding).

- Supported install sources: `uv tool`, Homebrew
- Skipped for `uvx` (ephemeral runtime managed by uv)
- Manual: `bm update` (check + apply) or `bm update --check` (check only)

Disable in `~/.basic-memory/config.json`:

```json
{ "auto_update": false }
```

## Logging

Basic Memory uses [Loguru](https://github.com/Delgan/loguru). Defaults vary
by entry point:

| Entry point | Default | Why |
|---|---|---|
| CLI commands | File only | Doesn't interfere with command output |
| MCP server | File only | Stdout would corrupt JSON-RPC |
| API server | File only | Same sink as the CLI |

Log file: `~/.basic-memory/basic-memory.log` (10MB rotation, 10 days
retention).

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `BASIC_MEMORY_LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |
| `BASIC_MEMORY_ENV` | `dev` | Set to `test` for test mode (stderr only) |
| `BASIC_MEMORY_IMPORT_UPLOAD_MAX_BYTES` | `104857600` | Max uploaded import size |

```bash
BASIC_MEMORY_LOG_LEVEL=DEBUG basic-memory reindex
tail -f ~/.basic-memory/basic-memory.log
```

## Development

Basic Memory supports SQLite (default, fast, no Docker) and Postgres
(via testcontainers — Docker required).

```bash
just install          # Install with dev dependencies
just test-sqlite      # All tests, SQLite
just test-postgres    # All tests, Postgres (testcontainers)
just test             # Both backends
just fast-check       # fix/format/typecheck + impacted tests
just gate             # lint + typecheck + unit tests (pre-push gate)
just doctor           # File <-> DB consistency check (temp config)
just lint
just typecheck        # Pyright (primary)
just typecheck-ty     # ty (supplemental)
just format
just check            # All quality checks
just migration "msg"  # New Alembic migration
just release v0.23.0  # Cut a release tag (publishes nothing)
```

Tests use pytest markers: `windows`, `benchmark`, `smoke`. See
[justfile](justfile) for the full list.

## License

[AGPL-3.0](LICENSE).

## Star History

<a href="https://www.star-history.com/#basicmachines-co/basic-memory&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=basicmachines-co/basic-memory&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=basicmachines-co/basic-memory&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=basicmachines-co/basic-memory&type=Date" />
 </picture>
</a>

Built with ♥️ by [Basic Machines](https://basicmachines.co?utm_source=github&utm_medium=referral&utm_campaign=readme)
