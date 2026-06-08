# Filesystem MCP Demo

This directory is a safe local sandbox for the Filesystem MCP server example.

Example MCP server args:

```yaml
args: ["-y", "@modelcontextprotocol/server-filesystem", "./demos/mcp/filesystem-demo"]
allowed_roots: ["./demos/mcp/filesystem-demo"]
```

Only grant directories that should be visible to the MCP server. File write,
delete, overwrite, and patch-like operations require user confirmation.
