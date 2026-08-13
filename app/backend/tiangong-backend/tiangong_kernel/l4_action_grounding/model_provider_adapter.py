from __future__ import annotations
from dataclasses import dataclass
import json
from typing import Any

PROVIDER_IDS=("deepseek_v4","mimo","glm_5_2","minimax_m3","gpt_5_6")

@dataclass(frozen=True, slots=True)
class ProviderFactsheet:
    provider_id: str
    provider_display_name: str
    default_model_id: str
    protocol_family: str = "openai_compatible"
    streaming_supported: bool = True
    tool_calling_supported: bool = True
    context_window_tokens: int = 131072
    max_output_tokens: int = 16384
    thinking_mode_supported: bool = True
    structured_output_supported: bool = True

@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    provider_id: str
    chat: bool = True
    streaming: bool = True
    tools: bool = True
    structured_output: bool = True
    reasoning: bool = True

@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider_id: str
    protocol_family: str
    default_model_id: str

_FACTS={
 "deepseek_v4": ProviderFactsheet("deepseek_v4","DeepSeek V4","deepseek-v4-pro",context_window_tokens=131072,max_output_tokens=32768),
 "mimo": ProviderFactsheet("mimo","MiMo","mimo-v2.5-pro",context_window_tokens=131072,max_output_tokens=16384),
 "glm_5_2": ProviderFactsheet("glm_5_2","GLM 5.2","glm-5.2",context_window_tokens=131072,max_output_tokens=32768),
 "minimax_m3": ProviderFactsheet("minimax_m3","MiniMax M3","MiniMax-M3",context_window_tokens=1000000,max_output_tokens=32768),
 "gpt_5_6": ProviderFactsheet("gpt_5_6","GPT-5.6","gpt-5.6",context_window_tokens=400000,max_output_tokens=32768),
}

def _pid(provider_id: str) -> str:
    key=str(provider_id or "").strip().lower()
    aliases={"deepseek":"deepseek_v4","deepseek_v4_pro":"deepseek_v4","glm":"glm_5_2","minimax":"minimax_m3","gpt":"gpt_5_6","openai":"gpt_5_6"}
    key=aliases.get(key,key)
    if key not in _FACTS: raise ValueError(f"unknown provider: {provider_id}")
    return key

def all_provider_factsheets(): return dict(_FACTS)
def capability_profile_for(provider_id: str): return CapabilityProfile(_pid(provider_id))
def descriptor_for(provider_id: str):
    fs=_FACTS[_pid(provider_id)]
    return ProviderDescriptor(fs.provider_id,fs.protocol_family,fs.default_model_id)

def _content_text(content: Any) -> str:
    if isinstance(content,str): return content
    if isinstance(content,list):
        out=[]
        for x in content:
            if isinstance(x,dict): out.append(str(x.get("text") or x.get("content") or ""))
            elif x is not None: out.append(str(x))
        return "\n".join(v for v in out if v)
    return "" if content is None else str(content)

class ModelProviderRequestMapper:
    def __init__(self, provider_id: str): self.provider_id=_pid(provider_id)
    def yingse_canshu(self, shenti: Any) -> dict[str, Any]:
        # Advisory defaults only; caller remains the policy authority.
        if self.provider_id=="deepseek_v4": return {"temperature":0.6}
        if self.provider_id=="glm_5_2": return {"temperature":0.7}
        if self.provider_id=="mimo": return {"temperature":0.7}
        if self.provider_id=="gpt_5_6": return {}
        return {}

class ModelProviderResponseMapper:
    def __init__(self, provider_id: str): self.provider_id=_pid(provider_id)
    def guiyihua(self, raw: dict[str, Any]) -> dict[str, Any]:
        choices=raw.get("choices") if isinstance(raw,dict) else None
        choice=choices[0] if isinstance(choices,list) and choices else {}
        msg=choice.get("message") if isinstance(choice,dict) and isinstance(choice.get("message"),dict) else {}
        text=_content_text(msg.get("content"))
        if not text and isinstance(raw,dict): text=_content_text(raw.get("output_text") or raw.get("content"))
        return {"neirong":text,"jieshu_yuanyin":choice.get("finish_reason"),"usage":raw.get("usage") or {},"raw_id":raw.get("id")}

class ModelProviderStreamMapper:
    def __init__(self, provider_id: str): self.provider_id=_pid(provider_id)
    def chuli_kuai(self, chunk: dict[str, Any], accumulated: str) -> tuple[str,str|None]:
        choices=chunk.get("choices") if isinstance(chunk,dict) else None
        choice=choices[0] if isinstance(choices,list) and choices else {}
        delta=choice.get("delta") if isinstance(choice,dict) and isinstance(choice.get("delta"),dict) else {}
        text=_content_text(delta.get("content"))
        if not text and isinstance(chunk,dict): text=_content_text(chunk.get("delta") or chunk.get("text"))
        if not text: return accumulated,None
        return accumulated+text,text

class ModelProviderToolCallMapper:
    def __init__(self, provider_id: str): self.provider_id=_pid(provider_id)
    def tiqu(self, raw: dict[str, Any]) -> list[dict[str,Any]]:
        choices=raw.get("choices") if isinstance(raw,dict) else None
        choice=choices[0] if isinstance(choices,list) and choices else {}
        msg=choice.get("message") if isinstance(choice,dict) and isinstance(choice.get("message"),dict) else {}
        calls=msg.get("tool_calls") if isinstance(msg.get("tool_calls"),list) else []
        out=[]
        for item in calls:
            if not isinstance(item,dict): continue
            fn=item.get("function") if isinstance(item.get("function"),dict) else item
            name=str(fn.get("name") or "").strip()
            if not name: continue
            args=fn.get("arguments",{})
            if isinstance(args,str):
                try: args=json.loads(args)
                except Exception: pass
            out.append({"id":str(item.get("id") or ""),"name":name,"arguments":args})
        return out

class ModelProviderErrorMapper:
    def __init__(self, provider_id: str): self.provider_id=_pid(provider_id)
    def jiexi(self, status_code: int, raw: Any) -> dict[str,Any]:
        text=str(raw or "")[:1000]
        try:
            obj=json.loads(text); text=str(obj.get("error",{}).get("message") or obj.get("message") or text)
        except Exception: pass
        transient=int(status_code) in {408,409,425,429,500,502,503,504}
        return {"cuowu":f"HTTP {int(status_code)}: {text}","zhongshi":transient,"xuyao_zhongshi":transient,"status_code":int(status_code),"provider":self.provider_id}
