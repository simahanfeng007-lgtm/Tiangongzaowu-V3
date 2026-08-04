"""
天工造物 v3：起源 — 状态同步层
WebSocket 服务端：身体状态 → JSON → 广播 → 虚幻5/前端
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime
from typing import Any, Optional

try:
    import websockets
    from websockets.asyncio.server import ServerConnection
except Exception:
    websockets = None
    ServerConnection = Any

from .shenti_zhuangtai import ShentiZhuangtai
from .peizhi import ZHUANGTAI_TONGBU_DUANKOU


class ZhuangtaiTongbu:
    """WebSocket 状态同步服务"""

    def __init__(self, duankou: int = ZHUANGTAI_TONGBU_DUANKOU):
        self.duankou = duankou
        self._lianjie: set[ServerConnection] = set()
        self._fuwuqi = None
        self._xiancheng: threading.Thread | None = None
        self._xunhuan: asyncio.AbstractEventLoop | None = None
        self.yunxing_zhong = False

    def qidong(self):
        """在后台线程启动 WebSocket 服务"""
        # The desktop contract carries body state over the canonical 7174 API.
        # The old standalone WebSocket listener is opt-in for legacy integrations.
        if os.environ.get("TIANGONG_LEGACY_STATE_WS", "").strip().lower() not in {"1", "true", "yes"}:
            self.yunxing_zhong = False
            return
        if websockets is None:
            self.yunxing_zhong = False
            return
        if self.yunxing_zhong:
            return
        self.yunxing_zhong = True
        self._jiuxu = threading.Event()
        self._xiancheng = threading.Thread(target=self._yunxing_xunhuan, daemon=True)
        self._xiancheng.start()
        # 等待事件循环就绪
        self._jiuxu.wait(timeout=3.0)

    def tingzhi(self):
        """停止 WebSocket 服务"""
        self.yunxing_zhong = False

    def tuibo(self, shenti: ShentiZhuangtai):
        """打包身体状态并广播给所有连接"""
        if not self._lianjie:
            return
        shuju = _dabao_shenti(shenti)
        xiaoxi = json.dumps(shuju, ensure_ascii=False)
        
        # 线程安全：将广播任务提交到服务端事件循环
        if self._xunhuan and self._xunhuan.is_running():
            lianjie_kb = set(self._lianjie)  # 快照避免并发修改
            self._xunhuan.call_soon_threadsafe(
                lambda: asyncio.create_task(self._guangbo(xiaoxi, lianjie_kb))
            )

    async def _guangbo(self, xiaoxi: str, lianjie: set):
        """广播消息给所有连接，断开时移除"""
        silian = set()
        for lj in lianjie:
            try:
                await lj.send(xiaoxi)
            except Exception:
                silian.add(lj)
        self._lianjie -= silian

    async def _chuli_lianjie(self, lj: ServerConnection):
        """处理单个 WebSocket 连接"""
        self._lianjie.add(lj)
        try:
            async for _ in lj:
                pass  # 只推送，不接收
        except Exception:
            pass
        finally:
            self._lianjie.discard(lj)

    async def _fuwu(self):
        """WebSocket 服务主循环"""
        if websockets is None:
            return
        self._fuwuqi = await websockets.serve(
            self._chuli_lianjie,
            "127.0.0.1",
            self.duankou,
        )
        await self._fuwuqi.wait_closed()

    def _yunxing_xunhuan(self):
        """后台线程入口"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._xunhuan = loop
        self._jiuxu.set()  # 通知主线程：事件循环就绪
        try:
            loop.run_until_complete(self._fuwu())
        except Exception:
            pass
        finally:
            self._xunhuan = None
            loop.close()


def _dabao_shenti(shenti: ShentiZhuangtai) -> dict:
    """身体状态 → 虚幻5可用的 JSON"""
    q = shenti.qinggan
    qd = shenti.qudong
    sm = shenti.shengming

    # 最近行动摘要（给虚幻5做动作参考）
    dongzuo_cankao = []
    for x in shenti.zuijin_xingdong[-3:]:
        lt = x.get("leixing", "")
        jg = x.get("jieguo", {})
        zhaiyao = ""
        if isinstance(jg, dict):
            zhaiyao = jg.get("lizi", jg.get("beizhu", ""))[:80]
        elif isinstance(jg, str):
            zhaiyao = jg[:80]
        dongzuo_cankao.append({"leixing": lt, "zhaiyao": zhaiyao})

    # 驱动压力 Top 3
    yali = sorted(qd.qudong_yali.items(), key=lambda x: x[1], reverse=True)[:3]

    return {
        "leixing": "zhuangtai",
        "shijianchuo": datetime.now().isoformat(),
        "shenti_id": shenti.shenti_id[:12],

        # ── 情感（面部表情驱动）──
        "qinggan": {
            "joy": round(q.joy, 3),
            "anger": round(q.anger, 3),
            "worry": round(q.worry, 3),
            "thoughtfulness": round(q.thoughtfulness, 3),
            "sadness": round(q.sadness, 3),
            "fear": round(q.fear, 3),
            "surprise": round(q.surprise, 3),
            "dominant": q.dominant_emotion,
        },

        # ── 驱动（行为树分支）──
        "qudong": {
            "dominant": q.dominant_desire,
            "yali_top3": [{"name": k, "pressure": round(v, 3)} for k, v in yali],
            "allostatic_load": round(q.allostatic_load, 3),
        },

        # ── 健康 ──
        "jiankang": {
            "zhuangtai": shenti.jiankang_zhuangtai,
            "shengmingli": round(shenti.shengmingli, 3),
            "sunshang": round(shenti.sunshang_leiji, 3),
        },

        # ── 生命周期 ──
        "shengming": {
            "jieduan": sm.zhouqi_jieduan,
            "chengzhang": round(sm.chengzhang_jindu, 3),
            "zizhu_jibie": shenti.anquan.zizhu_jibie,
        },

        # ── 动作参考 ──
        "dongzuo": dongzuo_cankao,
    }


# 全局单例
TONGBU = ZhuangtaiTongbu()
