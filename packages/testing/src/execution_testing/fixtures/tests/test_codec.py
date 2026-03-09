"""Tests for fixture codec abstraction."""

import pytest

from execution_testing.fixtures.codec import (
    FixtureCodec,
    JsonCodec,
    RlpCodec,
    get_codec,
    get_codec_for_extension,
)


SAMPLE_FIXTURE = {
    "test_case_one": {
        "env": {
            "currentCoinbase": "0x2adc25665018aa1fe0e6bc666dac8fc2697ff9ba",
            "currentGasLimit": "0x055d4a80",
            "currentNumber": "0x01",
        },
        "pre": {
            "0xaddr1": {
                "nonce": "0x01",
                "balance": "0x00",
                "code": "0x36600055",
                "storage": {},
            }
        },
    }
}


class TestJsonCodec:
    """Test the JSON codec implementation."""

    def test_round_trip(self) -> None:
        codec = JsonCodec()
        encoded = codec.dump_fixtures(SAMPLE_FIXTURE)
        decoded = codec.load_fixtures(encoded)
        assert decoded == SAMPLE_FIXTURE

    def test_suffix(self) -> None:
        assert JsonCodec().suffix == ".json"

    def test_partial_suffix(self) -> None:
        assert JsonCodec().partial_suffix == ".jsonl"

    def test_deterministic_hash(self) -> None:
        codec = JsonCodec()
        h1 = codec.deterministic_bytes({"b": 2, "a": 1})
        h2 = codec.deterministic_bytes({"a": 1, "b": 2})
        assert h1 == h2

    def test_dump_partial_entry(self) -> None:
        codec = JsonCodec()
        data = codec.dump_partial_entry("key1", '{"x": 1}')
        entries = list(codec.load_partial_entries(data))
        assert len(entries) == 1
        assert entries[0] == ("key1", '{"x": 1}')

    def test_dump_fixtures_human_readable(self) -> None:
        """JSON output should be indented for readability."""
        codec = JsonCodec()
        encoded = codec.dump_fixtures({"a": 1})
        text = encoded.decode("utf-8")
        assert "\n" in text


class TestRlpCodec:
    """Test the RLP codec implementation."""

    def test_round_trip(self) -> None:
        codec = RlpCodec()
        encoded = codec.dump_fixtures(SAMPLE_FIXTURE)
        decoded = codec.load_fixtures(encoded)
        assert decoded == SAMPLE_FIXTURE

    def test_suffix(self) -> None:
        assert RlpCodec().suffix == ".rlp"

    def test_partial_suffix(self) -> None:
        assert RlpCodec().partial_suffix == ".rlpl"

    def test_deterministic_hash(self) -> None:
        codec = RlpCodec()
        h1 = codec.deterministic_bytes({"b": 2, "a": 1})
        h2 = codec.deterministic_bytes({"a": 1, "b": 2})
        assert h1 == h2

    def test_dump_partial_entry(self) -> None:
        codec = RlpCodec()
        data = codec.dump_partial_entry("key1", '{"x": 1}')
        entries = list(codec.load_partial_entries(data))
        assert len(entries) == 1
        assert entries[0] == ("key1", '{"x": 1}')

    def test_smaller_than_json(self) -> None:
        """RLP should produce smaller output than JSON."""
        json_size = len(JsonCodec().dump_fixtures(SAMPLE_FIXTURE))
        rlp_size = len(RlpCodec().dump_fixtures(SAMPLE_FIXTURE))
        assert rlp_size < json_size


class TestGetCodec:
    """Test codec selection helpers."""

    def test_get_json_codec(self) -> None:
        assert isinstance(get_codec("json"), JsonCodec)

    def test_get_rlp_codec(self) -> None:
        assert isinstance(get_codec("rlp"), RlpCodec)

    def test_get_unknown_codec(self) -> None:
        with pytest.raises(ValueError, match="Unknown codec"):
            get_codec("xml")

    def test_get_codec_for_json_extension(self) -> None:
        assert isinstance(get_codec_for_extension(".json"), JsonCodec)

    def test_get_codec_for_rlp_extension(self) -> None:
        assert isinstance(get_codec_for_extension(".rlp"), RlpCodec)


class TestRoundTripWithRealFixture:
    """Test codec round-trip with a realistic fixture structure."""

    REALISTIC_FIXTURE = {
        "tests/frontier/test_example.py::test_one[fork_Berlin-state_test]": {
            "env": {
                "currentCoinbase": "0x2adc25665018aa1fe0e6bc666dac8fc2697ff9ba",
                "currentGasLimit": "0x055d4a80",
                "currentNumber": "0x01",
                "currentTimestamp": "0x03e8",
                "currentDifficulty": "0x020000",
            },
            "pre": {
                "0xb8fca6485226caff87613e36a797d35fdb3c0346": {
                    "nonce": "0x01",
                    "balance": "0x00",
                    "code": "0x36600055",
                    "storage": {},
                },
                "0x4e88de48f3d2b92594ee13fa916b23325ddbd6e1": {
                    "nonce": "0x00",
                    "balance": "0x3635c9adc5dea00000",
                    "code": "0x",
                    "storage": {"0x01": "0xff"},
                },
            },
            "transaction": {
                "nonce": "0x00",
                "gasPrice": "0x0a",
                "gasLimit": ["0x0186a0"],
                "to": "0xb8fca6485226caff87613e36a797d35fdb3c0446",
                "value": ["0x00"],
                "data": ["0x"],
            },
            "post": {
                "Berlin": [
                    {
                        "hash": "0x6eabad0466d8afd78bfa38cdd65b3fad7d78f933",
                        "logs": "0x1dcc4de8dec75d7aab85b567b6ccd41ad312451b",
                        "indexes": {"data": 0, "gas": 0, "value": 0},
                        "state": {},
                        "expectException": None,
                    }
                ]
            },
            "_info": {
                "hash": "0xabc123",
                "fixture-format": "state_test",
            },
        }
    }

    @pytest.mark.parametrize("codec_name", ["json", "rlp"])
    def test_realistic_round_trip(self, codec_name: str) -> None:
        codec = get_codec(codec_name)
        encoded = codec.dump_fixtures(self.REALISTIC_FIXTURE)
        decoded = codec.load_fixtures(encoded)
        assert decoded == self.REALISTIC_FIXTURE

    @pytest.mark.parametrize("codec_name", ["json", "rlp"])
    def test_partial_streaming_round_trip(self, codec_name: str) -> None:
        """Test that partial streaming preserves data."""
        codec = get_codec(codec_name)
        entries = [
            ("id_1", '{"env": {"a": "1"}}'),
            ("id_2", '{"env": {"b": "2"}}'),
        ]
        raw = b""
        for key, value in entries:
            raw += codec.dump_partial_entry(key, value)
        loaded = list(codec.load_partial_entries(raw))
        assert loaded == entries
