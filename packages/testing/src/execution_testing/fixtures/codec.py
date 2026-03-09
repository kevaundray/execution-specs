"""
Fixture codec abstraction.

Provides a protocol for serializing/deserializing fixture data,
with JSON and RLP implementations.
"""

import json
import struct
from typing import Any, Dict, Iterator, List, Protocol, Sequence, Tuple, Union

from ethereum_rlp import decode as rlp_decode
from ethereum_rlp import encode as rlp_encode
from ethereum_types.numeric import Uint

# RLP type tags for encoding JSON values
_TAG_STRING = b"\x00"
_TAG_INT = b"\x01"
_TAG_TRUE = b"\x02"
_TAG_FALSE = b"\x03"
_TAG_NULL = b"\x04"
_TAG_ARRAY = b"\x05"
_TAG_DICT = b"\x06"

Simple = Union[bytes, List["Simple"]]


def _json_to_rlp(value: Any) -> Sequence:
    """Convert a JSON-compatible value to an RLP-encodable structure."""
    if isinstance(value, str):
        return [_TAG_STRING, value.encode("utf-8")]
    elif isinstance(value, bool):
        return [_TAG_TRUE] if value else [_TAG_FALSE]
    elif isinstance(value, int):
        return [_TAG_INT, Uint(value).to_be_bytes() if value else b""]
    elif value is None:
        return [_TAG_NULL]
    elif isinstance(value, list):
        return [_TAG_ARRAY] + [_json_to_rlp(item) for item in value]
    elif isinstance(value, dict):
        pairs = []
        for k in sorted(value.keys()):
            pairs.append([k.encode("utf-8"), _json_to_rlp(value[k])])
        return [_TAG_DICT] + pairs
    else:
        raise TypeError(
            f"unsupported type for RLP fixture encoding: {type(value)}"
        )


def _rlp_to_json(value: Simple) -> Any:
    """Convert a decoded RLP structure back to a JSON-compatible value."""
    assert isinstance(value, (list, tuple))
    tag = value[0]
    assert isinstance(tag, bytes)

    if tag == _TAG_STRING:
        assert isinstance(value[1], bytes)
        return value[1].decode("utf-8")
    elif tag == _TAG_INT:
        assert isinstance(value[1], bytes)
        if len(value[1]) == 0:
            return 0
        return int(Uint.from_be_bytes(value[1]))
    elif tag == _TAG_TRUE:
        return True
    elif tag == _TAG_FALSE:
        return False
    elif tag == _TAG_NULL:
        return None
    elif tag == _TAG_ARRAY:
        return [_rlp_to_json(item) for item in value[1:]]
    elif tag == _TAG_DICT:
        result = {}
        for pair in value[1:]:
            assert isinstance(pair, (list, tuple))
            assert isinstance(pair[0], bytes)
            key = pair[0].decode("utf-8")
            result[key] = _rlp_to_json(pair[1])
        return result
    else:
        raise ValueError(f"unknown RLP fixture tag: {tag!r}")


class FixtureCodec(Protocol):
    """Protocol for fixture serialization formats."""

    @property
    def suffix(self) -> str:
        """File extension for fixture files (e.g. '.json', '.rlp')."""
        ...

    @property
    def partial_suffix(self) -> str:
        """File extension for partial streaming files."""
        ...

    def load_fixtures(self, data: bytes) -> Dict[str, Any]:
        """Deserialize fixture data from bytes."""
        ...

    def dump_fixtures(self, fixtures: Dict[str, Any]) -> bytes:
        """Serialize fixture data to bytes."""
        ...

    def deterministic_bytes(self, value: Dict[str, Any]) -> bytes:
        """Produce deterministic bytes for hashing."""
        ...

    def dump_partial_entry(self, key: str, value_str: str) -> bytes:
        """Serialize a single streaming entry (key + serialized value)."""
        ...

    def load_partial_entries(self, data: bytes) -> Iterator[Tuple[str, str]]:
        """Deserialize streaming entries from bytes."""
        ...

    def dump_index_entry(self, entry: Dict[str, Any]) -> bytes:
        """Serialize a single index entry for streaming."""
        ...

    def load_index_entries(self, data: bytes) -> Iterator[Dict[str, Any]]:
        """Deserialize index entries from bytes."""
        ...


