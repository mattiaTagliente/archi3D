from __future__ import annotations
from typing import Dict, Type
from archi3d.adapters.base import ModelAdapter
from archi3d.adapters.trellis import TrellisMultiAdapter
from archi3d.adapters.tripo3d import Tripo3DMultiV2p5Adapter

REGISTRY: Dict[str, Type[ModelAdapter]] = {
    "trellis_multi_stochastic": TrellisMultiAdapter,
    "trellis_multi_multidiffusion": TrellisMultiAdapter,
    "tripo3d_v2p5_multi": Tripo3DMultiV2p5Adapter,
    # other keys will be added later
}