# ATIS SearXNG Test Server

A native Python SearXNG test instance for NORA (Node & Ontology Retrieval Assistant) running on ATIS (Africa Trade Intelligence System).

## Purpose

This repository provides a **temporary, diagnostic-only** SearXNG metasearch engine instance that:

1. Runs natively on Python (no Docker)
2. Operates inside GitHub Codespaces
3. Exposes a JSON API endpoint compatible with NORA
4. Tests whether SearXNG can serve as a reliable intermediary between NORA (on Render) and external search engines

## Architecture

```
NORA / Render
      |
      | HTTPS
      v
SearXNG / GitHub Codespaces
      |
      +--> search engines (wikipedia, wikidata, qwant, etc.)
      |
      v
JSON search results
      |
      v
NORA research pipeline
```

## Prerequisites

- GitHub Codespaces environment
- Python 3.10 or higher (Codespaces provides Python 3.12)
- Git
- pip

## Quick Start

### 1. Create a New Codespace

1. Navigate to this repository on GitHub
2. Click **Code** > **Codespaces** > **Create codespace on main**
3. Wait for the Codespace to initialize

### 2. Run Setup

In the Codespace terminal:

```bash
# Make scripts executable
chmod +x scripts/*.sh

# Run setup (creates venv, installs SearXNG)
./scripts/setup.sh
```

### 3. Start SearXNG

```bash
# Start the SearXNG server
./scripts/start.sh
```

The server will start on port 8888, bound to 0.0.0.0.

### 4. Make Port Public

1. In the Codespace, click on the **Ports** tab
2. Find port **8888** in the list
3. Click the **lock icon** (Private) to change it to **Earth icon** (Public)
4. Note the **Forwarded Address** (e.g., `https://<codespace-name>-8888.githubpreview.dev`)

### 5. Test the Endpoint

From within the Codespace, run the test script:

```bash
# In a new terminal (keep the server running in the first terminal)
./scripts/test.sh
```

From an external machine, test with:

```bash
# Replace with your actual Codespace URL
curl -v "https://<codespace-name>-8888.githubpreview.dev/search?q=African%20Development%20Bank&format=json"
```

## Configuration

### settings.yml

The main configuration file with the following key settings:

- **JSON enabled**: `formats: [html, json]` - Supports `/search?format=json`
- **GET method**: `method: "GET"` - Allows HTTP GET requests (NORA requirement)
- **No authentication**: `limiter: false` - No rate limiting for test purposes
- **Public binding**: `bind_address: "0.0.0.0"` - Accessible through Codespaces forwarding
- **Port**: `port: 8888` - Default port (can be overridden with `$PORT`)

### Enabled Search Engines

The following engines are enabled by default (no API keys required):

| Engine | Shortcut | Category | Notes |
|--------|----------|----------|-------|
| wikipedia | wp | general | Reliable, no API key |
| wikidata | wd | general | Structured knowledge |
| qwant | qw | general, web | General web search |
| searx | sx | general | Built-in fallback |
| mojeek | mjk | general, web | No API key, may have CAPTCHA |

**Note**: Test each engine individually. Disable any that fail in your environment.

## Environment Variables