class JsonCodec:
    """JSON fixture codec."""

    @property
    def suffix(self) -> str:  # noqa: D102
        return ".json"

    @property
    def partial_suffix(self) -> str:  # noqa: D102
        return ".jsonl"

    def load_fixtures(self, data: bytes) -> Dict[str, Any]:  # noqa: D102
        return json.loads(data)

    def dump_fixtures(self, fixtures: Dict[str, Any]) -> bytes:  # noqa: D102
        return json.dumps(
            dict(sorted(fixtures.items())), indent=4
        ).encode("utf-8")

    def deterministic_bytes(  # noqa: D102
        self, value: Dict[str, Any]
    ) -> bytes:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def dump_partial_entry(  # noqa: D102
        self, key: str, value_str: str
    ) -> bytes:
        return (
            json.dumps({"k": key, "v": value_str}) + "\n"
        ).encode("utf-8")

    def load_partial_entries(  # noqa: D102
        self, data: bytes
    ) -> Iterator[Tuple[str, str]]:
        for line in data.decode("utf-8").splitlines():
            line = line.strip()
            if line:
                entry = json.loads(line)
                yield (entry["k"], entry["v"])

    def dump_index_entry(  # noqa: D102
        self, entry: Dict[str, Any]
    ) -> bytes:
        return (json.dumps(entry) + "\n").encode("utf-8")

    def load_index_entries(  # noqa: D102
        self, data: bytes
    ) -> Iterator[Dict[str, Any]]:
        for line in data.decode("utf-8").splitlines():
            line = line.strip()
            if line:
                yield json.loads(line)


class RlpCodec:
    """RLP fixture codec."""

    @property
    def suffix(self) -> str:  # noqa: D102
        return ".rlp"

    @property
    def partial_suffix(self) -> str:  # noqa: D102
        return ".rlpl"

    def load_fixtures(  # noqa: D102
        self, data: bytes
    ) -> Dict[str, Any]:
        decoded = rlp_decode(data)
        return _rlp_to_json(decoded)

    def dump_fixtures(  # noqa: D102
        self, fixtures: Dict[str, Any]
    ) -> bytes:
        return rlp_encode(_json_to_rlp(fixtures))

    def deterministic_bytes(  # noqa: D102
        self, value: Dict[str, Any]
    ) -> bytes:
        return rlp_encode(_json_to_rlp(value))

    def dump_partial_entry(  # noqa: D102
        self, key: str, value_str: str
    ) -> bytes:
        blob = rlp_encode(
            [key.encode("utf-8"), value_str.encode("utf-8")]
        )
        return struct.pack(">I", len(blob)) + blob

    def load_partial_entries(  # noqa: D102
        self, data: bytes
    ) -> Iterator[Tuple[str, str]]:
        offset = 0
        while offset < len(data):
            (length,) = struct.unpack(
                ">I", data[offset : offset + 4]
            )
            offset += 4
            blob = data[offset : offset + length]
            offset += length
            decoded = rlp_decode(blob)
            assert isinstance(decoded, (list, tuple))
            assert len(decoded) == 2
            key = decoded[0]
            value = decoded[1]
            assert isinstance(key, bytes)
            assert isinstance(value, bytes)
            yield (key.decode("utf-8"), value.decode("utf-8"))

    def dump_index_entry(  # noqa: D102
        self, entry: Dict[str, Any]
    ) -> bytes:
        blob = rlp_encode(_json_to_rlp(entry))
        return struct.pack(">I", len(blob)) + blob

    def load_index_entries(  # noqa: D102
        self, data: bytes
    ) -> Iterator[Dict[str, Any]]:
        offset = 0
        while offset < len(data):
            (length,) = struct.unpack(
                ">I", data[offset : offset + 4]
            )
            offset += 4
            blob = data[offset : offset + length]
            offset += length
            decoded = rlp_decode(blob)
            yield _rlp_to_json(decoded)


# Global codec registry
_CODECS: Dict[str, FixtureCodec] = {
    "json": JsonCodec(),
    "rlp": RlpCodec(),
}

_EXTENSION_MAP: Dict[str, str] = {
    ".json": "json",
    ".rlp": "rlp",
}


def get_codec(name: str) -> FixtureCodec:
    """Get a codec by name."""
    if name not in _CODECS:
        raise ValueError(
            f"Unknown codec: {name!r}. Available: {list(_CODECS.keys())}"
        )
    return _CODECS[name]


def get_codec_for_extension(ext: str) -> FixtureCodec:
    """Get the appropriate codec for a file extension."""
    codec_name = _EXTENSION_MAP.get(ext)
    if codec_name is None:
        raise ValueError(
            f"No codec for extension: {ext!r}. "
            f"Known: {list(_EXTENSION_MAP.keys())}"
        )
    return _CODECS[codec_name]
