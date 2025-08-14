from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

class AdapterTransientError(RuntimeError): ...
class AdapterPermanentError(RuntimeError): ...

@dataclass(frozen=True)
class Token:
    run_id: str
    algo: str
    product_id: str
    variant: str
    image_files: List[str]  # relpaths under workspace (prefixed with 'dataset/…')
    img_suffixes: str
    job_id: str

@dataclass
class ExecResult:
    glb_path: Path
    timings: Dict[str, Any]  # raw timings if provider returns them
    request_id: str | None

class ModelAdapter:
    def __init__(self, cfg: Dict[str, Any], workspace: Path, logs_dir: Path):
        self.cfg = cfg
        self.workspace = workspace
        self.logs_dir = logs_dir

    def unit_price_usd(self) -> float:
        return float(self.cfg.get("unit_price_usd", 0.0))

    def price_source(self) -> str:
        return str(self.cfg.get("price_source", "unknown"))

    def execute(self, token: Token, deadline_s: int = 480) -> ExecResult:
        """Full execution hook: upload/prepare → invoke → materialize."""
        raise NotImplementedError
