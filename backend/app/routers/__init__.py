"""API 路由子模块聚合。

按业务域分组的 APIRouter 子模块。每个子模块导出 `router`，由 main.py 通过
`app.include_router(...)` 聚合注册。
"""
