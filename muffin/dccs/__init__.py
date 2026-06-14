"""DCC plugin registry. Add a new DCC by importing it and registering an
instance here."""

from typing import Optional

from .base import DCC
from .blender import BlenderDCC
from .houdini import HoudiniDCC
from .kick import KickDCC
from .maya import MayaDCC

_REGISTRY: dict[str, DCC] = {
    p.name: p for p in (BlenderDCC(), MayaDCC(), HoudiniDCC(), KickDCC())
}


def get(name: str) -> Optional[DCC]:
    return _REGISTRY.get(name.lower())


def all_dccs() -> dict[str, DCC]:
    return dict(_REGISTRY)


def catalog() -> list[dict]:
    """Describe every DCC for the UI: name, renderers, installed-on-this-host."""
    return [
        {"name": p.name, "renderers": p.renderers, "installed": p.is_installed()}
        for p in _REGISTRY.values()
    ]
