# MCP server setup

Quale exposes its 3 harness-proven commands as MCP tools — typed functions
any MCP-compatible agent can call directly, no shell overhead.

## How it works

Run `quale --mcp` to start the MCP server. It reads JSON-RPC requests from
stdin and writes responses to stdout (stdio transport — zero network, zero
dependencies).

Discovered via `tools/list` on startup. Agent sees 3 tools:

| Tool | Input | Returns |
|------|-------|---------|
| `edit_context` | `{file: str, task?: str, path?: str}` | Risk, verification candidates, scope guard |
| `verify_packet` | `{file: str, diff?: str, path?: str}` | Verification + entangled candidates |
| `orient` | `{path?: str}` | Module map, landmarks, languages |

## OpenCode

Add to `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "quale": {
      "type": "local",
      "command": ["quale", "--mcp"],
      "description": "Structural codebase analysis. 75% test-file accuracy."
    }
  }
}
```

## Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "quale": {
      "command": "quale",
      "args": ["--mcp"]
    }
  }
}
```

## Claude Code

```bash
claude mcp add quale -- pip install quale && quale --mcp
```

## Cursor

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "quale": {
      "type": "local",
      "command": ["quale", "--mcp"]
    }
  }
}
```

## VS Code (Cline / Continue)

Add to your MCP config:

```json
{
  "mcpServers": {
    "quale": {
      "command": "quale",
      "args": ["--mcp"]
    }
  }
}
```

## Measured effect

12-trial comparison (Mistral-7B, 4 conditions):

| Condition | Correct test file | Method |
|-----------|-----------------|--------|
| Baseline (no skill, no MCP) | 2/3 | Agent guesses by naming convention |
| Skill only | 3/3 | `quale ec` via shell |
| MCP only | 2/3 | `edit_context` tool |
| MCP + skill | **3/3** | Typed tool preferred, skill as fallback |

MCP + skill together is the strongest configuration. The agent prefers
the typed `edit_context` tool over shell commands, but the skill ensures
correct behavior even when MCP isn't available.
