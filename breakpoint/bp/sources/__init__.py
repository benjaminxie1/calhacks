"""Typed, key-checked access to the source systems for in-process components."""
from bp.sources.api import KEYS, authorize
from bp.sources.store import SourceStore


class Sources:
    """Hands out a source's store view only to holders of that source's key."""

    def __init__(self, store: SourceStore, keys: dict[str, str] | None = None):
        self._store = store
        self._keys = dict(KEYS if keys is None else keys)

    def __call__(self, source: str) -> SourceStore:
        authorize(source, self._keys.get(source))
        return self._store

    @property
    def store(self) -> SourceStore:  # for the truth-reading personas and the eval only
        return self._store
