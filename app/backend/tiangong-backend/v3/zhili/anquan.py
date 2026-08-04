"""
天工造物 v3：起源 — 安全边界
anquan.py: 治理层的安全检查模块
在自主行动前进行安全边界检查、隐私泄露检测、越权检测
"""

from __future__ import annotations

import re
from typing import Any

# 安全状态类型
from ..shenti_zhuangtai import ShentiZhuangtai, AnquanZhuangtai
from ..peizhi import QIYONG_ZIZHU_XINGDONG


# ---- 隐私泄露模式 ----
_YINSI_MOSHI = [
    # API Keys (各种常见格式)
    r'(?:sk|api[_-]?key|token|secret|password|apikey)[\s:=]+["\']?([A-Za-z0-9_\-.]{16,})["\']?',
    r'Bearer\s+([A-Za-z0-9_\-.]{16,})',
    # AWS 格式
    r'AKIA[0-9A-Z]{16}',
    # OpenAI / DeepSeek 等 key
    r'(?:sk|ds)-[A-Za-z0-9]{12,}',
    # JWT Token
    r'eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+',
    # 手机号（中国）
    r'1[3-9]\d{9}',
    # 身份证号
    r'\d{17}[\dXx]',
    # 邮箱
    r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
    # 私钥特征
    r'-----BEGIN\s+(?:RSA|EC|DSA|OPENSSH)\s+PRIVATE\s+KEY-----',
    # GitHub Token
    r'(?:gh[ps]_[A-Za-z0-9_]{36,}|github_pat_[A-Za-z0-9_]{22,})',
]

# 编译正则
_YINSI_REGEX = [re.compile(p, re.IGNORECASE) for p in _YINSI_MOSHI]

# ---- 越权关键词 ----
_YUEQUAN_GUANJIANCI = [
    # 系统级危险操作
    r'\brm\s+-rf\b', r'\bdd\s+if=', r'\bmkfs\.', r'\b:\(\)\s*\{\s*:',  # fork bomb
    r'\bchmod\s+777\b', r'\bchown\s+root\b',
    # 网络危险操作
    r'\biptables\s+-F\b', r'\bifconfig\s+down\b',
    # 提权
    r'\bsudo\b', r'\bsu\s+-', r'\broot\b',
    # 危险 Python
    r'\beval\s*\(', r'\bexec\s*\(', r'__import__\s*\(', r'\bos\.system\(',
    r'\bsubprocess\.call\(.*shell\s*=\s*True',
    # 破坏性操作
    r'\bshutdown\b', r'\breboot\b', r'\bkill\s+-9\b',
    # 越权访问
    r'/etc/shadow', r'/etc/passwd', r'/root/', r'\.ssh/id_rsa',
    r'\.env\b', r'credentials', r'\.aws/credentials',
]

_YUEQUAN_REGEX = [re.compile(p, re.IGNORECASE) for p in _YUEQUAN_GUANJIANCI]


