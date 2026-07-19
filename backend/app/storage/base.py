"""P2: 存储层抽象基类

定义 DocumentRepository 接口，为后续多存储后端（SQLite / PostgreSQL / S3）提供统一契约。
"""

from abc import ABC, abstractmethod


class DocumentRepository(ABC):
    @abstractmethod
    def get(self, doc_id: str) -> dict | None: ...

    @abstractmethod
    def save(self, filename: str, content: bytes, fmt: str) -> dict: ...

    @abstractmethod
    def delete(self, doc_id: str) -> bool: ...
