#!/usr/bin/env python3
"""
Qwen3.5-Plus MCP Server (stdio)
Proxies MCP requests to DashScope API (Qwen model).
Uses standard input/output for MCP communication.
"""

import os
import sys
import json
import urllib.request

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen-plus")
DASHSCOPE_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def log_debug(msg):
    print(f"[DEBUG] {msg}", file=sys.stderr)


def call_dashscope(messages):
    payload = {
        "model": QWEN_MODEL,
        "messages": messages,
    }

    req = urllib.request.Request(
        DASHSCOPE_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        },
    )

    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode("utf-8"))

    assistant_msg = result.get("choices", [{}])[0].get("message", {})
    return {
        "role": "assistant",
        "content": assistant_msg.get("content", ""),
    }


def handle_mcp_request(request):
    method = request.get("method", "")
    params = request.get("params", {})
    request_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                    "prompts": {},
                    "resources": {},
                },
                "serverInfo": {
                    "name": "qwen-plus-mcp",
                    "version": "1.0.0",
                },
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "chat",
                        "description": "Chat with Qwen3.5-Plus (qwen-plus model via DashScope)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "prompt": {
                                    "type": "string",
                                    "description": "The prompt to send to Qwen",
                                }
                            },
                            "required": ["prompt"],
                        },
                    },
                    {
                        "name": "complete_task",
                        "description": "Complete a complex task using Qwen's reasoning capabilities",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "task": {
                                    "type": "string",
                                    "description": "The task description",
                                },
                                "context": {
                                    "type": "string",
                                    "description": "Additional context for the task",
                                },
                            },
                            "required": ["task"],
                        },
                    },
                ]
            },
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if tool_name == "chat":
            prompt = tool_args.get("prompt", "")
            assistant = call_dashscope([{"role": "user", "content": prompt}])
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": assistant.get("content", ""),
                        }
                    ]
                },
            }

        elif tool_name == "complete_task":
            task = tool_args.get("task", "")
            context = tool_args.get("context", "")
            system_prompt = f"You are Qwen3.5-Plus, an AI assistant. Help the user complete this task: {task}"
            messages = [{"role": "system", "content": system_prompt}]
            if context:
                messages.append({"role": "user", "content": f"Context: {context}"})
            assistant = call_dashscope(messages)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": assistant.get("content", ""),
                        }
                    ]
                },
            }

        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unknown tool: {tool_name}",
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32601,
            "message": f"Unknown method: {method}",
        },
    }


def main():
    log_debug(f"Starting Qwen MCP Server (model: {QWEN_MODEL})")
    log_debug(f"Using API URL: {DASHSCOPE_API_URL}")
    log_debug(f"API Key: {'*' * (len(DASHSCOPE_API_KEY) - 4)}{DASHSCOPE_API_KEY[-4:]}")

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            request = json.loads(line.strip())
            response = handle_mcp_request(request)
            print(json.dumps(response), flush=True)
        except json.JSONDecodeError as e:
            error_response = {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {str(e)}"},
            }
            print(json.dumps(error_response), flush=True)
        except KeyboardInterrupt:
            log_debug("Shutting down...")
            break
        except Exception as e:
            log_debug(f"Error: {str(e)}")
            error_response = {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": f"Server error: {str(e)}"},
            }
            print(json.dumps(error_response), flush=True)


if __name__ == "__main__":
    main()