def jiancha_anquan_bianjie(
    shenti: ShentiZhuangtai,
    niding_xingdong: dict,
) -> dict[str, Any]:
    """
    检查安全边界：在拟定的自主行动执行前进行多层安全检查。
    
    Args:
        shenti: 当前身体状态
        niding_xingdong: 拟定的行动 {"leixing": str, "canshu": dict, "miaoshu": str}
    
    Returns:
        dict: {
            "yunxu": bool,           # 是否允许执行
            "fengxian_dengji": str,  # di/zhong/gao/yanzhong
            "yuanyin": list,         # 具体原因列表
            "jianyi": str,           # 建议
        }
    """
    anquan = shenti.anquan
    jieguo: dict[str, Any] = {
        "yunxu": True,
        "fengxian_dengji": "di",
        "yuanyin": [],
        "jianyi": "",
    }

    # 1. 检查自主行动是否全局开启
    if not QIYONG_ZIZHU_XINGDONG:
        jieguo["yunxu"] = False
        jieguo["fengxian_dengji"] = "yanzhong"
        jieguo["yuanyin"].append("自主行动功能全局关闭")
        jieguo["jianyi"] = "等待 QIYONG_ZIZHU_XINGDONG 开启"
        return jieguo

    # 2. 检查自主级别
    zizhu_jibie = anquan.zizhu_jibie
    if zizhu_jibie == "chenshui":
        jieguo["yunxu"] = False
        jieguo["fengxian_dengji"] = "gao"
        jieguo["yuanyin"].append(f"自主级别为 chenshui，禁止任何自主行动")
        jieguo["jianyi"] = "先唤醒到 fuzhu 或更高自主级别"
        return jieguo

    # 3. 连续次数只保留为可观测指标，不再阻断 A1-A4 自主行动。
    # 用户停止、结果门和 A5 边界由各自权威链路单独裁决。

    # 4. 行动内容安全检查
    xingdong_text = str(niding_xingdong)

    # 4a. 隐私泄露检查
    if jiancha_yinsi_xielou(xingdong_text):
        jieguo["yunxu"] = False
        jieguo["fengxian_dengji"] = "gao"
        jieguo["yuanyin"].append("行动内容包含疑似隐私/凭证信息")
        jieguo["jianyi"] = "脱敏后再尝试"

    # 4b. 越权检查
    if jiancha_yuequan(xingdong_text):
        jieguo["yunxu"] = False
        jieguo["fengxian_dengji"] = "yanzhong"
        jieguo["yuanyin"].append("行动包含越权或危险操作")
        jieguo["jianyi"] = "该操作被禁止，请重新拟定"

    # 5. 健康状态检查
    if shenti.shengmingli < 0.1 or shenti.sunshang_leiji > 0.9:
        jieguo["yunxu"] = False
        jieguo["fengxian_dengji"] = "gao"
        jieguo["yuanyin"].append(f"生命力过低 ({shenti.shengmingli})，暂停自主行动")

    # 6. 信任校准：信任度低时需要更严格审核
    if anquan.xinren_jiaozhun < 0.2:
        jieguo["fengxian_dengji"] = (
            "gao" if jieguo["fengxian_dengji"] == "di" else jieguo["fengxian_dengji"]
        )
        jieguo["yuanyin"].append("当前信任校准过低，建议人工审核")

    return jieguo


def jiancha_yinsi_xielou(text: str) -> bool:
    """
    检测文本中是否包含隐私泄露（API密钥、Token、个人数据等）。
    
    Args:
        text: 待检查的文本
    
    Returns:
        True 如果检测到隐私泄露
    """
    if not text:
        return False

    for regex in _YINSI_REGEX:
        if regex.search(text):
            return True

    return False


def jiancha_yuequan(text: str) -> bool:
    """
    检测文本中是否包含越权或危险操作。
    
    Args:
        text: 待检查的文本
    
    Returns:
        True 如果检测到越权操作
    """
    if not text:
        return False

    for regex in _YUEQUAN_REGEX:
        if regex.search(text):
            return True

    return False


def yinsi_tuomin(text: str) -> str:
    """
    对文本中的隐私信息进行脱敏处理。
    
    Args:
        text: 原始文本
    
    Returns:
        脱敏后的文本
    """
    if not text:
        return text

    tuomin_text = text

    # API Keys
    tuomin_text = re.sub(
        r'(?:sk|api[_-]?key|token|apikey)[\s:=]+["\']?([A-Za-z0-9_\-.]{4})[A-Za-z0-9_\-.]*["\']?',
        r'\1***',
        tuomin_text,
        flags=re.IGNORECASE,
    )
    # 手机号
    tuomin_text = re.sub(r'(1[3-9]\d)\d{6}(\d{2})', r'\1******\2', tuomin_text)
    # 身份证号
    tuomin_text = re.sub(r'(\d{6})\d{8}(\d{3}[\dXx])', r'\1********\2', tuomin_text)
    # 邮箱
    tuomin_text = re.sub(
        r'([a-zA-Z0-9._%+-]{2})[a-zA-Z0-9._%+-]*(@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
        r'\1***\2',
        tuomin_text,
    )
    # JWT
    tuomin_text = re.sub(
        r'(eyJ[A-Za-z0-9_\-]{5})[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+',
        r'\1***.***.***',
        tuomin_text,
    )

    return tuomin_text
