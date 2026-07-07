"""SQL 脚本解析器（基于 sqlparse + 正则）

拆分多语句，识别 DDL/DML，提取表名/字段名实体候选。
"""

from __future__ import annotations

import hashlib
import re

import sqlparse

from app.parsers.base import ElementType, ParsedDocument, ParsedElement


class SQLParser:
    format = "sql"

    def parse(self, path: str, doc_id: str) -> ParsedDocument:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()

        checksum = hashlib.sha256(text.encode()).hexdigest()
        elements = self._parse_sql(text)

        return ParsedDocument(
            doc_id=doc_id,
            source_path=path,
            format="sql",
            checksum=checksum,
            title=doc_id,
            elements=elements,
        )

    def _parse_sql(self, text: str) -> list[ParsedElement]:
        elements: list[ParsedElement] = []
        statements = sqlparse.split(text)

        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue

            # 去除注释后再分类
            stmt_clean = re.sub(r"--.*$", "", stmt, flags=re.MULTILINE).strip()
            stmt_upper = stmt_clean.upper().lstrip()
            # 分类
            is_ddl = any(
                stmt_upper.startswith(kw)
                for kw in ("CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME")
            )
            is_dml = any(
                stmt_upper.startswith(kw)
                for kw in ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE")
            )
            is_dcl = any(stmt_upper.startswith(kw) for kw in ("GRANT", "REVOKE"))
            is_tcl = any(
                stmt_upper.startswith(kw)
                for kw in ("COMMIT", "ROLLBACK", "SAVEPOINT", "BEGIN")
            )

            category = (
                "ddl"
                if is_ddl
                else (
                    "dml"
                    if is_dml
                    else ("dcl" if is_dcl else ("tcl" if is_tcl else "other"))
                )
            )

            # 提取表名
            tables = self._extract_tables(stmt, category)

            # 提取注释
            comments = self._extract_comments(stmt)

            elements.append(
                ParsedElement(
                    type=ElementType.SQL_STATEMENT,
                    content=stmt,
                    metadata={
                        "category": category,
                        "tables": tables,
                        "comments": comments,
                    },
                )
            )

        return elements

    def _extract_tables(self, stmt: str, category: str) -> list[str]:
        tables: list[str] = []
        # CREATE TABLE xxx
        m = re.search(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w[\w.]*)", stmt, re.IGNORECASE
        )
        if m:
            tables.append(m.group(1))
        # ALTER TABLE xxx
        for m in re.finditer(r"ALTER\s+TABLE\s+(\w[\w.]*)", stmt, re.IGNORECASE):
            tables.append(m.group(1))
        # INSERT INTO xxx
        m = re.search(r"INSERT\s+INTO\s+(\w[\w.]*)", stmt, re.IGNORECASE)
        if m:
            tables.append(m.group(1))
        # FROM xxx / JOIN xxx
        for m in re.finditer(r"(?:FROM|JOIN)\s+(\w[\w.]*)", stmt, re.IGNORECASE):
            tables.append(m.group(1))
        # UPDATE xxx
        m = re.search(r"UPDATE\s+(\w[\w.]*)", stmt, re.IGNORECASE)
        if m:
            tables.append(m.group(1))
        return list(dict.fromkeys(tables))  # 去重保序

    def _extract_comments(self, stmt: str) -> list[str]:
        return re.findall(r"--\s*(.+?)(?:\n|$)", stmt)
