"""P2-7: Agent Loop 探索 — LLM 自主选工具→执行→观察→再选择

与传统 RAG（LLM 单次生成）不同，Agent Loop 让 LLM：
1. 接收用户问题 + 可用工具列表
2. 自主决定调用哪个工具（function calling）
3. 执行工具，观察结果
4. 基于观察结果决定：继续调工具 or 给出最终答案
5. 循环直到给出最终答案或达到最大迭代数

这是从 RAG 到 Agent 的关键升级。当前为探索性实现，验证可行性。

用法：
    agent = AgentLoop(
        llm_client=get_llm_client(),
        tools={
            "search_knowledge": {
                "description": "搜索知识库",
                "parameters": {"query": "str", "limit": "int"},
                "handler": lambda args: search_engine.search(args["query"]),
            },
        },
    )
    result = await agent.run("Nginx 502 怎么排查？")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import structlog

from app.core.llm.base import ChatMessage, LLMClient, LLMResponse

logger = structlog.get_logger()


@dataclass
class ToolDef:
    """工具定义"""

    name: str
    description: str
    parameters: dict[str, str]  # 参数名 → 参数类型描述
    handler: Callable[[dict[str, Any]], str]  # 同步执行器，返回文本结果


@dataclass
class AgentStep:
    """Agent 执行步骤记录"""

    step_type: str  # "tool_call" | "observation" | "final_answer"
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    text: str | None = None


@dataclass
class AgentResult:
    """Agent 执行结果"""

    answer: str
    steps: list[AgentStep] = field(default_factory=list)
    total_tokens: int = 0
    iterations: int = 0


class AgentLoop:
    """Agent Loop — LLM 自主工具调用循环

    通过 OpenAI 兼容的 function calling 实现：
    - LLM 返回 tool_calls → 执行工具 → 将结果作为 observation 注入 → LLM 再决策
    - 最多 max_iterations 轮，防止无限循环
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: dict[str, ToolDef],
        *,
        max_iterations: int = 5,
        system_prompt: str | None = None,
    ) -> None:
        self._llm = llm_client
        self._tools = tools
        self._max_iterations = max_iterations
        self._system_prompt = system_prompt or (
            "你是 OpsKG 运维助手。你可以调用工具获取信息来回答用户问题。"
            "优先使用工具获取准确信息，基于工具返回的结果给出回答。"
            "如果工具返回的信息不足以回答问题，明确指出缺口。"
        )

    async def run(self, question: str, *, history: list[dict] | None = None) -> AgentResult:
        """执行 Agent Loop

        Args:
            question: 用户问题
            history: 对话历史（可选）

        Returns:
            AgentResult 含最终答案 + 执行步骤 + token 用量
        """
        steps: list[AgentStep] = []
        total_tokens = 0

        # 构建 OpenAI function calling 格式的工具定义
        openai_tools = self._build_openai_tools()

        # 构建消息历史
        messages: list[ChatMessage] = [ChatMessage(role="system", content=self._system_prompt)]
        if history:
            for h in history[-5:]:  # 最多保留 5 轮历史
                messages.append(ChatMessage(role=h.get("role", "user"), content=h.get("content", "")))
        messages.append(ChatMessage(role="user", content=question))

        for iteration in range(self._max_iterations):
            # 调用 LLM，附带工具定义
            try:
                resp = await self._llm.chat(
                    messages,
                    temperature=0.2,
                    max_tokens=4096,
                    tools=openai_tools,  # type: ignore[arg-type]
                )
            except Exception as e:
                logger.warning("agent_loop.llm_call_failed", iteration=iteration, error=str(e))
                return AgentResult(
                    answer=f"Agent 执行失败：{e}",
                    steps=steps,
                    total_tokens=total_tokens,
                    iterations=iteration,
                )

            total_tokens += resp.prompt_tokens + resp.completion_tokens

            # 检查 LLM 是否要求调用工具
            tool_calls = self._extract_tool_calls(resp)
            if not tool_calls:
                # LLM 给出最终答案
                steps.append(AgentStep(step_type="final_answer", text=resp.text))
                logger.info(
                    "agent_loop.completed",
                    iterations=iteration + 1,
                    total_tokens=total_tokens,
                    steps=len(steps),
                )
                return AgentResult(
                    answer=resp.text,
                    steps=steps,
                    total_tokens=total_tokens,
                    iterations=iteration + 1,
                )

            # 执行工具调用
            for tool_call in tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["arguments"]

                steps.append(
                    AgentStep(
                        step_type="tool_call",
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )
                )

                # 执行工具
                tool_def = self._tools.get(tool_name)
                if tool_def is None:
                    tool_result = f"错误：未知工具 {tool_name}"
                else:
                    try:
                        tool_result = tool_def.handler(tool_args)
                    except Exception as e:
                        tool_result = f"工具执行出错：{e}"

                steps.append(
                    AgentStep(
                        step_type="observation",
                        tool_name=tool_name,
                        tool_result=tool_result,
                    )
                )

                # 将工具结果注入消息历史（让 LLM 下一轮能看到）
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=json.dumps(
                            {"tool_call": tool_name, "args": tool_args},
                            ensure_ascii=False,
                        ),
                    )
                )
                messages.append(
                    ChatMessage(
                        role="user",
                        content=f"工具 {tool_name} 的返回结果：\n{tool_result}\n\n"
                        "请基于此结果继续：如果信息足够请给出最终回答，"
                        "如果需要更多信息请继续调用工具。",
                    )
                )

                logger.info(
                    "agent_loop.tool_executed",
                    iteration=iteration,
                    tool=tool_name,
                    result_len=len(tool_result),
                )

        # 达到最大迭代数仍未给出最终答案
        logger.warning("agent_loop.max_iterations_reached", max_iterations=self._max_iterations)
        return AgentResult(
            answer=f"Agent 达到最大迭代数 {self._max_iterations}，未给出最终答案。"
            f"已执行 {len(steps)} 步。",
            steps=steps,
            total_tokens=total_tokens,
            iterations=self._max_iterations,
        )

    def _build_openai_tools(self) -> list[dict]:
        """构建 OpenAI function calling 格式的工具定义"""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            name: {"type": type_desc, "description": name}
                            for name, type_desc in tool.parameters.items()
                        },
                        "required": list(tool.parameters.keys()),
                    },
                },
            }
            for tool in self._tools.values()
        ]

    def _extract_tool_calls(self, resp: LLMResponse) -> list[dict]:
        """从 LLMResponse 中提取 tool_calls

        当前 LLMResponse.raw 是 openai SDK 的 model_dump()，
        含 choices[0].message.tool_calls 字段。
        """
        if not resp.raw:
            return []

        choices = resp.raw.get("choices", [])
        if not choices:
            return []

        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return []

        result: list[dict] = []
        for tc in tool_calls:
            function = tc.get("function", {})
            name = function.get("name")
            args_str = function.get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except json.JSONDecodeError:
                args = {}
            if name:
                result.append({"name": name, "arguments": args})
        return result
