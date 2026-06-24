#!/usr/bin/env python3
"""
SSH VPS MCP Server
Executes commands on remote VPS via SSH using password authentication.
WARNING: Storing passwords in environment variables/configs is not secure for production use.
"""

import os
import sys
import json
import subprocess

VPS_IP = os.environ.get("VPS_IP", "31.97.105.238")
VPS_USER = os.environ.get("VPS_USER", "root")
VPS_PASS = os.environ.get("VPS_PASS", "")

def log_debug(msg):
    """Log debug messages to stderr so they don't interfere with MCP protocol."""
    print(f"[VPS SSH] {msg}", file=sys.stderr)

def execute_ssh_command(command):
    """Execute a command on the VPS via SSH."""
    if not VPS_PASS:
        return {"error": "VPS_PASSWORD not set in environment"}
    
    try:
        process = subprocess.run(
            [
                "sshpass", "-p", VPS_PASS, "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=10",
                "-o", "ServerAliveInterval=5",
                f"{VPS_USER}@{VPS_IP}",
                command
            ],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        return {
            "stdout": process.stdout,
            "stderr": process.stderr,
            "exit_code": process.returncode
        }

    except subprocess.TimeoutExpired:
        return {"error": "Command execution timed out (60s limit)"}
    except FileNotFoundError:
        return {"error": "sshpass not found. Install it: brew install sshpass"}
    except Exception as e:
        return {"error": f"SSH connection error: {str(e)}"}

def handle_mcp_request(request):
    """Handle incoming MCP JSON-RPC requests."""
    method = request.get("method", "")
    params = request.get("params", {})
    request_id = request.get("id")

    # Handle initialization
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "vps-ssh-mcp",
                    "version": "1.0.0"
                }
            }
        }

    # List available tools
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "ssh_exec",
                        "description": f"Execute shell command on VPS ({VPS_IP})",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": "The shell command to execute on the VPS (e.g., 'ls -la', 'df -h', 'ps aux')"
                                }
                            },
                            "required": ["command"]
                        }
                    },
                    {
                        "name": "ssh_exec_sudo",
                        "description": f"Execute command with sudo on VPS ({VPS_IP})",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": "The command to execute with sudo privileges"
                                }
                            },
                            "required": ["command"]
                        }
                    }
                ]
            }
        }

    # Execute tool calls
    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        
        if tool_name == "ssh_exec":
            command = tool_args.get("command", "")
            result = execute_ssh_command(command)
            
            # Format output for better readability
            output_parts = []
            if result.get("stdout"):
                output_parts.append(f"**STDOUT:**\n```bash\n{result['stdout']}```")
            if result.get("stderr"):
                output_parts.append(f"**STDERR:**\n```bash\n{result['stderr']}```")
            if result.get("exit_code") is not None:
                output_parts.append(f"**Exit Code:** {result['exit_code']}")
            if result.get("error"):
                output_parts.append(f"**ERROR:** {result['error']}")
                
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "\n\n".join(output_parts) if output_parts else "No output"
                        }
                    ]
                }
            }

        elif tool_name == "ssh_exec_sudo":
            command = tool_args.get("command", "")
            # Prepend sudo to the command
            result = execute_ssh_command(f"sudo {command}")
            
            output_parts = []
            if result.get("stdout"):
                output_parts.append(f"**STDOUT:**\n```bash\n{result['stdout']}```")
            if result.get("stderr"):
                output_parts.append(f"**STDERR:**\n```bash\n{result['stderr']}```")
            if result.get("exit_code") is not None:
                output_parts.append(f"**Exit Code:** {result['exit_code']}")
            if result.get("error"):
                output_parts.append(f"**ERROR:** {result['error']}")
                
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "\n\n".join(output_parts) if output_parts else "No output"
                        }
                    ]
                }
            }

        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unknown tool: {tool_name}"
                }
            }

    # Handle initialization notification
    if method == "initialized":
        return None

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32601,
            "message": f"Unknown method: {method}"
        }
    }

def main():
    log_debug(f"Connecting to VPS: {VPS_USER}@{VPS_IP}")
    log_debug("SSH VPS MCP Server started. Waiting for commands...")

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            request = json.loads(line.strip())
            response = handle_mcp_request(request)
            
            if response is not None:
                print(json.dumps(response), flush=True)

        except json.JSONDecodeError as e:
            error_response = {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {str(e)}"}
            }
            print(json.dumps(error_response), flush=True)
            
        except KeyboardInterrupt:
            log_debug("Shutting down...")
            break
            
        except Exception as e:
            log_debug(f"Error: {str(e)}")
            error_response = {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": f"Server error: {str(e)}"}
            }
            print(json.dumps(error_response), flush=True)

if __name__ == "__main__":
    main()