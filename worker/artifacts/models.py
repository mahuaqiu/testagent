"""附件模型。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ArtifactRef:
    """附件引用，不包含二进制内容。"""

    artifact_id: str
    task_id: str
    artifact_type: str
    mime_type: str
    relative_path: str
    size: int
    sha256: str
    created_at: datetime
    expires_at: datetime
    action_number: int | None = None

    def to_dict(self) -> dict[str, object]:
        """转换为接口结构。"""
        return {
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "action_number": self.action_number,
            "type": self.artifact_type,
            "mime_type": self.mime_type,
            "path": self.relative_path,
            "size": self.size,
            "sha256": self.sha256,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }
