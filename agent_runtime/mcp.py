from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .settings import WORKDIR


class MCPClient:
    def __init__(self, server_name: str, command: str, args: list = None, env: dict = None):
        self.server_name = server_name
        self.command = command
        self.args = args or []
        self.env = {**os.environ, **(env or {})}
        self.process = None
        self._request_id = 0
        self._tools = []

    def connect(self):
        try:
            self.process = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=sys.stderr,
                env=self.env,
                text=True,
            )
            self._send({"method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "teaching-agent", "version": "1.0"},
            }})
            response = self._recv()
            if response and "result" in response:
                self._send({"method": "notifications/initialized"})
                return True
        except FileNotFoundError:
            print(f"[\033[31mMCP Error\033[0m] Server command not found: {self.command}")
        except Exception as e:
            print(f"[\033[31mMCP Error\033[0m] Connection failed: {e}")
        return False

    def list_tools(self) -> list:
        self._send({"method": "tools/list", "params": {}})
        response = self._recv()
        if response and "result" in response:
            self._tools = response["result"].get("tools", [])
        return self._tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        self._send({"method": "tools/call", "params": {"name": tool_name, "arguments": arguments}})
        response = self._recv()
        if response and "result" in response:
            content = response["result"].get("content", [])
            return "\n".join(c.get("text", str(c)) for c in content)
        if response and "error" in response:
            return f"MCP Error: {response['error'].get('message', 'unknown')}"
        return "MCP Error: no response"

    def get_agent_tools(self) -> list:
        agent_tools = []
        for tool in self._tools:
            prefixed_name = f"mcp__{self.server_name}__{tool['name']}"
            agent_tools.append({
                "type": "function",
                "function": {
                    "name": prefixed_name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                },
            })
        return agent_tools

    def disconnect(self):
        if self.process:
            try:
                self._send({"method": "shutdown"})
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
            self.process = None

    def _send(self, message: dict):
        if not self.process or self.process.poll() is not None:
            return
        self._request_id += 1
        envelope = {"jsonrpc": "2.0", "id": self._request_id, **message}
        try:
            self.process.stdin.write(json.dumps(envelope) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _recv(self) -> dict | None:
        if not self.process or self.process.poll() is not None:
            return None
        try:
            line = self.process.stdout.readline()
            if line:
                return json.loads(line)
        except (json.JSONDecodeError, OSError):
            pass
        return None


class PluginLoader:
    def __init__(self, search_dirs: list = None):
        self.search_dirs = search_dirs or [WORKDIR]
        self.plugins = {}

    def scan(self) -> list:
        found = []
        for search_dir in self.search_dirs:
            plugin_dir = Path(search_dir) / ".claude-plugin"
            manifest_path = plugin_dir / "plugin.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text())
                    name = manifest.get("name", plugin_dir.parent.name)
                    self.plugins[name] = manifest
                    found.append(name)
                except (json.JSONDecodeError, OSError) as e:
                    print(f"[Plugin] Failed to load {manifest_path}: {e}")
        return found

    def get_mcp_servers(self) -> dict:
        servers = {}
        for plugin_name, manifest in self.plugins.items():
            for server_name, config in manifest.get("mcpServers", {}).items():
                servers[f"{plugin_name}-{server_name}"] = config
        return servers


class MCPToolRouter:
    def __init__(self):
        self.clients = {}

    def register_client(self, client: MCPClient):
        self.clients[client.server_name] = client

    def is_mcp_tool(self, tool_name: str) -> bool:
        return tool_name.startswith("mcp__")

    def call(self, tool_name: str, arguments: dict) -> str:
        parts = tool_name.split("__", 2)
        if len(parts) != 3:
            return f"Error: Invalid MCP tool name: {tool_name}"
        _, server_name, actual_tool = parts
        client = self.clients.get(server_name)
        if not client:
            return f"Error: MCP server not found: {server_name}"
        return client.call_tool(actual_tool, arguments)

    def get_all_tools(self) -> list:
        tools = []
        for client in self.clients.values():
            tools.extend(client.get_agent_tools())
        return tools


mcp_router = MCPToolRouter()
plugin_loader = PluginLoader()
