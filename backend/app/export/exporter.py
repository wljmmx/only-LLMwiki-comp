"""导出功能（P1-4）

支持将生成的文档导出为 Markdown / HTML / 纯文本格式。
PDF 导出需要额外依赖（wkhtmltopdf），可选。
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import structlog

logger = structlog.get_logger()


class Exporter:
    """文档导出器"""

    def to_markdown(self, title: str, content: str) -> bytes:
        """导出为 Markdown"""
        header = f"# {title}\n\n"
        return (header + content).encode("utf-8")

    def to_html(self, title: str, content: str, include_css: bool = True) -> bytes:
        """导出为 HTML（带基本样式）"""
        css = ""
        if include_css:
            css = """<style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                   max-width: 800px; margin: 40px auto; padding: 20px;
                   color: #333; line-height: 1.6; }
            h1 { color: #1a1a2e; border-bottom: 2px solid #16213e; padding-bottom: 10px; }
            h2 { color: #16213e; margin-top: 30px; }
            h3 { color: #0f3460; }
            code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px;
                   font-family: 'Fira Code', monospace; font-size: 0.9em; }
            pre { background: #1e1e2e; color: #cdd6f4; padding: 16px;
                  border-radius: 8px; overflow-x: auto; }
            pre code { background: none; color: inherit; }
            table { border-collapse: collapse; width: 100%; margin: 16px 0; }
            th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
            th { background: #f0f0f0; font-weight: 600; }
            blockquote { border-left: 4px solid #16213e; margin: 16px 0;
                        padding: 8px 16px; background: #f8f9fa; color: #555; }
            a { color: #0f3460; }
            </style>"""

        body_html = self._markdown_to_html(content)
        full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)}</title>
{css}
</head>
<body>
<h1>{html.escape(title)}</h1>
{body_html}
</body>
</html>"""
        return full_html.encode("utf-8")

    def to_text(self, title: str, content: str) -> bytes:
        """导出为纯文本"""
        import re

        # 去除 Markdown 标记
        text = re.sub(r"^#+\s+", "", content, flags=re.MULTILINE)
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"`(.+?)`", r"\1", text)
        text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
        text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^[-*]\s+", "  - ", text, flags=re.MULTILINE)
        return f"{title}\n{'=' * len(title)}\n\n{text}".encode("utf-8")

    def export(
        self,
        title: str,
        content: str,
        fmt: str = "markdown",
    ) -> tuple[bytes, str, str]:
        """导出文档

        Returns:
            (content_bytes, media_type, file_extension)
        """
        if fmt == "markdown" or fmt == "md":
            return self.to_markdown(title, content), "text/markdown", ".md"
        elif fmt == "html":
            return self.to_html(title, content), "text/html", ".html"
        elif fmt == "text" or fmt == "txt":
            return self.to_text(title, content), "text/plain", ".txt"
        elif fmt == "pdf":
            return self._to_pdf(title, content), "application/pdf", ".pdf"
        else:
            raise ValueError(f"不支持的导出格式: {fmt}")

    def _markdown_to_html(self, md: str) -> str:
        """简易 Markdown → HTML 转换（不依赖外部库）"""
        import re

        lines = md.split("\n")
        html_parts: list[str] = []
        in_code_block = False
        in_list = False
        in_table = False
        table_rows: list[str] = []

        for line in lines:
            # 代码块
            if line.strip().startswith("```"):
                if in_code_block:
                    html_parts.append("</code></pre>")
                    in_code_block = False
                else:
                    lang = line.strip()[3:]
                    html_parts.append(f'<pre><code class="{lang}">')
                    in_code_block = True
                continue
            if in_code_block:
                html_parts.append(html.escape(line))
                continue

            # 表格
            if line.strip().startswith("|"):
                table_rows.append(line.strip())
                in_table = True
                continue
            elif in_table:
                # 表格结束
                if table_rows:
                    html_parts.append(self._render_table(table_rows))
                    table_rows = []
                in_table = False

            # 标题
            if line.startswith("### "):
                html_parts.append(f"<h3>{html.escape(line[4:])}</h3>")
                continue
            if line.startswith("## "):
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                html_parts.append(f"<h2>{html.escape(line[3:])}</h2>")
                continue
            if line.startswith("# "):
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                html_parts.append(f"<h2>{html.escape(line[2:])}</h2>")
                continue

            # 引用
            if line.startswith("> "):
                html_parts.append(f"<blockquote>{html.escape(line[2:])}</blockquote>")
                continue

            # 列表
            if re.match(r"^[-*]\s", line):
                if not in_list:
                    html_parts.append("<ul>")
                    in_list = True
                html_parts.append(f"<li>{html.escape(line[2:])}</li>")
                continue
            elif in_list:
                html_parts.append("</ul>")
                in_list = False

            # 空行
            if not line.strip():
                continue

            # 普通段落
            # 处理行内代码和粗体
            text = html.escape(line)
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
            text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
            html_parts.append(f"<p>{text}</p>")

        # 收尾
        if in_list:
            html_parts.append("</ul>")
        if in_code_block:
            html_parts.append("</code></pre>")
        if table_rows:
            html_parts.append(self._render_table(table_rows))

        return "\n".join(html_parts)

    def _render_table(self, rows: list[str]) -> str:
        """渲染 Markdown 表格为 HTML"""
        html_rows = []
        for i, row in enumerate(rows):
            # 跳过分隔行 |---|---|
            if re.match(r"^\|[\s:\-]+\|", row):
                continue
            cells = [c.strip() for c in row.strip("|").split("|")]
            tag = "th" if i == 0 else "td"
            tds = "".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in cells)
            html_rows.append(f"<tr>{tds}</tr>")
        return f"<table>{''.join(html_rows)}</table>"

    def _to_pdf(self, title: str, content: str) -> bytes:
        """导出 PDF（需要 wkhtmltopdf）"""
        try:
            import subprocess
            import tempfile

            html_content = self.to_html(title, content).decode("utf-8")
            with tempfile.NamedTemporaryFile(
                suffix=".html", delete=False, mode="w"
            ) as f:
                f.write(html_content)
                html_path = f.name
            pdf_path = html_path.replace(".html", ".pdf")
            subprocess.run(
                ["wkhtmltopdf", "--quiet", html_path, pdf_path],
                check=True,
                capture_output=True,
            )
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
            Path(html_path).unlink(missing_ok=True)
            Path(pdf_path).unlink(missing_ok=True)
            return pdf_bytes
        except FileNotFoundError:
            raise RuntimeError("PDF 导出需要安装 wkhtmltopdf: apt install wkhtmltopdf")
        except Exception as e:
            raise RuntimeError(f"PDF 导出失败: {e}")


# 全局单例
_exporter: Exporter | None = None


def get_exporter() -> Exporter:
    global _exporter
    if _exporter is None:
        _exporter = Exporter()
    return _exporter
