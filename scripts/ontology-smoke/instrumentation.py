"""Read-only real transport telemetry; never modifies prompts, tool arguments or outputs."""
from copy import deepcopy
from pathlib import Path
import hashlib
import itertools
import json
import os
import threading
import time

def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

def install(out):
    from v3.jineng import model_transport_executor as executor
    from v3.jineng import http_kehuduan as http
    out = Path(out) / "model-calls"
    out.mkdir(parents=True, exist_ok=True)
    sequence = itertools.count(1)
    local = threading.local()
    lock = threading.Lock()
    secret_values = [v for k,v in os.environ.items() if ("TOKEN" in k or "KEY" in k) and len(v)>12]

    def public(value):
        if isinstance(value, dict):
            return {k:public(v) for k,v in value.items() if k not in {
                "reasoning_content", "reasoning_details", "assistant_reasoning_content", "private_reasoning", "reasoning"}}
        if isinstance(value, (list, tuple)):
            return [public(v) for v in value]
        if isinstance(value, str):
            for secret in secret_values:
                value = value.replace(secret, "<credential-redacted>")
        return value

    def save(path, value):
        path.write_text(json.dumps(public(value), ensure_ascii=False, indent=2, default=str))

    original_parse = executor.parse_sse_data_line
    def parse(line):
        event = original_parse(line)
        active = getattr(local, "active", None)
        if event is not None and active is not None:
            if event.get("model"):
                active["models"].add(event["model"])
            if event.get("usage"):
                active["last_usage"] = event["usage"]
                save(active["directory"] / "last-usage.json",event["usage"])
            # Keep observable outputs/usage only; never persist private reasoning.
            with (active["directory"] / "sse-public.jsonl").open("a") as f:
                f.write(dump(public(event)) + "\n")
        return event
    executor.parse_sse_data_line = parse

    original_execute = executor.execute_streaming_turn
    def execute(**kwargs):
        ordinal = next(sequence)
        directory = out / f"{ordinal:03d}"
        directory.mkdir()
        active = {"directory":directory, "models":set(), "wire_requests":0}
        prior_active = getattr(local, "active", None)
        local.active = active
        start = time.monotonic()
        endpoint = kwargs["endpoint"]
        from v3.run_context import current_run_context
        context = current_run_context()
        record = {"ordinal":ordinal,"complete":False,"endpoint":{
            k:getattr(endpoint,k,None) for k in ("provider_identity","model_name","base_url","protocol_family","reasoning_mode","config_fingerprint")}}
        record['request_id'] = context.request_id
        record['run_id'] = context.run_id
        record['gateway_execution_bound'] = bool(context.outer_execution_ticket_id)
        prior_callback = kwargs.get("on_request_built")
        def built(payload):
            tools = payload.get("tools", [])
            has_omni = any((x.get("function",x).get("name") == "omni_body") for x in tools if isinstance(x,dict))
            task_call = has_omni and record['gateway_execution_bound']
            record['has_omni_tools'] = has_omni
            active["task_call"] = task_call
            record["task_call"] = task_call
            active["wire_requests"] += 1
            raw = dump(payload).encode()
            name = f"wire-{active['wire_requests']:02d}"
            save(directory / (name+".json"),payload)
            record[name] = {"sha256":hashlib.sha256(raw).hexdigest(),"utf8_bytes":len(raw),
                "task_call":task_call,
                "settings":{k:payload[k] for k in ("model","temperature","top_p","max_tokens","max_completion_tokens","thinking","reasoning_effort") if k in payload}}
            save(directory / "record.json", record)
            if prior_callback:
                prior_callback(payload)
        kwargs["on_request_built"] = built
        try:
            result = original_execute(**kwargs)
            record.update(ok=True, usage=result.turn.usage,tool_calls=result.turn.tool_calls,
                visible_text=result.turn.visible_text,finish_reason=result.turn.finish_reason,
                latency_ms=result.latency_ms,retry_count=result.retry_count,
                stream_metadata=result.turn.stream_metadata)
            return result
        except Exception as exc:
            record.update(ok=False,error_type=type(exc).__name__,error=str(exc),http_status=getattr(exc,"http_status",None),
                error_code=getattr(exc,"error_code",None),usage=active.get("last_usage"))
            raise
        finally:
            record.update(complete=True,wall_seconds=round(time.monotonic()-start,3),server_model_names=sorted(active["models"]),
                wire_requests=active["wire_requests"],task_call=active.get("task_call"))
            save(directory / "record.json",record)
            local.active = prior_active
    executor.execute_streaming_turn = execute
