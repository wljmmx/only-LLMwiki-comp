"""统一 API 响应格式与分页模型。

提供：
- ApiResponse[T]：通用成功响应 { code, data, message }
- PaginatedResponse[T]：分页响应，data 包含 { items, total, page, page_size }
- PageParams：请求端分页参数 { page, page_size }
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """通用 API 成功响应。

    所有端点统一使用此格式返回数据。
    - code=0 表示成功
    - data 承载实际业务数据
    - message 承载补充信息（成功时为空或提示）
    """

    code: int = 0
    data: T
    message: str = ""


class PaginatedData(BaseModel, Generic[T]):
    """分页数据载荷"""

    items: list[T]
    total: int
    page: int
    page_size: int


class PaginatedResponse(ApiResponse[PaginatedData[T]], Generic[T]):
    """分页 API 响应。

    继承 ApiResponse，data 为 PaginatedData 结构。
    使用示例：

        PaginatedResponse[DocumentItem](
            data=PaginatedData(
                items=docs,
                total=total_count,
                page=page,
                page_size=page_size,
            )
        )
    """

    data: PaginatedData[T]


class PageParams(BaseModel):
    """请求端分页参数。

    可作为 FastAPI 依赖注入或直接作为 Query 参数的基类。
    - page: 页码（1-based）
    - page_size: 每页条数（1-100）
    """

    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=20, ge=1, le=100, description="每页条数")

