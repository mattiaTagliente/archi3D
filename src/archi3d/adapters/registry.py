from __future__ import annotations
from typing import Dict, Type
from archi3d.adapters.base import ModelAdapter
from archi3d.adapters.trellis import TrellisMultiAdapter
from archi3d.adapters.tripo3d import Tripo3DMultiV2p5Adapter
from archi3d.adapters.rodin import RodinMultiAdapter
from archi3d.adapters.hunyuan3d import Hunyuan3DMultiviewV2Adapter

REGISTRY: Dict[str, Type[ModelAdapter]] = {
    "trellis_multi_stochastic": TrellisMultiAdapter,
    "trellis_multi_multidiffusion": TrellisMultiAdapter,
    "tripo3d_v2p5_multi": Tripo3DMultiV2p5Adapter,
    "rodin_multi": RodinMultiAdapter,
    "hunyuan3d_v2_multi": Hunyuan3DMultiviewV2Adapter,
    # other keys will be added later
}