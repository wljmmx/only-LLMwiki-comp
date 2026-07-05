"""Pytest 全局配置（S7-2）

确保 `from app.main import app` 能正确解析，
无论 pytest 从哪个目录启动。
"""
import sys
from pathlib import Path

# 将 backend/ 目录加入 sys.path，使 app 包可被导入
backend_root = Path(__file__).parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))
