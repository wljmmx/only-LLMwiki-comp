"""MCP server 入口

支持两种运行模式：
1. stdio 模式（独立脚本）：python -m app.mcp.server
   适合被 Claude Desktop / Cursor 等 MCP 客户端作为子进程启动
2. HTTP 模式（集成到 FastAPI）：见 main.py 的 /mcp 端点
"""
from __future__ import annotations

import json
import sys

from app.mcp.protocol import handle_request


def run_stdio() -> None:
    """stdio 模式：从 stdin 读取 JSON-RPC，写出到 stdout"""
    print(f"OpsKG MCP Server (stdio mode) 启动", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            # 返回 parse error
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"JSON 解析失败: {e}"},
            }
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue

        # 处理批量请求
        if isinstance(request, list):
            responses = []
            for req in request:
                resp = handle_request(req)
                if resp is not None:
                    responses.append(resp)
            if responses:
                sys.stdout.write(json.dumps(responses) + "\n")
                sys.stdout.flush()
        else:
            response = handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    run_stdio()
