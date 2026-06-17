import unittest

from core.rules_engine import RulesEngine


class TestRulesEngine(unittest.TestCase):
    def test_force_fix_fields_syncs_fonds_unit_and_drops_reserved(self):
        engine = RulesEngine()
        metadata = {
            "责任者": "黄石市脉源通档案管理有限公司",
        }

        result = engine._force_fix_fields(metadata)

        # 立档单位名称仍与责任者同步
        self.assertEqual(result.get("立档单位名称"), "黄石市脉源通档案管理有限公司")
        # 已下线的保留字段不再被注入(全宗号/档案馆代码/档案馆名称/外包单位名称)
        self.assertNotIn("全宗号", result)
        self.assertNotIn("档案馆代码", result)
        self.assertNotIn("档案馆名称", result)
        self.assertNotIn("外包单位名称", result)

    def test_rule11_marks_literary_briefing_title_for_review(self):
        engine = RulesEngine()
        metadata = {
            "题名": "春风行动简报",
            "备注": None,
        }

        with self.assertLogs("core.rules_engine", level="WARNING") as log_ctx:
            result = engine._clean_title(metadata)

        self.assertGreaterEqual(len(log_ctx.output), 1)
        self.assertTrue(result.get("_需重构简报题名"))
        # 规则 11 不再落备注，备注由 classifier 在 LLM 重写失败时兜底
        self.assertIsNone(result.get("备注"))

    def test_rule11_skips_when_title_has_substantive_verb(self):
        engine = RulesEngine()
        metadata = {
            "题名": "关于开展培训工作的简报",
            "备注": None,
        }

        result = engine._clean_title(metadata)

        self.assertNotIn("_需重构简报题名", result)
        self.assertIsNone(result.get("备注"))


    # ── 字段合法值校验（_force_fix_fields）──────────────────────────────────
    def test_illegal_security_level_nullifies_level_and_secret_period(self):
        engine = RulesEngine()
        result = engine._force_fix_fields({"密级": "高级机密", "保密期限": "10年"})
        self.assertIsNone(result["密级"])
        self.assertIsNone(result["保密期限"])  # 非法密级连带清空保密期限

    def test_illegal_secret_period_nullifies_only_period(self):
        engine = RulesEngine()
        result = engine._force_fix_fields({"密级": "秘密", "保密期限": "3年"})
        self.assertEqual(result["密级"], "秘密")  # 合法密级保留
        self.assertIsNone(result["保密期限"])  # 仅清空非法保密期限

    # ── 补充规则：简报锁定期限（规则2 最先执行并锁定，规则7 不得覆盖）──────────
    def test_rule2_briefing_locks_period_against_file_number_rule7(self):
        engine = RulesEngine()
        metadata = {
            "题名": "关于开展安全生产检查的简报",  # 含实质动词，避免触发规则11标志
            "文件编号": "脉源通发[2019]5号",
            "保管期限": "永久",  # LLM 误判为永久
            "实体分类名称": "综合类",
        }
        result = engine._apply_supplementary_rules(metadata, "全文内容")
        # 规则2 硬置 10年并锁定；规则7（带文号→≥30年）须尊重锁定，不上调
        self.assertEqual(result["保管期限"], "10年")

    # ── 补充规则：简报内容决定分类（党群/业务/综合）────────────────────────────
    def test_rule2_party_briefing_classified_as_dangqun(self):
        engine = RulesEngine()
        metadata = {"题名": "关于纪委工作的简报", "保管期限": "永久", "实体分类名称": "综合类"}
        result = engine._apply_supplementary_rules(metadata, "纪委 内容")
        self.assertEqual(result["实体分类名称"], "党群类")
        self.assertEqual(result["保管期限"], "10年")

    def test_rule2_archive_briefing_classified_as_yewu(self):
        engine = RulesEngine()
        metadata = {"题名": "关于档案整理的简报", "保管期限": "永久", "实体分类名称": "综合类"}
        result = engine._apply_supplementary_rules(metadata, "档案整理 内容")
        self.assertEqual(result["实体分类名称"], "业务类")

    # ── 开放状态：严格优先级短路 ────────────────────────────────────────────
    def test_open_status_security_marking_beats_privacy(self):
        engine = RulesEngine()
        # 既有密级标注又含隐私词，密级标注优先 → 工作秘密
        result = engine._apply_open_status_rules({"题名": "工资表", "密级": "秘密"}, "工资表 内容")
        self.assertEqual(result["开放状态"], "控制")
        self.assertEqual(result["延期开放理由"], "工作秘密")

    def test_open_status_privacy_when_no_security_marking(self):
        engine = RulesEngine()
        result = engine._apply_open_status_rules({"题名": "职工工资表", "密级": None}, "")
        self.assertEqual(result["开放状态"], "控制")
        self.assertEqual(result["延期开放理由"], "个人隐私")

    def test_open_status_commercial_exempt_for_bid_result(self):
        engine = RulesEngine()
        # 含商业词“报价单”，但题名含中标结果豁免词 → 仍开放
        result = engine._apply_open_status_rules(
            {"题名": "中标结果公示", "密级": None}, "报价单 中标结果"
        )
        self.assertEqual(result["开放状态"], "开放")
        self.assertIsNone(result["延期开放理由"])

    def test_open_status_negative_info_by_pattern(self):
        engine = RulesEngine()
        result = engine._apply_open_status_rules(
            {"题名": "关于给予处分的决定", "密级": None}, "给予警告处分"
        )
        self.assertEqual(result["开放状态"], "控制")
        self.assertEqual(result["延期开放理由"], "负面信息")

    def test_open_status_defaults_to_open(self):
        engine = RulesEngine()
        result = engine._apply_open_status_rules({"题名": "一般通知", "密级": None}, "普通内容")
        self.assertEqual(result["开放状态"], "开放")
        self.assertIsNone(result["延期开放理由"])

    # ── 实体分类号：2020 年切换 + 精确匹配 ──────────────────────────────────
    def test_classification_code_new_scheme_from_2020(self):
        engine = RulesEngine()
        result = engine._validate_classification_code(
            {"实体分类名称": "党群类", "文件形成时间": "20200115", "实体分类号": "X"}
        )
        self.assertEqual(result["实体分类号"], "DQL")

    def test_classification_code_old_scheme_before_2020(self):
        engine = RulesEngine()
        result = engine._validate_classification_code(
            {"实体分类名称": "党群类", "文件形成时间": "20191231", "实体分类号": "X"}
        )
        self.assertEqual(result["实体分类号"], "001")

    def test_classification_code_falls_back_to_archive_year(self):
        engine = RulesEngine()
        # 无文件形成时间 → 用归档年度判断
        result = engine._validate_classification_code(
            {"实体分类名称": "业务类", "归档年度": "2022", "实体分类号": "X"}
        )
        self.assertEqual(result["实体分类号"], "YWL")

    def test_classification_code_requires_exact_category_match(self):
        engine = RulesEngine()
        # “业务管理类”不得被当作“业务类”，无精确匹配则保持原值
        result = engine._validate_classification_code(
            {"实体分类名称": "业务管理类", "归档年度": "2021", "实体分类号": "X"}
        )
        self.assertEqual(result["实体分类号"], "X")


if __name__ == "__main__":
    unittest.main()
