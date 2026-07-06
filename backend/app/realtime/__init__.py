"""S15-5 实时协作模块

基于 WebSocket 的轻量化协作能力：
- 在线用户列表（presence）
- 编辑锁（同一页面同时只允许一人编辑，避免覆盖）
- 编辑事件广播（cursor / selection / 增量编辑提示）

设计取舍：
- 不引入 CRDT/OT 算法（复杂度过高，审计标注"非核心，可延后"）
- 编辑锁为"软锁"：客户端获得锁后可编辑，保存时仍走 VersionControl
  版本号乐观校验，避免破坏现有版本控制语义
- WebSocket 鉴权复用 verify_token_string（从 query param 取 token）
"""

from app.realtime.collab_hub import CollabHub, CollabRoom, get_collab_hub

__all__ = ["CollabHub", "CollabRoom", "get_collab_hub"]
