"""What every capture source produces."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class Document:
    text: str
    title: str = ""
    uri: str = ""
    source: str = "text"  # selection | clipboard | uia | file | ocr | claude
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.title:
            first = next((ln.strip() for ln in self.text.splitlines() if ln.strip()), "")
            self.title = (first[:80] + "…") if len(first) > 80 else first or "Untitled"
        if not self.uri:
            # Content-addressed, so re-reading the same pasted text finds the
            # same history row and resumes where it left off.
            digest = hashlib.sha1(self.text.encode("utf-8", "ignore")).hexdigest()[:16]
            self.uri = "earmark:" + self.source + "/" + digest

    @property
    def snippet(self) -> str:
        flat = " ".join(self.text.split())
        return flat[:200]

    def __len__(self) -> int:
        return len(self.text)
