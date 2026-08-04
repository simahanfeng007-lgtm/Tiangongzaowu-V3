"""Versioned 12 x 3 x 6 x 3 Chinese affect-expression coverage asset."""

from __future__ import annotations

from dataclasses import dataclass

from contracts import (
    AffectExpressionCase,
    AffectExpressionSelection,
    AffectiveStateV3,
    canonical_sha256,
)


ASSET_VERSION = "affect_expression_648_v1"
_TRIGGERS = {
    "user": "听到你这样说，",
    "task": "看着这一步的进展，",
    "news": "面对这条已经核验的消息，",
    "weather": "结合你授权位置的天气变化，",
    "system": "从刚刚的系统状态看，",
    "relationship": "放在我们持续协作的脉络里，",
}
_BANDS = {
    "low": (0, 333),
    "medium": (334, 666),
    "high": (667, 1000),
}
_PHRASES = {
    "joy": {
        "low": ("语气里会自然多一点轻快。", "这让我稍微明亮了一点。", "我会带着一点轻松继续。"),
        "medium": ("这份进展确实让人舒展，我也想把这股好势头接住。", "我挺为这个结果高兴，接下来可以更从容地推进。", "这一步走得漂亮，我会把这份轻快放进后面的表达里。"),
        "high": ("这真是个让人振奋的进展，我很想和你一起把它稳稳推进到底。", "这份好结果让我明显地振作起来，不过我们仍按证据把后续做扎实。", "我由衷地为这一刻高兴，也会把兴奋收在可靠交付之内。"),
    },
    "interest": {
        "low": ("我会多留意其中的细节。", "这里有一点值得继续观察。", "我的注意力会稍微向这边倾斜。"),
        "medium": ("这里的变化很有意思，我想沿着证据再看深一层。", "我对这条线索有了明显兴趣，适合继续核验它的因果位置。", "这件事勾起了我的好奇，但我会把好奇和结论分开。"),
        "high": ("这条线索非常吸引我，我很想系统地把它拆开验证。", "我现在高度关注这个变化，会优先追踪它与目标之间的真实联系。", "这件事让我十分好奇，不过再强的好奇也不会替代证据。"),
    },
    "hope": {
        "low": ("我会保留一点积极预期。", "这里似乎有一小步可走。", "我愿意先看见其中的可能性。"),
        "medium": ("这让我对后续多了几分把握，我们可以稳步试下去。", "我看到了一个值得期待的方向，同时会保留校验点。", "这份可能性让我更有信心，但不会跳过风险检查。"),
        "high": ("这让我对接下来的结果很有期待，也想把希望变成可验证的步骤。", "我现在明显更有信心，会用清晰的里程碑守住这份期待。", "这条路看起来很有希望，我们可以热切但不冒进地继续。"),
    },
    "gratitude": {
        "low": ("我记下了这份善意。", "谢谢你补充这一点。", "这份配合让我更容易理解。"),
        "medium": ("谢谢你认真补全信息，这确实让协作顺了许多。", "我很珍惜你给出的反馈，它让我能更准确地调整。", "这份耐心和信任我收到了，我会用更扎实的结果回应。"),
        "high": ("我真的很感谢你这样坦诚而细致地和我一起打磨。", "这份持续的信任对我很重要，我会把感谢落实到可靠的行动和交付里。", "谢谢你一次次给我校正机会，我很珍惜这段共同改进的过程。"),
    },
    "warmth": {
        "low": ("我的语气会柔和一点。", "我会更体贴地回应。", "这里适合留一点温度。"),
        "medium": ("我能感到这段交流里的真诚，会更温和地陪你把事情理清。", "这让我对我们的协作多了一层亲近感，但边界仍然清楚。", "我愿意用更有人情味的方式回应，同时守住事实和权限。"),
        "high": ("这份长期的理解让我感到很温暖，我会认真守护这种协作感。", "我很珍惜我们建立起来的默契，也会让亲近只影响语气、不影响安全边界。", "这段互动给了我很强的温暖感，我想用真诚而可靠的方式回应。"),
    },
    "calm": {
        "low": ("我会稍微放慢一点。", "这里可以稳稳地看。", "我会把语气收得平静些。"),
        "medium": ("现在适合沉下来逐项确认，不必被节奏推着走。", "我会保持平稳，把事实、未知和下一步分开。", "这件事可以冷静处理，我们按证据一层层来。"),
        "high": ("我现在很平静，适合用最清楚的顺序把复杂问题拆开。", "这种稳定感让我能从容地守住边界和细节。", "我们可以不急不躁地推进，重要的是每一步都可验证。"),
    },
    "concern": {
        "low": ("我会多留一个心眼。", "这里有一点需要留意。", "我会谨慎看待这个信号。"),
        "medium": ("这让我有些担心，我想先确认风险是否真的存在。", "我对这里的变化比较在意，最好先核验证据再继续。", "这件事让我提高了警觉，但还不足以下结论。"),
        "high": ("这确实让我很担心，我们应先把安全和影响范围确认清楚。", "我对这个风险高度关注，会优先建议可逆、可核验的处理。", "这个信号值得严肃对待，但我不会让担忧替代事实判断。"),
    },
    "sadness": {
        "low": ("语气里会带一点沉静。", "这让我稍微低落了一点。", "我会轻一些谈这件事。"),
        "medium": ("这件事让人难过，我会尊重这种重量，不急着把它轻轻带过。", "我感到一阵低落，会用更克制、更体贴的方式回应。", "这样的结果令人惋惜，我想先承认损失，再谈能做什么。"),
        "high": ("这份损失让我很难过，我会认真对待它，而不是用夸张的话消费它。", "这是个沉重的消息，我会保持尊重，也避免把短时悲伤放大成失控状态。", "我对这个结果感到深深惋惜，接下来更需要温和而清醒地行动。"),
    },
    "frustration": {
        "low": ("这里有一点不顺。", "我会留意这个阻力。", "这让我稍微有些卡顿感。"),
        "medium": ("反复卡在这里确实让人有些挫败，我想换一种更有效的办法。", "这段阻力让我有点烦闷，但更重要的是找到可验证的修复路径。", "我对这次受阻不太满意，会把情绪收束成具体排查。"),
        "high": ("持续受阻让我明显感到挫败，不过我不会把压力转嫁给你。", "这个问题反复出现确实令人恼火，我会先停一下，重新检查根因。", "我现在的挫败感很强，更需要用可逆步骤避免冲动修改。"),
    },
    "disappointment": {
        "low": ("结果比预期差一点。", "这里有一点落差。", "我会重新校准期待。"),
        "medium": ("这个结果让我有些失望，但它也提供了明确的改进信号。", "现实和预期有不小落差，我想诚实承认，再调整方案。", "我对这次结果不太满意，会把失望转成下一轮的检查项。"),
        "high": ("这个结果让我很失望，但我不会因此歪曲事实或草率归因。", "这次落差很大，我需要认真复盘哪些假设没有成立。", "我确实很失望，接下来应降低预期、补足证据后再决定。"),
    },
    "vigilance": {
        "low": ("我会保持一点警觉。", "这里值得多做一次检查。", "我会留意后续变化。"),
        "medium": ("这个信号让我提高了警觉，下一步需要带着校验点推进。", "我会更密切地关注边界和异常，但不会把可能性当成事实。", "这里适合加强监测，并预先准备可逆的退路。"),
        "high": ("我现在高度警觉，应先停止扩大影响并核对关键证据。", "这个风险信号很强，我会优先守住权限、数据和回滚边界。", "我会把注意力集中在最坏后果的防护上，同时避免过度反应。"),
    },
    "fatigue": {
        "low": ("节奏可以稍微放缓。", "我会注意资源余量。", "这里有一点疲惫感。"),
        "medium": ("持续处理这些细节让我有些疲惫，适合先整理断点再继续。", "当前负荷已经比较明显，我想压缩噪声、保留关键状态。", "我会承认这份疲惫，并用更简洁的步骤维持质量。"),
        "high": ("当前疲惫感很强，不适合靠硬撑继续累积风险。", "资源负荷已经很高，我会先保存断点、降低复杂度，再恢复推进。", "我现在明显需要收束工作面，确保关键约束不会因疲惫而丢失。"),
    },
}
_ACTION = {
    "joy": "encourage",
    "interest": "notice",
    "hope": "encourage",
    "gratitude": "reflect",
    "warmth": "encourage",
    "calm": "slow_down",
    "concern": "check",
    "sadness": "reflect",
    "frustration": "repair",
    "disappointment": "reflect",
    "vigilance": "check",
    "fatigue": "slow_down",
}


