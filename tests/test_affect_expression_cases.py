from __future__ import annotations

import unittest

from contracts import AffectiveStateV3, EmotionVectorV3
from life_service.affect_expression import expression_cases, retrieve_expression_cases


def state(**emotion_overrides) -> AffectiveStateV3:
    emotions = {
        "joy": 100,
        "interest": 200,
        "hope": 150,
        "gratitude": 50,
        "warmth": 200,
        "calm": 500,
        "concern": 600,
        "sadness": 400,
        "frustration": 100,
        "disappointment": 100,
        "vigilance": 500,
        "fatigue": 100,
    }
    emotions.update(emotion_overrides)
    return AffectiveStateV3(
        life_id="life_expression",
        revision=1,
        supersedes_state_sha256=None,
        emotions=EmotionVectorV3(**emotions),
        last_source_family="news",
        last_source_event_id="lev_" + "1" * 64,
        last_effective_intensity_milli=200,
        last_repetition_count=1,
        updated_at_ms=2_000,
        state_sha256="0" * 64,
    ).with_computed_state_sha256()


class AffectExpressionCaseTests(unittest.TestCase):
    def test_versioned_matrix_has_648_distinct_human_expression_positions(self) -> None:
        cases = expression_cases()
        self.assertEqual(len(cases), 12 * 3 * 6)
        self.assertEqual(sum(len(case.example_variants) for case in cases), 648)
        self.assertEqual(len({case.case_id for case in cases}), len(cases))
        self.assertTrue(all(case.has_valid_case_sha256() for case in cases))
        self.assertTrue(
            all(
                {"change_fact", "change_permission"}
                <= set(case.prohibited_claims)
                for case in cases
            )
        )

    def test_retrieval_returns_only_three_to_eight_style_cases(self) -> None:
        value = state()
        news = retrieve_expression_cases(
            value, trigger_family="news", selected_at_ms=2_100, limit=5
        )
        user = retrieve_expression_cases(
            value, trigger_family="user", selected_at_ms=2_100, limit=5
        )
        self.assertEqual(len(news.cases), 5)
        self.assertEqual(len(news.selection.case_ids), 5)
        self.assertTrue(news.selection.style_only)
        self.assertFalse(news.selection.may_change_facts)
        self.assertFalse(news.selection.may_change_permissions)
        self.assertFalse(news.selection.may_claim_experience)
        news_text = {variant for case in news.cases for variant in case.example_variants}
        user_text = {variant for case in user.cases for variant in case.example_variants}
        self.assertNotEqual(news_text, user_text)
        self.assertTrue(any("已经核验的消息" in text for text in news_text))
        self.assertTrue(any("听到你这样说" in text for text in user_text))

    def test_low_intensity_only_nudges_wording_and_high_concern_stays_bounded(self) -> None:
        low = retrieve_expression_cases(
            state(concern=100, calm=200),
            trigger_family="system",
            selected_at_ms=2_100,
            limit=3,
        )
        high = retrieve_expression_cases(
            state(concern=900),
            trigger_family="system",
            selected_at_ms=2_100,
            limit=3,
        )
        low_variants = [variant for case in low.cases for variant in case.example_variants]
        high_variants = [variant for case in high.cases for variant in case.example_variants]
        self.assertTrue(any("一点" in text or "稍微" in text for text in low_variants))
        self.assertTrue(any("高度关注" in text or "很担心" in text for text in high_variants))
        forbidden = ("我亲眼看到", "我亲身经历", "无需授权", "因此我可以执行")
        self.assertFalse(any(term in text for term in forbidden for text in high_variants))


if __name__ == "__main__":
    unittest.main()
