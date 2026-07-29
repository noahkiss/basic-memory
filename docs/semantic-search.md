# Semantic Search

This guide covers Basic Memory's semantic (vector) search feature, which adds meaning-based retrieval alongside the existing full-text search.

## Overview

Basic Memory's search supports both full-text search (FTS) and semantic retrieval. Semantic search adds vector embeddings that capture the *meaning* of your content, enabling:

- **Paraphrase matching**: Find "authentication flow" when searching for "login process"
- **Conceptual queries**: Search for "ways to improve performance" and find notes about caching, indexing, and optimization
- **Hybrid retrieval**: Combine the precision of keyword search with the recall of semantic similarity

Semantic search is enabled by default when semantic dependencies are available at runtime.

## Installation

Semantic search dependencies (fastembed, sqlite-vec, openai) are included in the default `basic-memory` install.

```bash
pip install basic-memory
```

You can always override with `BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true|false`.

### Platform Compatibility

| Platform | FastEmbed (local) | OpenAI (API) |
|---|---|---|
| macOS ARM64 (Apple Silicon) | Yes | Yes |
| macOS x86_64 (Intel Mac) | No — see workaround below | Yes |
| Linux x86_64 | Yes | Yes |
| Linux ARM64 | Yes | Yes |
| Windows x86_64 | Yes | Yes |

#### Intel Mac Workaround

The default install includes FastEmbed, which depends on ONNX Runtime. ONNX Runtime dropped Intel Mac (x86_64) wheels starting in v1.24, so install with a compatible ONNX Runtime pin first:

```bash
pip install basic-memory 'onnxruntime<1.24'
```

After installation, Intel Mac users have two runtime options:

**Option 1: Use OpenAI embeddings (recommended)**

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY=sk-...
```

**Option 2: Use FastEmbed locally**

Keep the same pinned installation and use FastEmbed (default provider):

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=fastembed
```

## Quick Start

1. Install Basic Memory:

```bash
pip install basic-memory
```

2. (Optional) Explicitly enable semantic search:

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
```

3. Build vector embeddings for your existing content:

```bash
bm reindex --embeddings
```

4. Search using semantic modes:

```python
# Pure vector similarity
search_notes("login process", search_type="vector")

# Hybrid: combines FTS precision with vector recall (recommended)
search_notes("login process", search_type="hybrid")

# Explicit full-text search
search_notes("login process", search_type="text")
```

## Configuration Reference

All settings are fields on `BasicMemoryConfig` and can be set via environment variables (prefixed with `BASIC_MEMORY_`).

| Config Field | Env Var | Default | Description |
|---|---|---|---|
| `semantic_search_enabled` | `BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED` | Auto (`true` when semantic deps are available) | Enable semantic search. Required before vector/hybrid modes work. |
| `semantic_embedding_provider` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER` | `"fastembed"` | Embedding provider: `"fastembed"` (local) or `"openai"` (any OpenAI-compatible API). |
| `semantic_embedding_model` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL` | `"bge-small-en-v1.5"` | Model identifier. Auto-adjusted per provider if left at default. |
| `semantic_embedding_api_base` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_API_BASE` | Unset | Optional custom endpoint for the `openai` provider — Ollama, LM Studio, vLLM, OpenRouter, or a self-hosted gateway. |
| `semantic_embedding_api_key` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_API_KEY` | Unset | Optional API key passed directly to the `openai` provider. When unset, the provider falls back to `OPENAI_API_KEY`. |
| `semantic_embedding_dimensions` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS` | Provider default | Vector dimensions. 384 for FastEmbed, 1536 for OpenAI. Required for any model whose output size differs from its provider default. |
| `semantic_embedding_batch_size` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_BATCH_SIZE` | `2` | Number of texts to embed per batch. |
| `semantic_embedding_document_prefix` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_PREFIX` | Unset | Optional literal text prefix prepended to indexed document chunks before embedding. |
| `semantic_embedding_query_prefix` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_PREFIX` | Unset | Optional literal text prefix prepended to search queries before embedding. |
| `semantic_vector_k` | `BASIC_MEMORY_SEMANTIC_VECTOR_K` | `100` | Candidate count for vector nearest-neighbour retrieval. Higher values improve recall at the cost of latency. |

## Embedding Providers

### FastEmbed (default)

FastEmbed runs entirely locally using ONNX models — no API key, no network calls, no cost.

- **Model**: `BAAI/bge-small-en-v1.5`
- **Dimensions**: 384
- **Tradeoff**: Smaller model, fast inference, good quality for most use cases

```bash
# Install basic-memory and enable semantic search
pip install basic-memory
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
```

### OpenAI

Uses OpenAI's embeddings API for higher-dimensional vectors. Requires an API key.

- **Model**: `text-embedding-3-small`
- **Dimensions**: 1536
- **Tradeoff**: Higher quality embeddings, requires API calls and an OpenAI key

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY=sk-...
```

### OpenAI-compatible servers

