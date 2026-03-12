# Integrations: ChatGPT And Claude Desktop

This guide covers two common integration paths:

- Remote transport for ChatGPT custom apps (public endpoint required).
- Local stdio transport for Claude Desktop (local-only, no public endpoint).

## 1. ChatGPT Custom App via Public MCP Endpoint

As of March 11, 2026, OpenAI documentation states:

- You need a **remote/public MCP server URL**.
- `localhost` endpoints are not supported for ChatGPT connector setup.
- HTTPS is required.

For this project, run your server locally and expose it through a separate relay workspace (for example `chunking-ngrok`) or a similar tunnel provider.

### Step A: Harden `chunking` config before exposing anything

Use OAuth for internet-exposed endpoints:

```json
{
  "settings": {
    "MCP_AUTH_MODE": "oauth",
    "MCP_OAUTH_CLIENT_ID": "<strong-client-id>",
    "MCP_OAUTH_CLIENT_SECRET": "<strong-client-secret>",
    "MCP_OAUTH_REDIRECT_ALLOWLIST": [
      "https://chatgpt.com/connector_platform_oauth_redirect"
    ],
    "MCP_ISSUER_URL": "https://<your-public-domain>",
    "MCP_RESOURCE_SERVER_URL": "https://<your-public-domain>",
    "MCP_ENABLE_DNS_REBINDING_PROTECTION": true,
    "MCP_ALLOWED_HOSTS": [
      "127.0.0.1:*",
      "localhost:*",
      "[::1]:*",
      "<your-public-domain>",
      "<your-public-domain>:*"
    ],
    "MCP_ALLOWED_ORIGINS": [
      "http://127.0.0.1:*",
      "http://localhost:*",
      "http://[::1]:*",
      "https://<your-public-domain>"
    ]
  }
}
```

If you keep `MCP_AUTH_MODE="none"` while adding non-local hosts/origins, startup will fail by design.

### Step B: Run local MCP HTTP/SSE server

```powershell
$env:MCP_BIND_HOST = "127.0.0.1"
$env:MCP_BIND_PORT = "8000"
.\.venv\Scripts\python.exe run_mcp_v5_sse_actions.py
```

This server now exposes:

- `http://127.0.0.1:8000/mcp` (streamable HTTP)
- `http://127.0.0.1:8000/sse` + `/messages/` (SSE transport)
- `http://127.0.0.1:8000/token` (when OAuth mode is enabled)

### Step C: Expose local server with relay/tunnel

`ngrok` example from separate folder:

```powershell
cd ..\chunking-ngrok
.\start_ngrok_and_mcp_sse.bat
```

You can use any equivalent tunnel provider. The key requirement is a stable HTTPS public URL mapped to your local MCP endpoint.

### Step D: Connect in ChatGPT

1. In ChatGPT, enable Developer Mode in Apps/Connectors settings.
2. Add a custom connector and enter your public MCP URL.
   Use the transport URL expected by the connector UI (typically `/mcp` for streamable HTTP).
3. Select OAuth and provide your client ID/client secret.
4. If ChatGPT shows a redirect URI, add it to `MCP_OAUTH_REDIRECT_ALLOWLIST`.
5. Save and verify tool calls.

## 2. Claude Desktop via Local MCP stdio

For Claude Desktop, do not expose anything publicly. Use local stdio.

Windows config path:

- `C:\Users\<you>\AppData\Roaming\Claude\claude_desktop_config.json`

Example config:

```json
{
  "mcpServers": {
    "second-brain-v5": {
      "command": "C:\\Users\\<you>\\OneDrive\\Desktop\\chunking\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\Users\\<you>\\OneDrive\\Desktop\\chunking\\run_mcp_v5_stdio.py"
      ]
    }
  }
}
```

Then restart Claude Desktop.

## 3. Security Checklist For Exposed Paths

No public endpoint is "completely secure." The goal is strong practical hardening:

1. Use `MCP_AUTH_MODE="oauth"` when internet-exposed.
2. Keep bind host on loopback (`127.0.0.1`) and expose through relay only.
3. Keep `MCP_ENABLE_DNS_REBINDING_PROTECTION=true`.
4. Restrict `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` to exact domains you use.
5. Use strong random OAuth secrets and rotate them regularly.
6. Prefer short token TTLs (`MCP_OAUTH_TOKEN_TTL_SECONDS`) for exposed deployments.
7. Do not expose stdio transport; keep it local to desktop clients.
8. Keep relay config/secrets outside this repository.
9. Disable tunnel when not actively using remote integration.

## 4. Troubleshooting: `Invalid host header`

If MCP calls (for example `self_check` or `get_issues`) fail with `Invalid host header`, the request `Host` value is not in your `MCP_ALLOWED_HOSTS` list.

Most common case: public tunnel domain (for example ngrok) is not allowed while local hosts are allowed.

Recommended secure fix:

1. Use `MCP_AUTH_MODE="oauth"` for exposed endpoints.
2. Add your public tunnel/domain host to `MCP_ALLOWED_HOSTS`.
3. Add the corresponding `https://` origin to `MCP_ALLOWED_ORIGINS`.
4. Restart `run_mcp_v5_sse_actions.py` after config changes.

Example host/origin additions:

```json
{
  "MCP_ALLOWED_HOSTS": [
    "127.0.0.1:*",
    "localhost:*",
    "[::1]:*",
    "<your-public-domain>",
    "<your-public-domain>:*"
  ],
  "MCP_ALLOWED_ORIGINS": [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
    "https://<your-public-domain>"
  ]
}
```

Important: if `MCP_AUTH_MODE="none"`, non-local hosts/origins are intentionally blocked by startup validation.

## References

- OpenAI: Connect from ChatGPT to a custom MCP server  
  https://developers.openai.com/apps-sdk/build/mcp-server/
- OpenAI: Connectors in ChatGPT (Developer mode, custom connectors)  
  https://help.openai.com/en/articles/11487775-connectors-in-chatgpt
- OpenAI: MCP support in API platform docs  
  https://platform.openai.com/docs/mcp
- Anthropic: MCP configuration (stdio examples)  
  https://docs.anthropic.com/en/docs/claude-code/mcp
- Model Context Protocol: Claude Desktop setup and config guidance  
  https://modelcontextprotocol.io/quickstart/user
