from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class RawStore:
    """Append-only store for raw collected files with SHA-256 sidecars."""

    def __init__(self, base_path: Path) -> None:
        self.base_path = Path(base_path)

    def persist(
        self,
        content: bytes,
        source_id: str,
        filename: str,
        trade_date: Optional[str] = None,
        source_url: Optional[str] = None,
        http_status: Optional[int] = None,
        content_type: Optional[str] = None,
    ) -> tuple[Path, str, int]:
        """Write raw bytes and a JSON sidecar. Returns (file_path, sha256, byte_size)."""
        retrieval_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dest_dir = self.base_path / "raw" / source_id / retrieval_date
        dest_dir.mkdir(parents=True, exist_ok=True)

        sha256 = hashlib.sha256(content).hexdigest()
        byte_size = len(content)
        retrieved_at = datetime.now(timezone.utc).isoformat()

        file_path = dest_dir / filename
        file_path.write_bytes(content)

        meta = {
            "sha256": sha256,
            "byte_size": byte_size,
            "retrieved_at": retrieved_at,
            "source_id": source_id,
            "trade_date": trade_date,
            "source_url": source_url,
            "http_status": http_status,
            "content_type": content_type,
            "filename": filename,
        }
        meta_path = file_path.with_suffix(file_path.suffix + ".meta.json")
        meta_path.write_text(json.dumps(meta, indent=2))

        return file_path, sha256, byte_size
