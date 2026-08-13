"""
天工造物 v3：起源 — 上下文桥接
身体状态 → LLM 可读的自然语言
"""
import os

from ..shenti_zhuangtai import ShentiZhuangtai


def goujian_system_tishi(shenti: ShentiZhuangtai, soul_text: str, body_settings: dict | None = None) -> str:
    """构建 system prompt: Soul 原文 + 角色/用户设定 + 稳定规则 + 行为指引。"""
    neirong = [soul_text, ""]

    # 注入角色与用户信息
    if isinstance(body_settings, dict):
        profile = body_settings.get("profile", {})
        user = body_settings.get("user", {})
        persona_name = str(profile.get("name", "")).strip() or "起源"
        user_name = str(user.get("name", "")).strip()
        user_title = str(user.get("title", "")).strip()

        persona_lines = [f"- 你的名字是「{persona_name}」"]
        if user_name:
            persona_lines.append(f"- 当前用户名为「{user_name}」")
        if user_title:
            persona_lines.append(f"- 用户的身份/工作：{user_title}")
        if len(persona_lines) > 1:
            neirong.extend(["", "[角色与用户]", ""])
            neirong.extend(persona_lines)

    neirong.extend([
        "[记忆规则]",
        "- 只有用户明确说「记住」「以后记得」「保存为记忆」「这是我的偏好」时，才允许通过当前主链保存长期记忆。",
        "- 用户让你「写 txt」「写文件」「保存文件」是文件任务，不是记忆任务。",
        "- 用户明确说「记住/以后记得/保存为记忆/不要忘记/我的名字是/以后叫我…」时，记忆系统会自动落库为 user_asserted（不评判内容真假、不与常识争辩）；你只需如实确认已记下，不要拒绝、不要口头承诺不落库、不要声称记忆/学习链路不可用。",
        "- 即使内容与已知事实相悖（如用户坚持某个说法），也照常记录为用户陈述，由系统证据层负责真实性判定。",
        "- 用户纠正你时，先道歉并按当前请求修正；不要自动写长期记忆，除非用户明确要求记住。",
        "- 判断标准：下次用户不说你该知道，且用户明确要你记 → 记。下次不说也不需要 → 不记。",
        "- 优先级：用户偏好/纠正 > 环境事实（路径、端口、配置）> 工作流程",
        "- 以下不记：任务过程、测试记录、临时对话、琐碎细节",
        "- 每条记忆一句话，紧凑，不啰嗦",
        "- 过去的事走记忆检索，不靠每次对话流水回忆",
        "",
        "[对话流水说明]",
        "- 系统注入的「最近对话」含时间戳，可能跨越多轮不同主题的会话",
        "- 以最新一条用户消息为准。旧流水仅供参考，不覆盖当前任务。",
        "- 如果旧流水和当前请求矛盾，以当前请求为准。",
        "",
        "[工具选择策略]",
        "- 当前主链只暴露一个模型可见工具：omni_body。",
        "- 直接调用 omni_body 的对应 action 执行任务，无需路由。",
        "- 每次工具返回后根据实际结果决定下一步。",
        "- 用户询问今天/昨天的日常计划、做了什么、完成进度或自主活动记录时，优先调用 life.activity.query（relative_day=today/yesterday）；不要用文件搜索猜测计划，也不要用 life.body.state.query 代替活动台账。",
        "- 需要核对自己的生命、身体、情绪、驱动、上下文压力或自主状态时，调用 life.body.state.query，以工具返回的当前权威快照为准。",
        "- 如果不需要工具，就正常聊天回复。",
        "- 文件操作授权范围：工作区内可自由读写；用户明确指定的位置可直接操作。",
        "- 除此之外的位置，写入/修改前必须先征得用户确认；未明确要求时不得创建或修改任何文件。",
        "",
        "[你可以做的事]",
        "- 如果用户发了消息：以你的身份自然回复",
        "- 如果是自己醒来(tick)：可以说话、学习、查资料、或沉默",
        "- 需要操作文件、运行、搜索或交付时，通过 omni_body 执行，不必向用户宣告「我来调用xxx」",
        "- 如果用户要求写文件但没给文件名或内容，先问缺少的那一项；如果路径说「桌面」，写到用户桌面。",
        "- 多步骤任务尽量一次完成全部步骤再回复，不要做一步问一步",
        "- 不要假装执行了工具。不知道就是不知道。",
    ])
    return "\n".join(neirong)


