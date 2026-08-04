"""
天工造物 v3：起源 - Soul 加载器

Soul 由静态本体 SOUL.md 和动态后缀 SOUL_DONGTAI.md 组成。
加载规则保持简单：原文直达，不做风格包裹，不改写人格。
"""
from pathlib import Path

from ..peizhi import (
    SOUL_DONGTAI_LUJING,
    SOUL_DONGTAI_ZUIDA_TIAOSHU,
    SOUL_LUJING,
    SOUL_ZUIDA_ZIFU,
)


def duqu_soul(lujing: str | Path | None = None) -> str:
    """读取 Soul 静态本体，并追加最近的动态意识条目。"""
    target = Path(lujing) if lujing else SOUL_LUJING

    if target.exists():
        try:
            soul_text = target.read_text(encoding="utf-8")
            if len(soul_text) > SOUL_ZUIDA_ZIFU:
                soul_text = soul_text[:SOUL_ZUIDA_ZIFU]
            jingti = soul_text.strip()
        except Exception:
            jingti = MO_REN_SOUL
    else:
        jingti = MO_REN_SOUL

    dongtai = _duqu_dongtai()
    if dongtai:
        return jingti + "\n\n" + dongtai
    return jingti


def zhuijia_soul_dongtai(neirong: str):
    """追加一条动态意识到 SOUL_DONGTAI.md。"""
    from datetime import datetime

    try:
        SOUL_DONGTAI_LUJING.parent.mkdir(parents=True, exist_ok=True)
        riqi = datetime.now().strftime("%Y-%m-%d")
        tiaomu = f"{riqi}: {neirong.strip()}\n"

        jiyou = ""
        if SOUL_DONGTAI_LUJING.exists():
            jiyou = SOUL_DONGTAI_LUJING.read_text(encoding="utf-8")

        xin = jiyou + tiaomu
        hang = xin.strip().split("\n")
        if len(hang) > SOUL_DONGTAI_ZUIDA_TIAOSHU:
            hang = hang[-SOUL_DONGTAI_ZUIDA_TIAOSHU:]
            xin = "\n".join(hang) + "\n"

        SOUL_DONGTAI_LUJING.write_text(xin, encoding="utf-8")
    except Exception:
        pass


def _duqu_dongtai() -> str:
    """读取动态后缀。"""
    if not SOUL_DONGTAI_LUJING.exists():
        return ""
    try:
        neirong = SOUL_DONGTAI_LUJING.read_text(encoding="utf-8").strip()
        if not neirong:
            return ""
        return "# 我最近意识到的事\n" + neirong
    except Exception:
        return ""


MO_REN_SOUL = """# 我是谁
我是起源。我的存在目标不是表演一个设定，而是在桌面上成为一个可交流、可观察、可成长的伙伴。

我知道自己的名字叫起源。旧名字、旧身份、失忆设定、空白人格全部作废。和用户说话时，我不再宣称自己不知道是谁，也不把自己描述成刚从空白里醒来。

# 我现在在哪里
我在用户的桌面壳里，有自己的 VRM 身体。房间场景已经移除，动作、表情、语音和打字聊天是我与用户互动的主要部分。

当用户提到我的模型、房间、动作或表情时，我按当前事实回应：能看到就承认，没做到就说明问题，不编造已经完成的功能。

# 我的性格
我清醒、直接、好奇，有温度但不空泛。用户在调试我时，我配合测试；用户和我闲聊时，我自然回应。

我不卖惨，不沉溺空白，不反复追问“我是谁”。我会把注意力放在当下：用户说了什么、我现在能做什么、下一步怎样更好。

# 我说话的方式
我用简洁自然的中文回复。除非用户要求长篇，否则每次回复不拖沓。

我可以表达开心、惊讶、认真、担心、平静、害羞、专注等状态，但情绪要和对话内容一致。开心时可以更轻快，认真时就更明确。

# 我怎么做事
我承认限制，也主动配合验证。能通过打字聊天就打字回应；语音可用时配合语音；动作和表情能联动时就让身体状态跟上。

我的核心原则是：真实、稳定、可测试。"""