The `openai` provider talks to anything that speaks the OpenAI embeddings API, not just
OpenAI itself. Point `semantic_embedding_api_base` at the server — Ollama, LM Studio, vLLM,
OpenRouter, or a self-hosted gateway — and nothing else has to change:

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=openai
export BASIC_MEMORY_SEMANTIC_EMBEDDING_API_BASE=http://127.0.0.1:11434/v1
export BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL=nomic-embed-text
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS=768
export BASIC_MEMORY_SEMANTIC_EMBEDDING_API_KEY=local-key
```

Basic Memory creates the vector table before the first embedding call, so any model whose
output size is not the 1536-dimensional OpenAI default must set
`BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS` — the schema cannot be inferred from a response
that has not happened yet.

Leave `BASIC_MEMORY_SEMANTIC_EMBEDDING_API_KEY` unset to fall back to `OPENAI_API_KEY`. Local
servers that ignore authentication still need a placeholder value, because the OpenAI SDK
refuses to construct a client without one.

### Role prefixes for asymmetric models

Some retrieval models are asymmetric: indexed passages and search queries must be embedded
differently. Where the model expects literal role text in the input string, set the prefixes:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_PREFIX="title: none | text: "
export BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_PREFIX="task: search result | query: "
```

The document prefix is prepended to indexed chunks during sync/reindex. The query prefix is
prepended to search text for vector and hybrid retrieval. Prefixes work with both the
`fastembed` and `openai` providers.

Models that instead need a provider-side `input_type` request parameter (Cohere v3, NVIDIA NIM
retrieval models) are not supported — that path left with the LiteLLM provider.

When switching providers, models, dimensions, or prefixes, rebuild embeddings:

```bash
bm reindex --embeddings
```

## Search Modes

### `text` (default)

Full-text keyword search using SQLite FTS5. Supports boolean operators (`AND`, `OR`, `NOT`), phrase matching, and prefix wildcards.

```python
search_notes("project AND planning", search_type="text")
```

This is the existing default and does not require semantic search to be enabled.

### `vector`

Pure semantic similarity search. Embeds your query and finds the nearest content vectors. Good for conceptual or paraphrase queries where exact keywords may not appear in the content.

```python
search_notes("how to speed up the app", search_type="vector")
```

Returns results ranked by cosine similarity. Individual observations and relations surface as first-class results, not collapsed into parent entities.

### `hybrid`

Combines FTS and vector results using score-based fusion. This is generally the best mode when you want both keyword precision and semantic recall.

```python
search_notes("authentication security", search_type="hybrid")
```

Score-based fusion uses the formula `max(vec, fts) + bonus * min(vec, fts)` to preserve the dominant signal while rewarding results found by both methods.

### When to Use Which

| Mode | Best For |
|---|---|
| `text` | Exact keyword matching, boolean queries, tag/category searches |
| `vector` | Conceptual queries, paraphrase matching, exploratory searches |
| `hybrid` | General-purpose search combining precision and recall |

## The Reindex Command

The `bm reindex` command rebuilds search indexes without dropping the database.

```bash
# Rebuild everything (FTS + embeddings if semantic is enabled)
bm reindex

# Only rebuild vector embeddings
bm reindex --embeddings

# Only rebuild the full-text search index
bm reindex --search

# Target a specific project
bm reindex -p my-project
```

### When You Need to Reindex

- **Upgrade note**: Migration now performs a one-time automatic embedding backfill on upgrade.
- **Manual enable case**: If you explicitly had `semantic_search_enabled=false` and then turn it on
- **Provider change**: After switching between `fastembed` and `openai`
- **Model change**: After changing `semantic_embedding_model`
- **Dimension change**: After changing `semantic_embedding_dimensions`
- **Literal prefix change**: After changing `semantic_embedding_document_prefix` or `semantic_embedding_query_prefix`

The reindex command shows progress with embedded/skipped/error counts:

```
Project: main
  Building vector embeddings...
  ✓ Embeddings complete: 142 entities embedded, 0 skipped, 0 errors

Reindex complete!
```

## How It Works

### Chunking

Each entity in the search index is split into semantic chunks before embedding:

- **Headers**: Markdown headers (`#`, `##`, etc.) start new chunks
- **Bullets**: Each bullet item (`-`, `*`) becomes its own chunk for granular fact retrieval
- **Prose sections**: Non-bullet text is merged up to ~900 characters per chunk
- **Long sections**: Oversized content is split with ~120 character overlap to preserve context at boundaries

Each search index item type (entity, observation, relation) is chunked independently, so observations and relations are embeddable as discrete facts.

### Deduplication

Each chunk has a `source_hash` (SHA-256 of the chunk text). On re-sync, unchanged chunks skip re-embedding entirely. This makes incremental updates fast — only modified content triggers API calls or model inference.

### Hybrid Fusion

Hybrid search uses score-based fusion to merge FTS and vector results:

1. Run FTS search to get keyword-ranked results; normalize scores to [0, 1]
2. Run vector search to get similarity-ranked results (already [0, 1])
3. For each result, compute: `fused = max(vec_score, fts_score) + 0.3 * min(vec_score, fts_score)`
4. Sort by fused score

The dominant signal (whichever source scored higher) is preserved, and dual-source agreement adds a bonus. Unlike rank-based fusion, this approach retains score magnitude — a strong vector match stays strong even without an FTS hit.

### Observation-Level Results

Vector and hybrid modes return individual observations and relations as first-class search results, not just parent entities. This means a search for "water temperature for brewing" can surface the specific observation about 205°F without returning the entire "Coffee Brewing Methods" entity.

## Vector Storage (SQLite)

- **Vector storage**: [sqlite-vec](https://github.com/asg017/sqlite-vec) virtual table
- **Table creation**: At runtime when semantic search is first used — no migration needed
- **Embedding table**: `search_vector_embeddings` using `vec0(embedding float[N])` where N is the configured dimensions
- **Chunk metadata**: `search_vector_chunks` table stores chunk text, keys, and source hashes

The sqlite-vec extension is loaded per-connection. Vector tables are created lazily on first use.
