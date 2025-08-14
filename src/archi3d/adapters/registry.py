from __future__ import annotations
from typing import Dict, Type
from .base import ModelAdapter
from .trellis import TrellisMultiAdapter

REGISTRY: Dict[str, Type[ModelAdapter]] = {
    "trellis_multi_stochastic": TrellisMultiAdapter,
    "trellis_multi_multidiffusion": TrellisMultiAdapter,
    # other keys will be added later
}
