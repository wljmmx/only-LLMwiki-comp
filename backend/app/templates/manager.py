"""模板管理（P1-3）

文档模板 CRUD + 变量占位渲染。
内置运维常用模板，支持自定义。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "templates.db"

# 内置模板
BUILTIN_TEMPLATES = [
    {
        "slug": "runbook",
        "name": "运维操作手册（Runbook）",
        "category": "ops",
        "description": "标准操作流程模板，包含前置条件、操作步骤、回滚方案",
        "content": """# {{title}} Runbook

## 概述
{{description}}

## 前置条件
- [ ] 权限确认：{{required_permission}}
- [ ] 备份状态：{{backup_status}}
- [ ] 影响评估：{{impact_scope}}

## 操作步骤
{{#steps}}
### 步骤 {{step_num}}: {{step_name}}
**操作命令**:
```bash
{{step_command}}
```
**预期输出**:
```
{{expected_output}}
```
{{/steps}}

## 回滚方案
{{rollback_plan}}

## 验证
- [ ] {{verification_check}}

## 联系人
- 负责人: {{owner}}
- 升级路径: {{escalation_path}}
""",
    },
    {
        "slug": "sop",
        "name": "标准作业程序（SOP）",
        "category": "ops",
        "description": "规范化作业流程模板",
        "content": """# SOP: {{title}}

## 目的
{{purpose}}

## 适用范围
{{scope}}

## 责任人
- 主责: {{primary_owner}}
- 协助: {{secondary_owner}}

## 操作流程
{{#procedures}}
### {{proc_id}}. {{proc_name}}
**操作说明**: {{proc_description}}
**风险等级**: {{risk_level}}
**预计耗时**: {{estimated_time}}
{{/procedures}}

## 质量检查
{{quality_checks}}

## 相关文档
{{references}}
""",
    },
    {
        "slug": "incident-report",
        "name": "故障报告（Post-mortem）",
        "category": "incident",
        "description": "故障复盘报告模板",
        "content": """# 故障报告: {{incident_title}}

## 基本信息
- 故障等级: {{severity}}
- 发生时间: {{occurred_at}}
- 恢复时间: {{resolved_at}}
- 持续时长: {{duration}}
- 影响范围: {{impact}}

## 故障现象
{{symptoms}}

## 根因分析
{{root_cause}}

## 处理过程
{{#timeline}}
### {{timestamp}}
{{action}}
{{/timeline}}

## 影响评估
- 业务影响: {{business_impact}}
- 数据影响: {{data_impact}}
- 估计损失: {{estimated_loss}}

## 改进措施
{{#action_items}}
- [ ] {{action}} (负责人: {{owner}}, 截止: {{deadline}})
{{/action_items}}

## 经验教训
{{lessons_learned}}
""",
    },
    {
        "slug": "config-guide",
        "name": "配置指南",
        "category": "config",
        "description": "组件配置参数说明文档",
        "content": """# {{component}} 配置指南

## 概述
{{description}}

## 基础配置
{{#basic_configs}}
### {{param_name}}
- **默认值**: `{{default_value}}`
- **说明**: {{description}}
- **风险等级**: {{risk_level}}
{{/basic_configs}}

## 高级配置
{{#advanced_configs}}
### {{param_name}}
- **默认值**: `{{default_value}}`
- **推荐值**: `{{recommended_value}}`
- **说明**: {{description}}
{{/advanced_configs}}

## 常见问题
{{#faq}}
### Q: {{question}}
A: {{answer}}
{{/faq}}
""",
    },
    {
        "slug": "faq",
        "name": "常见问题（FAQ）",
        "category": "general",
        "description": "问答知识库模板",
        "content": """# {{title}} FAQ

{{#questions}}
## Q: {{question}}
**A**: {{answer}}

<details>
<summary>详细信息</summary>

{{details}}
</details>
{{/questions}}
""",
    },
]


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            description TEXT DEFAULT '',
            content TEXT NOT NULL,
            is_builtin INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tpl_category ON templates(category);
        CREATE INDEX IF NOT EXISTS idx_tpl_slug ON templates(slug);
    """)


class TemplateManager:
    """模板管理器"""

    def __init__(self) -> None:
        self._ensure_builtin()

    def _ensure_builtin(self) -> None:
        """初始化内置模板"""
        conn = _get_db()
        for tpl in BUILTIN_TEMPLATES:
            existing = conn.execute(
                "SELECT id FROM templates WHERE slug = ?", (tpl["slug"],)
            ).fetchone()
            if not existing:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """INSERT INTO templates
                       (slug, name, category, description, content, is_builtin, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        tpl["slug"],
                        tpl["name"],
                        tpl["category"],
                        tpl["description"],
                        tpl["content"],
                        now,
                        now,
                    ),
                )
        conn.commit()

    def list(self, category: str | None = None) -> list[dict]:
        """列出模板"""
        conn = _get_db()
        if category:
            rows = conn.execute(
                "SELECT * FROM templates WHERE category = ? ORDER BY name", (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM templates ORDER BY category, name"
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, slug: str) -> dict | None:
        """获取模板"""
        conn = _get_db()
        row = conn.execute("SELECT * FROM templates WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def create(
        self,
        slug: str,
        name: str,
        content: str,
        category: str = "custom",
        description: str = "",
    ) -> dict:
        """创建自定义模板"""
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn.execute(
                """INSERT INTO templates
                   (slug, name, category, description, content, is_builtin, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
                (slug, name, category, description, content, now, now),
            )
            conn.commit()
            return self.get(slug)
        except sqlite3.IntegrityError:
            raise ValueError(f"模板 slug '{slug}' 已存在")

    def update(
        self,
        slug: str,
        name: str | None = None,
        content: str | None = None,
        category: str | None = None,
        description: str | None = None,
    ) -> dict | None:
        """更新模板（内置模板不可改 content）"""
        existing = self.get(slug)
        if not existing:
            return None
        if existing["is_builtin"] and content is not None:
            raise ValueError("内置模板内容不可修改，请创建副本")

        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        sets = ["updated_at = ?"]
        params: list = [now]
        for field, value in [
            ("name", name),
            ("content", content),
            ("category", category),
            ("description", description),
        ]:
            if value is not None:
                sets.append(f"{field} = ?")
                params.append(value)
        params.append(slug)
        conn.execute(f"UPDATE templates SET {', '.join(sets)} WHERE slug = ?", params)
        conn.commit()
        return self.get(slug)

    def delete(self, slug: str) -> bool:
        """删除模板（仅自定义）"""
        existing = self.get(slug)
        if not existing:
            return False
        if existing["is_builtin"]:
            raise ValueError("内置模板不可删除")
        conn = _get_db()
        conn.execute("DELETE FROM templates WHERE slug = ?", (slug,))
        conn.commit()
        return True

    def render(self, slug: str, variables: dict) -> str:
        """渲染模板，替换变量占位

        支持 Mustache 风格: {{variable}}
        支持条件块: {{#list}}...{{/list}}（简单循环）
        """
        tpl = self.get(slug)
        if not tpl:
            raise ValueError(f"模板不存在: {slug}")
        return self._render_template(tpl["content"], variables)

    def _render_template(self, template: str, variables: dict) -> str:
        """渲染模板"""
        result = template

        # 处理循环块 {{#list}}...{{/list}}
        loop_pattern = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.DOTALL)
        for match in loop_pattern.finditer(result):
            var_name = match.group(1)
            block_template = match.group(2)
            items = variables.get(var_name, [])
            if not isinstance(items, list):
                items = []
            rendered_blocks = []
            for i, item in enumerate(items, 1):
                if isinstance(item, dict):
                    item = {**item, "step_num": i, "proc_id": i}
                rendered_blocks.append(
                    self._render_template(
                        block_template, item if isinstance(item, dict) else {}
                    )
                )
            result = result.replace(match.group(0), "".join(rendered_blocks))

        # 替换简单变量 {{variable}}
        for key, value in variables.items():
            if isinstance(value, (str, int, float, bool)):
                result = result.replace(f"{{{{{key}}}}}", str(value))

        # 清理未匹配的占位符
        result = re.sub(r"\{\{\w+\}\}", "", result)

        return result.strip()


# 全局单例
_mgr: TemplateManager | None = None


def get_template_manager() -> TemplateManager:
    global _mgr
    if _mgr is None:
        _mgr = TemplateManager()
    return _mgr
