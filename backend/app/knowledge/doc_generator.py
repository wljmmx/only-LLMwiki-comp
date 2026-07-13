"""多智能体文档生成引擎（W7）

基于 LangGraph 的 6 智能体流水线：
  IntentAgent → OutlineAgent → GenerationAgent → ReviewAgent → [ModifyAgent] → ProofreadAgent

状态机：最多 3 次自迭代，Token 预算 120k/200k
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypedDict

import structlog
from langgraph.graph import END, StateGraph

from app.config import get_settings
from app.core.llm import ChatMessage, get_llm_client

logger = structlog.get_logger()

# P2-5.5: 进度回调类型 (stage, data_dict) -> None
ProgressCallback = Callable[[str, dict[str, Any]], None]


# ── 状态定义 ──


class PipelineStage(str, Enum):
    INTENT = "intent"
    OUTLINE = "outline"
    GENERATE = "generate"
    REVIEW = "review"
    MODIFY = "modify"
    PROOFREAD = "proofread"
    DONE = "done"


class ReviewDecision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"


@dataclass
class Section:
    """文档章节"""

    title: str
    level: int = 1
    content: str = ""
    subsections: list[Section] = field(default_factory=list)


class DocGenState(TypedDict, total=False):
    """LangGraph 状态"""

    # 输入
    user_request: str
    context: str  # 图谱检索的上下文
    doc_type: str  # 文档类型（runbook/sop/incident_report/faq）

    # 中间产物
    intent: str  # 意图分析结果
    outline: list[dict]  # 大纲 [{title, level, key_points}]
    sections: list[dict]  # 已生成章节 [{title, level, content}]
    current_section: int  # 当前章节索引

    # 审查
    review_feedback: str  # 审查意见
    review_decision: str  # accept / reject
    iteration: int  # 当前迭代次数
    max_iterations: int  # 最大迭代次数

    # 输出
    final_document: str  # 最终文档
    token_usage: int  # Token 用量
    error: str  # 错误信息


# ── 系统提示词 ──

INTENT_PROMPT = """你是一个运维文档专家。分析用户的文档需求，输出 JSON：

{
  "doc_type": "runbook|sop|incident_report|faq|config_guide",
  "topic": "文档主题",
  "scope": "覆盖范围说明",
  "target_audience": "目标读者",
  "key_requirements": ["需求1", "需求2"]
}

用户需求：{request}
上下文（来自知识图谱）：{context}"""

OUTLINE_PROMPT = """你是一个运维文档架构师。根据意图分析结果，生成文档大纲。

意图分析：{intent}

输出 JSON 数组，每个元素为 {{"title": "章节标题", "level": 1-3, "key_points": ["要点1", "要点2"]}}。
确保大纲结构合理、覆盖全面。"""

GENERATE_PROMPT = """你是一个运维文档撰写专家。根据大纲和上下文生成章节内容。

大纲：{outline}
当前章节：{section_title}
上下文（来自知识图谱）：{context}

要求：
1. 内容准确、专业，基于提供的上下文
2. 包含具体的命令、配置示例、参数说明
3. 使用 Markdown 格式
4. 如果上下文不足，说明"待补充"而非编造"""

REVIEW_PROMPT = """你是一个运维文档质量审查专家。审查以下内容：

文档大纲：{outline}
已生成内容：{content}

检查项：
1. 准确性：内容是否与上下文一致，有无错误
2. 完整性：是否覆盖大纲所有要点
3. 可操作性：命令/配置是否可直接使用
4. 格式规范：Markdown 格式是否正确

输出 JSON：
{
  "decision": "accept|reject",
  "score": 0-100,
  "issues": ["问题1", "问题2"],
  "suggestions": ["建议1", "建议2"]
}"""

MODIFY_PROMPT = """你是一个运维文档修改专家。根据审查意见修改文档内容。

原内容：{content}
审查意见：{feedback}

请修改内容，解决所有问题。保持 Markdown 格式。"""

PROOFREAD_PROMPT = """你是一个运维文档校对专家。对最终文档进行校对润色。

