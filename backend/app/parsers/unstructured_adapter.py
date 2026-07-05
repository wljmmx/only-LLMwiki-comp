"""Unstructured 备选适配器（Fallback）

当 MarkItDown 或 MinerU 不可用时启用。支持 20+ 格式，企业级 ETL。
"""

from __future__ import annotations

import hashlib
import structlog
from typing import Callable

from app.parsers.base import DocumentParser, ElementType, ParsedDocument, ParsedElement

logger = structlog.get_logger()


class UnstructuredAdapter:
    """Unstructured 备选解析器"""

    def __init__(self, source_format: str) -> None:
        self.format = source_format

    def parse(self, path: str, doc_id: str) -> ParsedDocument:
        with open(path, "rb") as f:
            checksum = hashlib.sha256(f.read()).hexdigest()

        try:
            from unstructured.partition.auto import partition

            elements = partition(filename=path)

            parsed_elements: list[ParsedElement] = []
            current_section: str | None = None

            for e in elements:
                category = str(e.category)
                if category in ("Title", "Header"):
                    parsed_elements.append(
                        ParsedElement(
                            type=ElementType.HEADING,
                            content=str(e),
                            metadata={"level": 1 if category == "Title" else 2},
                        )
                    )
                    current_section = str(e)
                elif category == "Table":
                    parsed_elements.append(
                        ParsedElement(
                            type=ElementType.TABLE,
                            content=str(e),
                            section=current_section,
                        )
                    )
                elif category in ("ListItem", "BulletedText", "NumberedListItem"):
                    parsed_elements.append(
                        ParsedElement(
                            type=ElementType.LIST,
                            content=str(e),
                            section=current_section,
                        )
                    )
                elif category in ("Code", "CodeBlock"):
                    parsed_elements.append(
                        ParsedElement(
                            type=ElementType.CODE,
                            content=str(e),
                            section=current_section,
                        )
                    )
                else:
                    parsed_elements.append(
                        ParsedElement(
                            type=ElementType.PARAGRAPH,
                            content=str(e),
                            section=current_section,
                        )
                    )

            title = str(elements[0]) if elements else None
            return ParsedDocument(
                doc_id=doc_id,
                source_path=path,
                format=self.format,
                checksum=checksum,
                title=title,
                elements=parsed_elements,
            )
        except ImportError:
            logger.warning("unstructured_not_installed")
            raise RuntimeError(
                "Unstructured 未安装，请 pip install 'unstructured[docx,xlsx]'"
            )
        except Exception as e:
            logger.error("unstructured_parse_failed", error=str(e))
            raise


def make_unstructured_factory(fmt: str) -> Callable[[], DocumentParser]:
    return lambda: UnstructuredAdapter(fmt)
