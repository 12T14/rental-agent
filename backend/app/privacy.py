"""本地单用户模式的数据清理边界。

这里只处理明确允许清理的目录和文件，不把任意路径交给 API 调用方。
"""

from __future__ import annotations

import shutil
from pathlib import Path

try:
    from ..agent_core.batch_detail_tool import _DETAIL_VERIFICATION_REQUESTS
    from ..agent_core.human_verification_tool import _LAST_ATTEMPT_AT, _session_path
except ImportError:  # pragma: no cover - backend 目录作为 sys.path 根运行时使用。
    from agent_core.batch_detail_tool import _DETAIL_VERIFICATION_REQUESTS
    from agent_core.human_verification_tool import _LAST_ATTEMPT_AT, _session_path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = BACKEND_ROOT / "artifacts"
PLATFORMS = ("58", "anjuke", "fang")


def clear_platform_sessions() -> dict[str, object]:
    """删除三个平台的本机 Storage State，不删除聊天或抓取结果。"""

    cleared: list[str] = []
    for platform in PLATFORMS:
        path = _session_path(platform)
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                cleared.append(platform)
        except OSError as exc:
            raise RuntimeError("平台验证数据清理失败") from exc
    _LAST_ATTEMPT_AT.clear()
    _DETAIL_VERIFICATION_REQUESTS.clear()
    return {"cleared_platforms": cleared, "cleared_count": len(cleared)}


def clear_artifacts(artifact_dir: Path | None = None) -> dict[str, int]:
    """清空固定的 backend/artifacts 目录，保留目录本身。"""

    root = artifact_dir or ARTIFACT_DIR
    root.mkdir(parents=True, exist_ok=True)
    removed_files = 0
    removed_directories = 0
    try:
        for child in list(root.iterdir()):
            if child.is_symlink() or child.is_file():
                child.unlink()
                removed_files += 1
            elif child.is_dir():
                removed_files += sum(1 for item in child.rglob("*") if item.is_file())
                shutil.rmtree(child)
                removed_directories += 1
    except OSError as exc:
        raise RuntimeError("本地抓取产物清理失败") from exc
    return {"removed_files": removed_files, "removed_directories": removed_directories}