文档内容：{content}

要求：
1. 修正错别字、语法错误
2. 统一术语和格式
3. 优化表达流畅性
4. 保持 Markdown 格式
5. 不要改变实质内容"""


# ── 智能体实现 ──


class DocGenerationPipeline:
    """多智能体文档生成流水线"""

    def __init__(self) -> None:
        self.llm = get_llm_client()
        self.settings = get_settings()
        self.graph = self._build_graph()
        # P2-5.5: 进度回调，generate() 入口设置，节点出口触发
        self._on_progress: ProgressCallback | None = None

    def _emit_progress(self, stage: str, data: dict[str, Any] | None = None) -> None:
        """P2-5.5: 触发进度回调，失败不影响主流程"""
        if self._on_progress is None:
            return
        try:
            self._on_progress(stage, data or {})
        except Exception:  # noqa: BLE001
            pass  # 回调异常不应中断文档生成

    def _build_graph(self) -> StateGraph:
        """构建 LangGraph 状态图"""
        workflow = StateGraph(DocGenState)

        workflow.add_node("intent", self._intent_agent)
        workflow.add_node("outline", self._outline_agent)
        workflow.add_node("generate", self._generate_agent)
        workflow.add_node("review", self._review_agent)
        workflow.add_node("modify", self._modify_agent)
        workflow.add_node("proofread", self._proofread_agent)

        workflow.set_entry_point("intent")
        workflow.add_edge("intent", "outline")
        workflow.add_edge("outline", "generate")
        workflow.add_edge("generate", "review")

        # 审查分支：accept → proofread, reject → modify
        workflow.add_conditional_edges(
            "review",
            self._review_router,
            {"accept": "proofread", "reject": "modify", "done": END},
        )
        workflow.add_edge("modify", "review")
        workflow.add_edge("proofread", END)

        return workflow.compile()

    # ── 路由 ──

    def _review_router(self, state: DocGenState) -> str:
        """审查路由决策"""
        decision = state.get("review_decision", "accept")
        iteration = state.get("iteration", 0)
        max_iter = state.get("max_iterations", self.settings.doc_gen_max_iter)

        if decision == "accept":
            return "accept"
        if iteration >= max_iter:
            logger.info("max_iterations_reached", iteration=iteration)
            return "done"  # 强制结束
        return "reject"

    # ── 智能体节点 ──

    async def _intent_agent(self, state: DocGenState) -> DocGenState:
        """意图理解"""
        self._emit_progress("stage_start", {
            "stage": PipelineStage.INTENT.value,
            "message": "分析需求意图...",
            "iteration": state.get("iteration", 0),
        })
        prompt = INTENT_PROMPT.format(
            request=state.get("user_request", ""),
            context=state.get("context", ""),
        )
        try:
            resp = await self.llm.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=0.1,
                max_tokens=1024,
            )
            state["intent"] = resp.text
            state["token_usage"] = state.get("token_usage", 0) + (
                resp.prompt_tokens + resp.completion_tokens
            )
        except Exception as e:
            state["error"] = f"intent_agent: {e}"
            state["intent"] = '{"doc_type": "runbook", "topic": "未知"}'
        self._emit_progress("stage_done", {
            "stage": PipelineStage.INTENT.value,
            "token_usage": state.get("token_usage", 0),
            "error": state.get("error"),
        })
        return state

    async def _outline_agent(self, state: DocGenState) -> DocGenState:
        """大纲生成"""
        self._emit_progress("stage_start", {
            "stage": PipelineStage.OUTLINE.value,
            "message": "生成文档大纲...",
            "iteration": state.get("iteration", 0),
        })
        prompt = OUTLINE_PROMPT.format(intent=state.get("intent", ""))
        try:
            resp = await self.llm.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=0.2,
                max_tokens=2048,
            )
            state["outline"] = self._parse_json(resp.text)
            state["current_section"] = 0
            state["token_usage"] = state.get("token_usage", 0) + (
                resp.prompt_tokens + resp.completion_tokens
            )
        except Exception as e:
            state["error"] = f"outline_agent: {e}"
            state["outline"] = []
        self._emit_progress("stage_done", {
            "stage": PipelineStage.OUTLINE.value,
            "sections_total": len(state.get("outline", [])),
            "token_usage": state.get("token_usage", 0),
            "error": state.get("error"),
        })
        return state

    async def _generate_agent(self, state: DocGenState) -> DocGenState:
        """内容生成"""
        outline = state.get("outline", [])
        sections = state.get("sections", [])
        idx = state.get("current_section", 0)

        # P2-5.5: 首次进入 generate 节点时发 stage_start
        if idx == 0:
            self._emit_progress("stage_start", {
                "stage": PipelineStage.GENERATE.value,
                "message": "生成文档内容...",
                "sections_total": len(outline),
                "iteration": state.get("iteration", 0),
            })

        if idx >= len(outline):
            return state

        section = outline[idx]
        # P2-5.5: 逐章节进度
        self._emit_progress("section_start", {
            "stage": PipelineStage.GENERATE.value,
            "section_index": idx,
            "section_total": len(outline),
            "section_title": section.get("title", ""),
        })
        prompt = GENERATE_PROMPT.format(
            outline=outline,
            section_title=section.get("title", ""),
            context=state.get("context", ""),
        )
        try:
            resp = await self.llm.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=0.3,
                max_tokens=self.settings.llm_max_tokens,
            )
            sections.append(
                {
                    "title": section.get("title", ""),
                    "level": section.get("level", 1),
                    "content": resp.text,
                }
            )
            state["sections"] = sections
            state["current_section"] = idx + 1
            state["token_usage"] = state.get("token_usage", 0) + (
                resp.prompt_tokens + resp.completion_tokens
            )
        except Exception as e:
            state["error"] = f"generate_agent: {e}"

        # P2-5.5: 单章节完成进度
        self._emit_progress("section_done", {
            "stage": PipelineStage.GENERATE.value,
            "section_index": idx + 1,
            "section_total": len(outline),
            "token_usage": state.get("token_usage", 0),
        })

        # 如果还有章节未生成，继续生成
        if state["current_section"] < len(outline):
            return await self._generate_agent(state)
        self._emit_progress("stage_done", {
            "stage": PipelineStage.GENERATE.value,
            "sections_completed": len(state.get("sections", [])),
            "token_usage": state.get("token_usage", 0),
        })
        return state

    async def _review_agent(self, state: DocGenState) -> DocGenState:
        """质量审查"""
        self._emit_progress("stage_start", {
            "stage": PipelineStage.REVIEW.value,
            "message": f"质量审查（第 {state.get('iteration', 0) + 1} 轮）...",
            "iteration": state.get("iteration", 0) + 1,
        })
        outline = state.get("outline", [])
        sections = state.get("sections", [])
        content = self._format_document(sections)

        prompt = REVIEW_PROMPT.format(outline=outline, content=content)
        try:
            resp = await self.llm.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=0.1,
                max_tokens=1024,
            )
            review = self._parse_json(resp.text)
            decision = (
                review.get("decision", "accept")
                if isinstance(review, dict)
                else "accept"
            )
            state["review_decision"] = decision
            state["review_feedback"] = resp.text
            state["iteration"] = state.get("iteration", 0) + 1
            state["token_usage"] = state.get("token_usage", 0) + (
                resp.prompt_tokens + resp.completion_tokens
            )
        except Exception as e:
            state["error"] = f"review_agent: {e}"
            state["review_decision"] = "accept"
        self._emit_progress("stage_done", {
            "stage": PipelineStage.REVIEW.value,
            "decision": state.get("review_decision", "accept"),
            "iteration": state.get("iteration", 0),
            "token_usage": state.get("token_usage", 0),
            "error": state.get("error"),
        })
        return state

    async def _modify_agent(self, state: DocGenState) -> DocGenState:
        """修改执行"""
        self._emit_progress("stage_start", {
            "stage": PipelineStage.MODIFY.value,
            "message": f"根据审查反馈修改（第 {state.get('iteration', 0)} 轮）...",
            "iteration": state.get("iteration", 0),
        })
        sections = state.get("sections", [])
        content = self._format_document(sections)
        feedback = state.get("review_feedback", "")

        prompt = MODIFY_PROMPT.format(content=content, feedback=feedback)
        try:
            resp = await self.llm.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=0.2,
                max_tokens=self.settings.llm_max_tokens,
            )
            # 用修改后的内容替换所有章节
            state["sections"] = [
                {"title": "修改后内容", "level": 1, "content": resp.text}
            ]
            state["token_usage"] = state.get("token_usage", 0) + (
                resp.prompt_tokens + resp.completion_tokens
            )
        except Exception as e:
            state["error"] = f"modify_agent: {e}"
        self._emit_progress("stage_done", {
            "stage": PipelineStage.MODIFY.value,
            "iteration": state.get("iteration", 0),
            "token_usage": state.get("token_usage", 0),
            "error": state.get("error"),
        })
        return state

    async def _proofread_agent(self, state: DocGenState) -> DocGenState:
        """校对润色"""
        self._emit_progress("stage_start", {
            "stage": PipelineStage.PROOFREAD.value,
            "message": "校对润色最终文档...",
            "iteration": state.get("iteration", 0),
        })
        sections = state.get("sections", [])
        content = self._format_document(sections)

        prompt = PROOFREAD_PROMPT.format(content=content)
        try:
            resp = await self.llm.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=0.1,
                max_tokens=self.settings.llm_max_tokens,
            )
            state["final_document"] = resp.text
            state["token_usage"] = state.get("token_usage", 0) + (
                resp.prompt_tokens + resp.completion_tokens
            )
        except Exception as e:
            state["error"] = f"proofread_agent: {e}"
            state["final_document"] = content
        self._emit_progress("stage_done", {
            "stage": PipelineStage.PROOFREAD.value,
            "token_usage": state.get("token_usage", 0),
            "error": state.get("error"),
        })
        return state

    # ── 工具方法 ──

    def _format_document(self, sections: list[dict]) -> str:
        """格式化章节为 Markdown 文档"""
        lines = []
        for s in sections:
            prefix = "#" * s.get("level", 1)
            lines.append(f"{prefix} {s.get('title', '')}")
            lines.append("")
            lines.append(s.get("content", ""))
            lines.append("")
        return "\n".join(lines)

    def _parse_json(self, text: str) -> Any:
        """从 LLM 输出中解析 JSON"""
        import json
        import re

        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            return text

    # ── 入口 ──

    async def generate(
        self,
        request: str,
        context: str = "",
        max_iterations: int | None = None,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> DocGenState:
        """执行文档生成流水线

        Args:
            request: 用户需求
            context: 图谱检索上下文
            max_iterations: review→modify 最大迭代次数
            on_progress: P2-5.5 进度回调，签名 (stage, data) -> None
                stage 取值见 PipelineStage 枚举 + "stage_start"/"stage_done" 前缀
                data 含 iteration/token_usage/sections 等运行时信息
        """
        # P2-5.5: 设置进度回调，供 6 个节点出口触发
        self._on_progress = on_progress
        initial_state: DocGenState = {
            "user_request": request,
            "context": context,
            "iteration": 0,
            "max_iterations": max_iterations or self.settings.doc_gen_max_iter,
            "token_usage": 0,
            "sections": [],
            "current_section": 0,
        }
        try:
            result = await self.graph.ainvoke(initial_state)
        finally:
            # 清理回调，避免单例泄漏到下次调用
            self._on_progress = None
        logger.info(
            "doc_gen_done",
            sections=len(result.get("sections", [])),
            iterations=result.get("iteration", 0),
            token_usage=result.get("token_usage", 0),
        )
        return result


# 全局单例
_pipeline: DocGenerationPipeline | None = None


def get_pipeline() -> DocGenerationPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = DocGenerationPipeline()
    return _pipeline
