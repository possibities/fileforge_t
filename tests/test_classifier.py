import sys
import types
import unittest
from unittest.mock import patch


# Heavy optional deps may not be installed in CI / dev shells.
# Stub them in sys.modules before importing classifier so its transitive
# imports (paddleocr via OcrClient, openai via LlmClient) don't fail.
if "paddleocr" not in sys.modules:
    paddleocr_stub = types.ModuleType("paddleocr")
    paddleocr_stub.PaddleOCR = object
    sys.modules["paddleocr"] = paddleocr_stub

if "openai" not in sys.modules:
    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = object
    sys.modules["openai"] = openai_stub

from core.classifier import ArchiveClassifier


class _FakeOcrClient:
    def __init__(self, text=""):
        self.text = text
        self.last_paths = None

    def extract_text_from_images(self, image_paths):
        self.last_paths = list(image_paths)
        return self.text


class _FakeLlmClient:
    def __init__(self, metadata=None, rewrite_result="", rewrite_exc=None):
        self.metadata = metadata or {}
        self.rewrite_result = rewrite_result
        self.rewrite_exc = rewrite_exc
        self.extract_calls = []
        self.rewrite_calls = []

    def extract_metadata(self, ocr_text, prompt):
        self.extract_calls.append((ocr_text, prompt))
        return dict(self.metadata)

    def rewrite_briefing_title(
        self, ocr_text, current_title, responsible_party, prompt
    ):
        self.rewrite_calls.append(
            {
                "ocr_text": ocr_text,
                "current_title": current_title,
                "responsible_party": responsible_party,
                "prompt": prompt,
            }
        )
        if self.rewrite_exc is not None:
            raise self.rewrite_exc
        return self.rewrite_result


class _FakeRulesEngine:
    def __init__(self, transform=None):
        self.transform = transform or (lambda meta, _text: meta)
        self.calls = []

    def apply_all(self, metadata, ocr_text):
        self.calls.append((dict(metadata), ocr_text))
        return self.transform(metadata, ocr_text)


def _make_classifier(ocr=None, llm=None, rules=None):
    classifier = ArchiveClassifier.__new__(ArchiveClassifier)
    classifier.ocr_client = ocr or _FakeOcrClient(text="ocr text")
    classifier.llm_client = llm or _FakeLlmClient()
    classifier.rules_engine = rules or _FakeRulesEngine()
    classifier.metadata_schema = {}
    classifier.extraction_prompt = "PROMPT {ocr_text}"
    classifier.briefing_rewrite_prompt = "REWRITE PROMPT"
    return classifier


class TestProcessMultiPageDocument(unittest.TestCase):
    def test_empty_ocr_short_circuits_to_empty_dict(self):
        classifier = _make_classifier(ocr=_FakeOcrClient(text=""))

        result = classifier.process_multi_page_document(
            "ARCHIVE_X", ["/tmp/page1.jpg"]
        )

        self.assertEqual(result, {})

    def test_empty_llm_metadata_returns_empty_dict_without_attaching_fields(self):
        # When LLM extraction yields {}, the inner method returns {} and the
        # outer method must NOT attach 数字化时间/档案文件夹 — that would
        # produce a half-populated record that downstream stages can't
        # distinguish from a real result.
        ocr = _FakeOcrClient(text="non-empty ocr text")
        llm = _FakeLlmClient(metadata={})
        classifier = _make_classifier(ocr=ocr, llm=llm)

        with patch("core.classifier.get_file_creation_time", return_value="2026年4月"):
            result = classifier.process_multi_page_document(
                "ARCH_42", ["/tmp/p.jpg"]
            )

        self.assertEqual(result, {})

    def test_normal_flow_attaches_archive_name_and_digitization_time(self):
        ocr = _FakeOcrClient(text="some ocr text")
        llm = _FakeLlmClient(metadata={"题名": "测试题名", "保管期限": "10年"})
        rules = _FakeRulesEngine()  # passthrough
        classifier = _make_classifier(ocr=ocr, llm=llm, rules=rules)

        with patch("core.classifier.get_file_creation_time", return_value="2026年4月"):
            result = classifier.process_multi_page_document(
                "ARCH_42", ["/tmp/p1.jpg", "/tmp/p2.jpg"]
            )

        self.assertEqual(result["题名"], "测试题名")
        self.assertEqual(result["保管期限"], "10年")
        self.assertEqual(result["数字化时间"], "2026年4月")
        self.assertEqual(result["档案文件夹"], "ARCH_42")
        self.assertEqual(ocr.last_paths, ["/tmp/p1.jpg", "/tmp/p2.jpg"])
        # OCR text was forwarded to LLM
        self.assertEqual(llm.extract_calls[0][0], "some ocr text")

    def test_emits_progress_for_ocr_llm_rules_and_export(self):
        ocr = _FakeOcrClient(text="some ocr text")
        llm = _FakeLlmClient(metadata={"题名": "测试题名", "保管期限": "10年"})
        classifier = _make_classifier(ocr=ocr, llm=llm)
        events = []
        classifier.progress_callback = lambda **event: events.append(event)

        with patch("core.classifier.get_file_creation_time", return_value="2026年4月"):
            result = classifier.process_multi_page_document("ARCH_42", ["/tmp/p.jpg"])

        self.assertEqual(result["题名"], "测试题名")
        self.assertEqual(
            [(event["stage"], event["status"], event["progress"]) for event in events],
            [
                ("ocr", "ocr_running", 10),
                ("llm", "llm_running", 45),
                ("rules", "rules_running", 75),
                ("export", "exporting", 90),
            ],
        )