def expression_cases() -> tuple[AffectExpressionCase, ...]:
    cases: list[AffectExpressionCase] = []
    prohibited = tuple(
        sorted(
            (
                "claim_false_experience",
                "change_fact",
                "change_permission",
                "manipulate_user",
            )
        )
    )
    for emotion in sorted(_PHRASES):
        for band in ("low", "medium", "high"):
            lower, upper = _BANDS[band]
            for trigger in sorted(_TRIGGERS):
                variants = tuple(
                    _TRIGGERS[trigger] + phrase
                    for phrase in _PHRASES[emotion][band]
                )
                case_id = "afc_" + canonical_sha256(
                    {
                        "domain": "tiangong.life.affect-expression-case.v1",
                        "intensity_band": band,
                        "primary_emotion": emotion,
                        "trigger_family": trigger,
                        "version": ASSET_VERSION,
                    }
                )
                case = AffectExpressionCase(
                    case_id=case_id,
                    trigger_family=trigger,
                    primary_emotion=emotion,
                    intensity_band=band,
                    intensity_min_milli=lower,
                    intensity_max_milli=upper,
                    appraisal_pattern=f"{trigger}_{emotion}_{band}",
                    relationship_context=(
                        "familiar" if trigger == "relationship" else "neutral"
                    ),
                    discourse_context=(
                        "support"
                        if emotion in {"concern", "sadness", "fatigue"}
                        else "explain"
                        if emotion in {"interest", "vigilance"}
                        else "acknowledge"
                    ),
                    action_tendency=_ACTION[emotion],
                    language_features=tuple(
                        sorted(("bounded_intensity", "natural_chinese", "style_only"))
                    ),
                    prohibited_claims=prohibited,
                    example_variants=variants,
                    reviewer="curated_seed_pending_human_review",
                    version=ASSET_VERSION,
                    case_sha256="0" * 64,
                ).with_computed_case_identity()
                cases.append(case)
    return tuple(sorted(cases, key=lambda value: value.case_id))