### For SearXNG Server

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARXNG_PORT` | 8888 | Port to listen on |
| `SEARXNG_BIND_ADDRESS` | 0.0.0.0 | Network interface to bind to |
| `SEARXNG_DEBUG` | false | Debug mode |
| `PORT` | (none) | GitHub Codespaces port (overrides SEARXNG_PORT) |

### For NORA Integration

Set this environment variable in NORA:

```bash
SEARXNG_BASE_URL=https://<codespace-name>-8888.githubpreview.dev
```

NORA should then call:
```
${SEARXNG_BASE_URL}/search?q=<query>&format=json
```

## API Endpoint

### Request Format

```
GET /search?q=<URL-encoded-query>&format=json
```

### Example Request

```bash
curl "http://127.0.0.1:8888/search?q=African%20Development%20Bank&format=json"
```

### Expected Response Structure

```json
{
  "query": "African Development Bank",
  "number_of_results": 10,
  "results": [
    {
      "url": "https://en.wikipedia.org/wiki/African_Development_Bank",
      "title": "African Development Bank - Wikipedia",
      "content": "The African Development Bank is a regional multilateral development finance institution...",
      "engine": "wikipedia",
      "parsed_url": [
        {"name": "scheme", "type": "string", "value": "https"},
        {"name": "netloc", "type": "string", "value": "en.wikipedia.org"},
        {"name": "path", "type": "string", "value": "/wiki/African_Development_Bank"}
      ],
      "engines": ["wikipedia"],
      "positions": [1],
      "score": 2.0
    }
  ]
}
```

**Note**: The exact structure follows the installed SearXNG version.

## Test Queries

### Primary Test Query
```
African Development Bank
```

### Secondary Test Query (NORA's real use case)
```
Zimbabwe Energy Regulatory Authority
```

## Troubleshooting

### Server Won't Start

1. **Check Python version**: Requires Python 3.10+
   ```bash
   python3 --version
   ```

2. **Check virtual environment**:
   ```bash
   ls -la .venv/
   ```

3. **Check installation**:
   ```bash
   source .venv/bin/activate
   pip list | grep searxng
   ```

### No Results Returned

1. **Check engine connectivity**: Some engines may be blocked in Codespaces
2. **Test individual engines**: Edit `settings.yml` and enable one engine at a time
3. **Check logs**: SearXNG logs to stdout by default

### JSON Format Not Working

1. **Verify format in settings**: Ensure `json` is in the `formats` list
2. **Test with curl**:
   ```bash
   curl -H "Accept: application/json" "http://127.0.0.1:8888/search?q=test&format=json"
   ```

### Port Not Accessible

1. **Check binding**: Ensure `bind_address: "0.0.0.0"` in settings.yml
2. **Check port**: Ensure port 8888 is forwarded in Codespaces
3. **Check firewall**: Codespaces should allow outbound connections

## Engine Testing & Validation

### Test Individual Engines

Edit `settings.yml` to enable only one engine at a time, then test:

```yaml
engines:
  - name: wikipedia
    engine: wikipedia
    disabled: false
    # ... other engines disabled
```

### Known Engine Status

| Engine | API Key Required | Status | Notes |
|--------|------------------|--------|-------|
| wikipedia | No | ✅ Working | Reliable, recommended |
| wikidata | No | ✅ Working | Structured data |
| qwant | No | ✅ Working | General web |
| mojeek | No | ⚠️ CAPTCHA | May have PoW CAPTCHA |
| duckduckgo | No | ❌ Network issues | May fail from Render |
| google | Yes | ❌ Requires API key | Not enabled |
| bing | Yes | ❌ Requires API key | Not enabled |

## Performance Considerations

- **Timeout**: Default request timeout is 10 seconds (configurable in `outgoing.request_timeout`)
- **Concurrent connections**: Limited to 100 (`outgoing.pool_connections`)
- **Rate limiting**: Disabled for testing (`server.limiter: false`)

## Security Notes

⚠️ **IMPORTANT**: This is a **temporary, public** test endpoint.

- **No authentication**: Anyone with the URL can query the endpoint
- **No rate limiting**: Consider enabling if exposed to the internet
- **No secrets**: This configuration contains no API keys or credentials
- **Not for production**: This is for diagnostic validation only

## Repository Structure

```
atis-searxng/
├── README.md              # This file
├── settings.yml          # SearXNG configuration
└── scripts/
    ├── setup.sh           # Installation script
    ├── start.sh           # Start server script
    └── test.sh            # Test endpoint script
```

## Cleanup

To remove the installation:

```bash
# Remove virtual environment
rm -rf .venv

# Remove any SearXNG data
rm -rf searxng_data
```

## References

- [SearXNG Official Repository](https://github.com/searxng/searxng)
- [SearXNG Documentation](https://docs.searxng.org/)
- [SearXNG Native Installation Guide](https://docs.searxng.org/admin/installation-searxng.html)
- [SearXNG Configuration Guide](https://docs.searxng.org/admin/settings/index.html)

## License

This repository configuration is provided as-is for testing purposes. SearXNG itself is licensed under AGPL-3.0-or-later.

## Support

For issues with SearXNG itself, refer to:
- [SearXNG Issues](https://github.com/searxng/searxng/issues)
- [SearXNG Documentation](https://docs.searxng.org/)

For issues with this specific configuration, check:
1. The test script output
2. SearXNG server logs
3. Codespaces port forwarding settings
