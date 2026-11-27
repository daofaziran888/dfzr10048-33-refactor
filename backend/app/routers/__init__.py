"""业务路由汇总：交接专用路由先于通用资源路由注册，避免固定路径被动态模块路径抢占。"""
from __future__ import annotations

from app.routers import handoff, resource

ROUTERS = [handoff, resource]