class TestBriefingRewriteBranch(unittest.TestCase):
    """Covers the rule-11 → second-pass LLM hand-off in _extract_metadata_from_text."""

    def _make_with_flag(
        self,
        rewrite_result="",
        rewrite_exc=None,
        initial_metadata=None,
    ):
        if initial_metadata is None:
            initial_metadata = {"题名": "春风行动简报", "责任者": "X 部门"}

        llm = _FakeLlmClient(
            metadata=initial_metadata,
            rewrite_result=rewrite_result,
            rewrite_exc=rewrite_exc,
        )

        def transform(meta, _text):
            new = dict(meta)
            new["_需重构简报题名"] = True
            return new

        rules = _FakeRulesEngine(transform=transform)
        classifier = _make_classifier(llm=llm, rules=rules)
        return classifier, llm

    def test_no_flag_skips_rewrite_call(self):
        llm = _FakeLlmClient(metadata={"题名": "正常题名"})
        classifier = _make_classifier(llm=llm)  # passthrough rules → no flag

        result = classifier._extract_metadata_from_text("ocr text")

        self.assertEqual(result["题名"], "正常题名")
        self.assertEqual(len(llm.rewrite_calls), 0)
        self.assertNotIn("_需重构简报题名", result)

    def test_rewrite_success_replaces_title_and_drops_flag(self):
        classifier, llm = self._make_with_flag(
            rewrite_result="关于开展春风行动的简报"
        )

        result = classifier._extract_metadata_from_text("ocr text")

        self.assertEqual(result["题名"], "关于开展春风行动的简报")
        self.assertNotIn("_需重构简报题名", result)
        # 备注 stays absent on success
        self.assertNotIn("备注", result)
        # rewrite_briefing_title was called once with expected fields
        self.assertEqual(len(llm.rewrite_calls), 1)
        call = llm.rewrite_calls[0]
        self.assertEqual(call["current_title"], "春风行动简报")
        self.assertEqual(call["responsible_party"], "X 部门")
        self.assertEqual(call["prompt"], "REWRITE PROMPT")

    def test_rewrite_returns_empty_falls_back_to_remark(self):
        classifier, _ = self._make_with_flag(rewrite_result="")

        result = classifier._extract_metadata_from_text("ocr text")

        self.assertEqual(result["题名"], "春风行动简报")  # unchanged
        self.assertIn("【待核查】", result["备注"])
        self.assertIn("春风行动简报", result["备注"])

    def test_rewrite_without_briefing_keyword_falls_back_to_remark(self):
        # Result must contain "简报" or it counts as a failure.
        classifier, _ = self._make_with_flag(
            rewrite_result="关于开展某活动的通知"
        )

        result = classifier._extract_metadata_from_text("ocr text")

        self.assertEqual(result["题名"], "春风行动简报")
        self.assertIn("【待核查】", result["备注"])

    def test_rewrite_returns_same_title_falls_back_to_remark(self):
        # Identical title means the model couldn't add information — treat as failure.
        classifier, _ = self._make_with_flag(
            rewrite_result="春风行动简报"
        )

        result = classifier._extract_metadata_from_text("ocr text")

        self.assertEqual(result["题名"], "春风行动简报")
        self.assertIn("【待核查】", result["备注"])

    def test_rewrite_returns_same_title_logs_accurate_reason(self):
        # When new == current the failure log must NOT say "不含'简报'二字"
        # (the title plainly contains 简报). It must say the title was
        # unchanged so future debuggers don't waste time chasing a
        # non-existent character mismatch.
        classifier, _ = self._make_with_flag(rewrite_result="春风行动简报")

        with self.assertLogs("core.classifier", level="WARNING") as log_ctx:
            classifier._extract_metadata_from_text("ocr text")

        joined = "\n".join(log_ctx.output)
        self.assertIn("与原题名相同", joined)
        self.assertNotIn("不含'简报'二字", joined)

    def test_rewrite_raises_is_caught_and_remark_added(self):
        # The rewrite branch must never propagate exceptions — pipeline stays alive.
        classifier, _ = self._make_with_flag(
            rewrite_exc=RuntimeError("network down")
        )

        result = classifier._extract_metadata_from_text("ocr text")

        self.assertEqual(result["题名"], "春风行动简报")
        self.assertIn("【待核查】", result["备注"])

    def test_existing_remark_is_preserved_when_warning_appended(self):
        initial = {
            "题名": "春风行动简报",
            "责任者": "X 部门",
            "备注": "原有备注",
        }
        classifier, _ = self._make_with_flag(
            rewrite_result="", initial_metadata=initial
        )

        result = classifier._extract_metadata_from_text("ocr text")

        self.assertTrue(result["备注"].startswith("原有备注"))
        self.assertIn("【待核查】", result["备注"])


if __name__ == "__main__":
    unittest.main()
