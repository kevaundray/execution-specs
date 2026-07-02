from typing import Dict, List, Tuple

def state_root(
    accounts: List[Tuple[bytes, bytes, bytes, bytes]],
    storage: Dict[bytes, List[Tuple[bytes, bytes]]],
) -> bytes: ...

__all__ = ("state_root",)
