import json

from .settings import PERMISSION_MODES


class CapabilityPermissionGate:
    """Classify and gate native/MCP tool calls."""

    READ_PREFIXES = ("read", "list", "get", "show", "search", "query", "inspect")
    HIGH_RISK_PREFIXES = ("delete", "remove", "drop", "shutdown")

    def __init__(self, mode: str = "default"):
        self.mode = mode if mode in PERMISSION_MODES else "default"

    def normalize(self, tool_name: str, tool_input: dict) -> dict:
        if tool_name.startswith("mcp__"):
            _, server_name, actual_tool = tool_name.split("__", 2)
            source = "mcp"
        else:
            server_name = None
            actual_tool = tool_name
            source = "native"

        lowered = actual_tool.lower()
        if actual_tool == "read_file" or lowered.startswith(self.READ_PREFIXES):
            risk = "read"
        elif actual_tool == "bash":
            command = tool_input.get("command", "")
            risk = "high" if any(token in command for token in ("rm -rf", "sudo", "shutdown", "reboot")) else "write"
        elif actual_tool == "submit_pipeline":
            risk = "high"
        elif lowered.startswith(self.HIGH_RISK_PREFIXES):
            risk = "high"
        else:
            risk = "write"

        return {
            "source": source,
            "server": server_name,
            "tool": actual_tool,
            "risk": risk,
        }

    def check(self, tool_name: str, tool_input: dict) -> dict:
        intent = self.normalize(tool_name, tool_input)

        if intent["risk"] == "read":
            return {"behavior": "allow", "reason": "Read capability", "intent": intent}

        if self.mode == "auto" and intent["risk"] != "high":
            return {"behavior": "allow", "reason": "Auto mode for non-high-risk", "intent": intent}

        if intent["risk"] == "high":
            return {"behavior": "ask", "reason": "High-risk capability requires confirmation", "intent": intent}

        return {"behavior": "ask", "reason": "State-changing capability requires confirmation", "intent": intent}

    def ask_user(self, intent: dict, tool_input: dict) -> bool:
        if hasattr(self, "web_ask_user_callback") and self.web_ask_user_callback:
            return self.web_ask_user_callback(intent, tool_input)

        preview = json.dumps(tool_input, ensure_ascii=False)[:200]
        source = (
            f"\033[35m{intent['source']}:{intent['server']}/{intent['tool']}\033[0m"
            if intent.get("server")
            else f"\033[32m{intent['source']}:{intent['tool']}\033[0m"
        )
        risk_color = "\033[31m" if intent["risk"] == "high" else "\033[33m"
        print(f"\n  [\033[1mPermission\033[0m] {source} {risk_color}risk={intent['risk']}\033[0m: {preview}")
        try:
            answer = input("  Allow? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")


permission_gate = CapabilityPermissionGate()
