# Testing

SQLite is the only database backend. There is nothing to start and nothing to configure — the
suite is self-contained.

```bash
just test-sqlite     # everything (tests/ + test-int/)
just test-unit-sqlite
just test-int-sqlite
just fast-test       # only the tests your working tree impacted (pytest-testmon)
uv run pytest tests/path/to/test_file.py::test_function_name
```

## Layout

- `tests/` — unit tests. Each gets a fresh in-memory SQLite database, destroyed after the test.
- `test-int/` — integration tests. Each gets a real on-disk SQLite file under `tmp_path`, so the
  full MCP → FastAPI → repository → database stack runs unmocked.

## Fixtures

The `engine_factory` fixture in each conftest owns the engine and session maker: `tests/conftest.py`
builds an in-memory database, `test-int/conftest.py` builds `tmp_path / "test.db"`. Both create the
ORM schema and then the FTS5 `search_index` virtual table, which SQLAlchemy metadata cannot express.

`app_config` and `config_manager` write an isolated `config.json` under a monkeypatched `HOME`, so
tests never read or mutate the developer's real configuration.

## Markers

Registered in `pyproject.toml` (`[tool.pytest.ini_options] markers`):

- `semantic` — needs the embedding stack (fastembed + sqlite-vec)
- `live` — calls external provider APIs; explicit opt-in only
- `benchmark` — performance measurement, not a correctness assertion
- `slow` — long-running
- `smoke` — fast end-to-end MCP smoke tests
- `windows` — Windows-specific behavior
