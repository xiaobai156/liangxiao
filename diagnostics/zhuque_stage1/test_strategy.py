import unittest

from strategy import SourceInput, analyze_document, evaluate_sources


ANCHOR = "177792a.com『禁用资料』"


def make_source(*rows: str, anchor: str = ANCHOR) -> str:
    body = "".join(f"<p>{row}</p>" for row in rows)
    return f"<section><h3>{anchor}</h3>{body}<p>禁两肖</p></section>"


class ZhuqueStage1StrategyTests(unittest.TestCase):
    def test_real_target_shape_selects_216_from_top_three(self) -> None:
        evidence = analyze_document(
            make_source(
                "216期→禁用二肖→【兔猪】开123",
                "215期→禁用二肖→【马鼠】开122",
                "214期→禁用二肖→【猪鼠】开121",
            ),
            label="脚本解码",
            url="https://script.example/decoded.js",
            target_period=216,
        )

        self.assertTrue(evidence.success)
        self.assertEqual([(item.period, item.zodiac) for item in evidence.top3], [(216, "兔猪"), (215, "马鼠"), (214, "猪鼠")])
        assert evidence.selected is not None
        self.assertEqual(evidence.selected.zodiac, "兔猪")

    def test_adjacent_period_is_independently_selectable(self) -> None:
        result = evaluate_sources(
            [
                SourceInput(
                    "script",
                    "https://script.example/a.js",
                    make_source("216期→禁用二肖→【兔猪】开123", "215期→禁用二肖→【马鼠】开122"),
                )
            ],
            target_period=215,
        )

        self.assertTrue(result.success)
        assert result.selected is not None
        self.assertEqual(result.selected.zodiac, "马鼠")

    def test_nonexistent_period_fails(self) -> None:
        evidence = analyze_document(
            make_source("216期→禁用二肖→【兔猪】开123"),
            label="script",
            url="https://script.example/a.js",
            target_period=217,
        )

        self.assertFalse(evidence.success)
        self.assertIn("方向窗口", evidence.reason)

    def test_target_after_top_three_fails(self) -> None:
        evidence = analyze_document(
            make_source(
                "215期→禁用二肖→【马鼠】开122",
                "214期→禁用二肖→【猪鼠】开121",
                "213期→禁用二肖→【马猪】开120",
                "216期→禁用二肖→【兔猪】开123",
            ),
            label="script",
            url="https://script.example/a.js",
            target_period=216,
        )

        self.assertFalse(evidence.success)
        self.assertEqual([(item.period, item.zodiac) for item in evidence.top3], [(215, "马鼠"), (214, "猪鼠"), (213, "马猪")])

    def test_wrong_field_does_not_count(self) -> None:
        evidence = analyze_document(
            make_source("216期→禁用三肖→【兔猪】开123"),
            label="script",
            url="https://script.example/a.js",
            target_period=216,
        )

        self.assertFalse(evidence.success)
        self.assertEqual(evidence.valid_candidates, ())

    def test_duplicate_zodiac_is_diagnostic_only_and_does_not_consume_window(self) -> None:
        evidence = analyze_document(
            make_source(
                "216期→禁用二肖→【牛牛】开123",
                "215期→禁用二肖→【马鼠】开122",
                "214期→禁用二肖→【猪鼠】开121",
                "213期→禁用二肖→【马猪】开120",
            ),
            label="script",
            url="https://script.example/a.js",
            target_period=216,
        )

        self.assertFalse(evidence.success)
        self.assertEqual([item.period for item in evidence.top3], [215, 214, 213])
        self.assertTrue(any("重复生肖" in item.reason for item in evidence.invalid_candidates))

    def test_out_of_range_periods_are_diagnostic_only_and_do_not_consume_window(self) -> None:
        evidence = analyze_document(
            make_source(
                "000期→禁用二肖→【牛牛】开123",
                "999期→禁用二肖→【马羊】开122",
                "216期→禁用二肖→【兔猪】开121",
                "215期→禁用二肖→【马鼠】开120",
                "214期→禁用二肖→【猪鼠】开119",
            ),
            label="script",
            url="https://script.example/a.js",
            target_period=216,
        )

        self.assertTrue(evidence.success)
        self.assertEqual([item.period for item in evidence.top3], [216, 215, 214])
        self.assertEqual({item.period for item in evidence.invalid_candidates}, {0, 999})
        self.assertTrue(all("1..365" in item.reason for item in evidence.invalid_candidates))

    def test_same_document_same_period_conflict_fails(self) -> None:
        evidence = analyze_document(
            make_source(
                "216期→禁用二肖→【兔猪】开123",
                "216期→禁用二肖→【马羊】开122",
                "215期→禁用二肖→【马鼠】开121",
            ),
            label="script",
            url="https://script.example/a.js",
            target_period=216,
        )

        self.assertFalse(evidence.success)
        self.assertIn("同文档同期冲突", evidence.reason)

    def test_same_document_conflict_after_top_three_still_fails(self) -> None:
        evidence = analyze_document(
            make_source(
                "216期→禁用二肖→【兔猪】开123",
                "215期→禁用二肖→【马鼠】开122",
                "214期→禁用二肖→【猪鼠】开121",
                "213期→禁用二肖→【马猪】开120",
                "216期→禁用二肖→【马羊】开119",
            ),
            label="script",
            url="https://script.example/a.js",
            target_period=216,
        )

        self.assertFalse(evidence.success)
        self.assertIn("目标区块内216期出现不同合法值", evidence.reason)

    def test_cross_document_anchor_borrowing_fails(self) -> None:
        result = evaluate_sources(
            [
                SourceInput("anchor-only", "https://example/anchor", f"<h3>{ANCHOR}</h3>"),
                SourceInput("body-only", "https://example/body", "<p>216期→禁用二肖→【兔猪】开123</p>"),
            ],
            target_period=216,
        )

        self.assertFalse(result.success)
        self.assertIn("同一文档", result.reason)

    def test_script_and_browser_value_divergence_fails(self) -> None:
        result = evaluate_sources(
            [
                SourceInput("脚本解码", "https://example/script", make_source("216期→禁用二肖→【兔猪】开123")),
                SourceInput("浏览器DOM", "https://example/browser", make_source("216期→禁用二肖→【马羊】开123")),
            ],
            target_period=216,
        )

        self.assertFalse(result.success)
        self.assertIn("跨文档同期冲突", result.reason)

    def test_same_document_adjacent_same_value_is_deduped_with_positions(self) -> None:
        evidence = analyze_document(
            make_source(
                "216期→禁用二肖→【兔猪】开123",
                "216期→禁用二肖→【兔猪】开123",
                "215期→禁用二肖→【马鼠】开122",
            ),
            label="script",
            url="https://script.example/a.js",
            target_period=216,
        )

        self.assertTrue(evidence.success)
        self.assertEqual([item.period for item in evidence.top3], [216, 215])
        assert evidence.selected is not None
        self.assertEqual(evidence.selected.zodiac, "兔猪")
        self.assertEqual(len(evidence.selected.source_positions), 2)

    def test_source_state_direction_window_divergence_fails(self) -> None:
        result = evaluate_sources(
            [
                SourceInput(
                    "脚本解码",
                    "https://example/script",
                    make_source(
                        "215期→禁用二肖→【马鼠】开122",
                        "214期→禁用二肖→【猪鼠】开121",
                        "213期→禁用二肖→【马猪】开120",
                    ),
                ),
                SourceInput(
                    "浏览器DOM",
                    "https://example/browser",
                    make_source(
                        "216期→禁用二肖→【兔猪】开123",
                        "215期→禁用二肖→【马鼠】开122",
                        "214期→禁用二肖→【猪鼠】开121",
                    ),
                ),
            ],
            target_period=216,
        )

        self.assertFalse(result.success)
        self.assertIn("来源状态/方向窗口分歧", result.reason)
        self.assertNotIn("跨文档同期冲突", result.reason)


if __name__ == "__main__":
    unittest.main()