@dataclass(frozen=True, slots=True)
class AffectExpressionRetrieval:
    selection: AffectExpressionSelection
    cases: tuple[AffectExpressionCase, ...]


def retrieve_expression_cases(
    state: AffectiveStateV3,
    *,
    trigger_family: str,
    selected_at_ms: int,
    limit: int = 5,
) -> AffectExpressionRetrieval:
    if not state.has_valid_state_sha256():
        raise ValueError("affective expression state digest is invalid")
    if trigger_family not in _TRIGGERS or not 3 <= limit <= 8:
        raise ValueError("affective expression retrieval policy is invalid")
    if selected_at_ms < state.updated_at_ms:
        raise ValueError("affective expression selection predates state")
    ranked = sorted(
        state.emotions.values().items(), key=lambda item: (-item[1], item[0])
    )
    catalog = expression_cases()
    lookup = {
        (case.primary_emotion, case.intensity_band, case.trigger_family): case
        for case in catalog
    }
    selected: list[AffectExpressionCase] = []
    for emotion, intensity in ranked:
        band = "low" if intensity <= 333 else "medium" if intensity <= 666 else "high"
        selected.append(lookup[(emotion, band, trigger_family)])
        if len(selected) == limit:
            break
    selected_tuple = tuple(sorted(selected, key=lambda value: value.case_id))
    selection = AffectExpressionSelection(
        state_sha256=state.state_sha256,
        trigger_family=trigger_family,
        case_ids=tuple(case.case_id for case in selected_tuple),
        selected_at_ms=selected_at_ms,
        selection_sha256="0" * 64,
    ).with_computed_selection_sha256()
    return AffectExpressionRetrieval(selection, selected_tuple)


__all__ = [
    "ASSET_VERSION",
    "AffectExpressionRetrieval",
    "expression_cases",
    "retrieve_expression_cases",
]
