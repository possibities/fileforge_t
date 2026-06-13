import sys
import types
import unittest

from constants import METADATA_SCHEMA


# Keep imports lightweight if optional SDKs are absent in the test environment.
if "openai" not in sys.modules:
    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = object
    sys.modules["openai"] = openai_stub

from infrastructure.llm_client import (
    PARSE_STRATEGY_FAILED,
    PARSE_STRATEGY_JSON,
    PARSE_STRATEGY_REGEX,
    PARSE_STRATEGY_REPAIRED,
    LlmClient,
)


class TestLlmClientRegexFallback(unittest.TestCase):
    def _build_client(self):
        client = LlmClient.__new__(LlmClient)
        client.metadata_schema = METADATA_SCHEMA
        return client

    def test_extract_fields_by_regex_parses_null_string_and_number(self):
        client = self._build_client()
        fields = list(METADATA_SCHEMA.keys())
        key_text = fields[0]
        key_year = fields[1]
        key_note = fields[11]

        raw = (
            "{"
            f"\"{key_text}\": \"value\", "
            f"\"{key_year}\": 2020, "
            f"\"{key_note}\": null"
            "}"
        )
        metadata = client._extract_fields_by_regex(raw)

        self.assertEqual(metadata[key_text], "value")
        self.assertEqual(metadata[key_year], 2020)
        self.assertIsNone(metadata[key_note])

    def test_extract_fields_by_regex_parses_bool_float_and_array(self):
        client = self._build_client()
        fields = list(METADATA_SCHEMA.keys())
        key_bool = fields[12]
        key_float = fields[10]
        key_array = fields[9]

        raw = (
            "{"
            f"\"{key_bool}\": true, "
            f"\"{key_float}\": 1.5, "
            f"\"{key_array}\": [\"a\", \"b\"]"
            "}"
        )
        metadata = client._extract_fields_by_regex(raw)

        self.assertIs(metadata[key_bool], True)
        self.assertEqual(metadata[key_float], 1.5)
        self.assertEqual(metadata[key_array], ["a", "b"])

    def test_extract_fields_by_regex_ignores_unknown_keys(self):
        client = self._build_client()
        key_text = list(METADATA_SCHEMA.keys())[0]
        raw = "{ " f"\"{key_text}\": \"ok\", \"unknown_key\": \"drop\" " "}"

        metadata = client._extract_fields_by_regex(raw)

        self.assertIn(key_text, metadata)
        self.assertNotIn("unknown_key", metadata)

    def test_parse_json_falls_back_to_field_extraction_on_truncated_payload(self):
        client = self._build_client()
        key_text = list(METADATA_SCHEMA.keys())[0]
        key_year = list(METADATA_SCHEMA.keys())[1]
        raw = (
            "{"
            f"\"{key_text}\": \"ok\", "
            f"\"{key_year}\": 2020, "
            "\"unknown\": "
        )

        metadata, strategy = client._parse_json(raw)

        self.assertEqual(metadata[key_text], "ok")
        self.assertEqual(metadata[key_year], 2020)
        self.assertEqual(strategy, PARSE_STRATEGY_REGEX)


class TestLlmClientParseStrategy(unittest.TestCase):
    """覆盖 _parse_json 的四档解析路径,strategy 标签必须与数据库列对齐。"""

    def _build_client(self):
        client = LlmClient.__new__(LlmClient)
        client.metadata_schema = METADATA_SCHEMA
        return client

    def test_strategy_json_on_clean_payload(self):
        client = self._build_client()
        key = list(METADATA_SCHEMA.keys())[0]
        raw = "{" f"\"{key}\": \"hello\"" "}"
        metadata, strategy = client._parse_json(raw)
        self.assertEqual(strategy, PARSE_STRATEGY_JSON)
        self.assertEqual(metadata[key], "hello")

    def test_strategy_repaired_on_trailing_comma(self):
        client = self._build_client()
        key = list(METADATA_SCHEMA.keys())[0]
        raw = "{" f"\"{key}\": \"v\"," "}"
        metadata, strategy = client._parse_json(raw)
        self.assertEqual(strategy, PARSE_STRATEGY_REPAIRED)
        self.assertEqual(metadata[key], "v")

    def test_strategy_regex_on_truncated_payload(self):
        client = self._build_client()
        key_a = list(METADATA_SCHEMA.keys())[0]
        key_b = list(METADATA_SCHEMA.keys())[1]
        raw = "{" f"\"{key_a}\": \"x\", \"{key_b}\": 2020, \"unknown\": "
        metadata, strategy = client._parse_json(raw)
        self.assertEqual(strategy, PARSE_STRATEGY_REGEX)
        self.assertEqual(metadata[key_a], "x")
        self.assertEqual(metadata[key_b], 2020)

    def test_strategy_failed_on_pure_garbage(self):
        client = self._build_client()
        metadata, strategy = client._parse_json("not json at all")
        self.assertEqual(strategy, PARSE_STRATEGY_FAILED)
        self.assertEqual(metadata, {})


class TestLlmClientRewriteTrace(unittest.TestCase):
    """[R2] 二次简报重写调用也必须留下可审计的 trace。"""

    def _build_client(self):
        client = LlmClient.__new__(LlmClient)
        client.metadata_schema = METADATA_SCHEMA
        return client

    def test_rewrite_sets_trace_on_success(self):
        client = self._build_client()
        client._generate = lambda prompt: '{"题名": "关于开展春风行动的简报"}'
        result = client.rewrite_briefing_title(
            ocr_text="正文……",
            current_title="春风行动简报",
            responsible_party="某单位",
            prompt="{current_title}|{responsible_party}|{ocr_text}",
        )
        self.assertEqual(result, "关于开展春风行动的简报")
        self.assertIsNotNone(client.last_rewrite_trace)
        self.assertEqual(client.last_rewrite_trace.parse_strategy, PARSE_STRATEGY_JSON)
        self.assertEqual(
            client.last_rewrite_trace.parsed_metadata["题名"],
            "关于开展春风行动的简报",
        )

    def test_rewrite_trace_is_none_when_no_current_title(self):
        # 没有原题名 → 不发起调用 → 无 trace
        client = self._build_client()
        client._generate = lambda prompt: '{"题名": "x"}'
        result = client.rewrite_briefing_title(
            ocr_text="x", current_title="", responsible_party="y", prompt="p",
        )
        self.assertEqual(result, "")
        self.assertIsNone(client.last_rewrite_trace)

    def test_rewrite_sets_failed_trace_on_exception(self):
        client = self._build_client()

        def _boom(prompt):
            raise RuntimeError("vllm down")

        client._generate = _boom
        result = client.rewrite_briefing_title(
            ocr_text="x", current_title="春风行动简报", responsible_party="y", prompt="p",
        )
        self.assertEqual(result, "")
        self.assertIsNotNone(client.last_rewrite_trace)
        self.assertEqual(client.last_rewrite_trace.parse_strategy, PARSE_STRATEGY_FAILED)


if __name__ == "__main__":
    unittest.main()