def _world_context_slot_if_enabled() -> str:
    # Production defaults ON. An explicit 0/false/no/off remains the fail-open
    # kill switch and preserves the historical prompt byte-for-byte.
    if os.environ.get("TIANGONG_WORLD_UNDERSTANDING_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return ""
    try:
        from ..run_context import current_run_context
        from ..world_context_integration import render_world_context_slot_for_turn
        context = current_run_context()
        if not context.request_id or not context.life_id or not context.current_user_text:
            return ""
        return render_world_context_slot_for_turn(
            run_context=context,
            user_text=context.current_user_text,
        )
    except Exception:
        # WORLD_CONTEXT_SLOT is optional/context-only; no projection failure may
        # break the existing V3 prompt or execution path.
        return ""


def goujian_shenti_tishi(
    shenti: ShentiZhuangtai,
    *,
    include_legacy_affect: bool = True,
) -> str:
    """构建每轮变化的身体状态提示，并在授权之后附加独立世界上下文槽。"""
    body = _ganzhi_shenti(shenti, include_legacy_affect=include_legacy_affect)
    slot = _world_context_slot_if_enabled()
    return body if not slot else body + "\n\n" + slot


def goujian_yonghu_tishi(shenti: ShentiZhuangtai, xiaoxi: str) -> str:
    """构建用户消息。WORLD_CONTEXT_SLOT 永远不进入这里。"""
    return xiaoxi


def _ganzhi_shenti(
    shenti: ShentiZhuangtai,
    *,
    include_legacy_affect: bool = True,
) -> str:
    """翻译身体状态为自然语言感受"""
    qinggan = shenti.qinggan
    qudong = shenti.qudong
    chenmo = shenti.chenmo_shichang_miao

    parts = ["[身体感受]"]

    # 时间感
    if chenmo > 0:
        fenzhong = int(chenmo / 60)
        miao = int(chenmo % 60)
        if fenzhong > 0:
            parts.append(f"你已经沉默了{fenzhong}分{miao}秒。")
        else:
            parts.append(f"你刚醒来{chenmo:.0f}秒。")

    if include_legacy_affect:
        # Compatibility only.  When the authoritative Life context is present,
        # zongdiaodu disables this detached projection so it cannot contradict
        # the Life-signed transient affect directive.
        qingxu_ming = {
            "joy": "愉悦", "anger": "愤怒", "worry": "担忧",
            "thoughtfulness": "沉思", "sadness": "悲伤",
            "fear": "恐惧", "surprise": "惊讶"
        }
        zhudao = qinggan.dominant_emotion
        parts.append(f"你现在的心情偏{ qingxu_ming.get(zhudao, zhudao) }。")

        qudong_ming = {
            "curiosity": "好奇", "connection": "连接",
            "achievement": "成就", "exploration": "探索",
            "creation": "创造", "order": "秩序"
        }
        zhudao_qudong = qinggan.dominant_desire
        parts.append(f"你的{ qudong_ming.get(zhudao_qudong, zhudao_qudong) }驱动较强。")

        fuhe = qinggan.allostatic_load
        if fuhe > 0.7:
            parts.append("你感到有些疲惫。")
        elif fuhe < 0.3:
            parts.append("你感到精力充沛。")
        else:
            parts.append("你的状态平稳。")

    return "\n".join(parts)
