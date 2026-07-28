# Docker Setup Guide

Basic Memory can be run in Docker containers to provide a consistent, isolated environment for your knowledge management
system. This is particularly useful for integrating with existing Dockerized MCP servers or for deployment scenarios.

## Quick Start

This fork publishes no images anywhere — you build from this checkout.

### Option 1: Using Docker Compose (Building Locally)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/noahkiss/basic-memory.git
   cd basic-memory
   ```

2. **Update the docker-compose.yml:**
   Edit the volume mount to point to your Obsidian vault:
   ```yaml
   volumes:
     # Change './obsidian-vault' to your actual directory path
     - /path/to/your/obsidian-vault:/app/data:rw
   ```

3. **Start the container:**
   ```bash
   docker-compose up -d
   ```

### Option 2: Using Docker CLI

```bash
# Build the image
docker build -t basic-memory .

# Run with volume mounting
docker run -d \
  --name basic-memory-server \
  -v /path/to/your/obsidian-vault:/app/data:rw \
  -v basic-memory-config:/app/.basic-memory:rw \
  -e BASIC_MEMORY_DEFAULT_PROJECT=main \
  basic-memory
```

## Configuration

### Volume Mounts

Basic Memory requires several volume mounts for proper operation:

1. **Knowledge Directory** (Required):
   ```yaml
   - /path/to/your/obsidian-vault:/app/data:rw
   ```
   Mount your Obsidian vault or knowledge base directory.

2. **Configuration and Database** (Recommended):
   ```yaml
   - basic-memory-config:/app/.basic-memory:rw
   ```
   Persistent storage for configuration and SQLite database.

You can edit the basic-memory config.json file located in the /app/.basic-memory/config.json after Basic Memory starts.

3. **Multiple Projects** (Optional):
   ```yaml
   - /path/to/project1:/app/data/project1:rw
   - /path/to/project2:/app/data/project2:rw
   ```

You can edit the basic-memory config.json file located in the /app/.basic-memory/config.json

## CLI Commands via Docker

You can run Basic Memory CLI commands inside the container using `docker exec`:

### Basic Commands

```bash
# Check status
docker exec basic-memory-server basic-memory status

# Sync files
docker exec basic-memory-server basic-memory reindex

# Show help
docker exec basic-memory-server basic-memory --help
```

### Managing Projects with Volume Mounts

When using Docker volumes, you'll need to configure projects to point to your mounted directories:

1. **Check current configuration:**
   ```bash
   docker exec basic-memory-server cat /app/.basic-memory/config.json
   ```

2. **Add a project for your mounted volume:**
   ```bash
   # If you mounted /path/to/your/vault to /app/data
   docker exec basic-memory-server basic-memory project create my-vault /app/data
   
   # Set it as default
   docker exec basic-memory-server basic-memory project set-default my-vault
   ```

3. **Sync the new project:**
   ```bash
   docker exec basic-memory-server basic-memory reindex
   ```

### Example: Setting up an Obsidian Vault

If you mounted your Obsidian vault like this in docker-compose.yml:
```yaml
volumes:
  - /Users/yourname/Documents/ObsidianVault:/app/data:rw
```

Then configure it:
```bash
# Create project pointing to mounted vault
docker exec basic-memory-server basic-memory project create obsidian /app/data

# Set as default
docker exec basic-memory-server basic-memory project set-default obsidian

# Sync to index all files
docker exec basic-memory-server basic-memory reindex
```

### Environment Variables

Configure Basic Memory using environment variables:

```yaml
environment:

  # Default project
  - BASIC_MEMORY_DEFAULT_PROJECT=main

  # Enable real-time sync
  - BASIC_MEMORY_SYNC_CHANGES=true

  # Logging level
  - BASIC_MEMORY_LOG_LEVEL=INFO

  # Sync delay in milliseconds
  - BASIC_MEMORY_SYNC_DELAY=1000
```

## File Permissions

### Linux/macOS

The Docker container now runs as a non-root user to avoid file ownership issues. By default, the container uses UID/GID 1000, but you can customize this to match your user:

```bash
# Build with custom UID/GID to match your user
docker build --build-arg UID=$(id -u) --build-arg GID=$(id -g) -t basic-memory .

# Or use docker-compose with build args
```

**Example docker-compose.yml with custom user:**
```yaml
version: '3.8'
services:
  basic-memory:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        UID: 1000  # Replace with your UID
        GID: 1000  # Replace with your GID
    container_name: basic-memory-server
    ports:
      - "8000:8000"
    volumes:
      - /path/to/your/obsidian-vault:/app/data:rw
      - basic-memory-config:/app/.basic-memory:rw
    environment:
      - BASIC_MEMORY_DEFAULT_PROJECT=main
    restart: unless-stopped
```

**Using the default UID/GID:**
If you build without the `UID`/`GID` build args, files will be created with UID/GID 1000. You can either:

1. Change your local directory ownership to match:
   ```bash
   sudo chown -R 1000:1000 /path/to/your/obsidian-vault
   ```

2. Or rebuild with custom UID/GID as shown above.

### Windows

When using Docker Desktop on Windows, ensure the directories are shared:

1. Open Docker Desktop
2. Go to Settings → Resources → File Sharing
3. Add your knowledge directory path
4. Apply & Restart

## Troubleshooting

### Common Issues

1. **File Watching Not Working:**
    - Ensure volume mounts are read-write (`:rw`)
    - Check directory permissions
    - On Linux, may need to increase inotify limits:
      ```bash
      echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf
      sudo sysctl -p
      ```

2. **Configuration Not Persisting:**
    - Use named volumes for `/app/.basic-memory`
    - Check volume mount permissions

3. **Network Connectivity:**
    - For HTTP transport, ensure port 8000 is exposed
    - Check firewall settings

### Debug Mode

Run with debug logging:

```yaml
environment:
  - BASIC_MEMORY_LOG_LEVEL=DEBUG
```

View logs:

```bash
docker-compose logs -f basic-memory
```


## Security Considerations

1. **Docker Security:**
   The container runs as a non-root user (UID/GID 1000 by default) for improved security. You can customize the user ID using build arguments to match your local user.

2. **Volume Permissions:**
   Ensure mounted directories have appropriate permissions and don't expose sensitive data. With the non-root container, files will be created with the specified user ownership.

3. **Network Security:**
   If using HTTP transport, consider using reverse proxy with SSL/TLS and authentication if the endpoint is available on
   a network.

4. **IMPORTANT:** The HTTP endpoints have no authorization. They should not be exposed on a public network.  

## Integration Examples

### Claude Desktop with Docker

The recommended way to connect Claude Desktop to the containerized Basic Memory is using `mcp-proxy`, which converts the HTTP transport to STDIO that Claude Desktop expects:

1. **Start the Docker container:**
   ```bash
   docker-compose up -d
   ```

2. **Configure Claude Desktop** to use mcp-proxy:
   ```json
   {
     "mcpServers": {
       "basic-memory": {
         "command": "uvx",
         "args": [
           "mcp-proxy",
           "http://localhost:8000/mcp"
         ]
       }
     }
   }
   ```


## Support

For Docker-specific issues:

1. Check the [troubleshooting section](#troubleshooting) above
2. Review container logs: `docker-compose logs basic-memory`
3. Verify volume mounts: `docker inspect basic-memory-server`
4. Test file permissions: `docker exec basic-memory-server ls -la /app`

For general Basic Memory support, see the main [README](../README.md).