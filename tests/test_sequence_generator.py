import unittest

from core.sequence_generator import SequenceGenerator


YEAR_KEY = "\u5f52\u6863\u5e74\u5ea6"
CLASS_KEY = "\u5b9e\u4f53\u5206\u7c7b\u53f7"
PERIOD_KEY = "\u4fdd\u7ba1\u671f\u9650"
SERIAL_KEY = "\u4ef6\u53f7"
DOC_ID_KEY = "\u6863\u53f7"


class TestSequenceGenerator(unittest.TestCase):
    def test_assign_generates_serial_and_doc_id(self):
        generator = SequenceGenerator()
        metadata = {
            YEAR_KEY: "2020",
            CLASS_KEY: "YWL",
            PERIOD_KEY: "30年",
        }

        result = generator.assign(metadata)

        self.assertEqual(result[SERIAL_KEY], "0001")
        self.assertEqual(result[DOC_ID_KEY], "2020-YWL-D30-0001")

    def test_pre_2007_accepts_new_and_old_period_vocabulary(self):
        # [R1] 2007 年前档案：分类方案只用 永久/30年/10年，必须能映射成 Y/D30/D10；
        # 旧词 长期/短期 仍兼容为 C/D。
        generator = SequenceGenerator()

        new_vocab = generator.assign({YEAR_KEY: "2005", CLASS_KEY: "002", PERIOD_KEY: "30年"})
        self.assertEqual(new_vocab[DOC_ID_KEY], "2005-002-D30-0001")

        old_vocab = generator.assign({YEAR_KEY: "2005", CLASS_KEY: "002", PERIOD_KEY: "长期"})
        self.assertEqual(old_vocab[DOC_ID_KEY], "2005-002-C-0001")

    def test_assign_returns_none_when_required_fields_missing(self):
        generator = SequenceGenerator()
        metadata = {
            YEAR_KEY: "2020",
            CLASS_KEY: "",
            PERIOD_KEY: "30年",
        }

        with self.assertLogs("core.sequence_generator", level="WARNING") as log_ctx:
            result = generator.assign(metadata)

        self.assertGreaterEqual(len(log_ctx.output), 1)
        self.assertIsNone(result[SERIAL_KEY])
        self.assertIsNone(result[DOC_ID_KEY])


if __name__ == "__main__":
    unittest.main()
