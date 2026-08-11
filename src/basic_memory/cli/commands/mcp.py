"""MCP server command with streamable HTTP transport."""

import os
from typing import Any, Optional

import typer
from loguru import logger

from basic_memory.cli.app import app
from basic_memory.config import init_mcp_logging
from basic_memory.project_registry import lookup_project


class _DeferredMcpServer:
    def run(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        from basic_memory.mcp.server import mcp as live_mcp_server

        live_mcp_server.run(*args, **kwargs)


# Keep module-level attribute for tests/monkeypatching while deferring heavy import.
mcp_server = _DeferredMcpServer()


@app.command()
def mcp(
    transport: str = typer.Option("stdio", help="Transport type: stdio, streamable-http, or sse"),
    host: str = typer.Option(
        "0.0.0.0", help="Host for HTTP transports (use 0.0.0.0 to allow external connections)"
    ),
    port: int = typer.Option(8000, help="Port for HTTP transports"),
    path: str = typer.Option("/mcp", help="Path prefix for streamable-http transport"),
    project: Optional[str] = typer.Option(None, help="Restrict the MCP server to one project"),
):  # pragma: no cover
    """Run the MCP server on one of three transports.

    - stdio: standard input and output, for local use
    - streamable-http: the recommended transport for a web deployment
    - sse: server-sent events, for older clients

    The server's own lifespan does the startup, the file indexing, and the cleanup.
    """
    # Import mcp tools/prompts to register them with the server
    import basic_memory.mcp.tools  # noqa: F401  # pragma: no cover
    import basic_memory.mcp.prompts  # noqa: F401  # pragma: no cover
    import basic_memory.mcp.resources  # noqa: F401  # pragma: no cover

    # Initialize logging for MCP (file only, stdout breaks protocol)
    init_mcp_logging()

    # Validate and set project constraint if specified
    if project:
        project_name, _ = lookup_project(project)
        if not project_name:
            typer.echo(f"No project found named: {project}", err=True)
            raise typer.Exit(1)

        # Set env var with validated project name
        os.environ["BASIC_MEMORY_MCP_PROJECT"] = project_name
        logger.info(f"MCP server constrained to project: {project_name}")

    # Run the MCP server (blocks)
    # Lifespan handles: initialization, migrations, file indexing, cleanup
    logger.info(f"Starting MCP server with {transport.upper()} transport")

    if transport == "stdio":
        mcp_server.run(
            transport=transport,
        )
    elif transport == "streamable-http" or transport == "sse":
        mcp_server.run(
            transport=transport,
            host=host,
            port=port,
            path=path,
            log_level="INFO",
        )
