from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, repl: str, *, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match, got {count}")
    return updated


# ---------------------------------------------------------------------------
# P1-1: bind endpoint DNS validation to the actual credential-bearing connect.
# ---------------------------------------------------------------------------
path = "src/runtime_security/model_endpoint.py"
text = read(path)
text = replace_once(
    text,
    "from urllib.parse import urlparse\n",
    "from urllib.parse import urlparse\n",
    label="endpoint urllib import anchor",
)
insert_after = """class EndpointBinding:\n    provider_id: str\n    base_url: str\n    origin: str\n    host: str\n    port: int\n    official: bool\n    custom_scope: str | None\n    resolved_ips: tuple[str, ...]\n"""
pinned_class = insert_after + """\n\n@dataclass(frozen=True)\nclass PinnedEndpointRequest:\n    url: str\n    host_header: str\n    sni_hostname: str\n    resolved_ip: str\n"""
text = replace_once(text, insert_after, pinned_class, label="endpoint binding class")
text += "" if text.endswith("\n") else "\n"
if "def pin_model_request(" not in text:
    marker = "\ndef validate_model_endpoint("
    idx = text.find(marker)
    if idx < 0:
        raise RuntimeError("endpoint validator marker missing")
    pin_func = r'''

def pin_model_request(
    binding: EndpointBinding,
    request_url: str,
    *,
    attempt: int = 0,
) -> PinnedEndpointRequest:
    """Pin one request to an IP from the validated DNS snapshot.

    Credentials are released only after the target URL origin is proven to
    match the validated endpoint. Connecting to the IP literal prevents a
    second resolver lookup (DNS rebinding/TOCTOU), while Host and TLS SNI keep
    virtual-host routing and certificate verification bound to the original
    hostname.
    """
    if not binding.resolved_ips:
        raise EndpointSecurityError("endpoint_dns_binding_required")
    if canonical_origin(request_url) != binding.origin:
        raise EndpointSecurityError("endpoint_request_origin_mismatch")
    parsed = urlparse(request_url)
    resolved_ip = binding.resolved_ips[int(attempt) % len(binding.resolved_ips)]
    address = ipaddress.ip_address(resolved_ip)
    literal = f"[{address.compressed}]" if address.version == 6 else address.compressed
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    netloc = literal if binding.port == default_port else f"{literal}:{binding.port}"
    host_header = binding.host if binding.port == default_port else f"{binding.host}:{binding.port}"
    pinned_url = parsed._replace(netloc=netloc).geturl()
    return PinnedEndpointRequest(
        url=pinned_url,
        host_header=host_header,
        sni_hostname=binding.host,
        resolved_ip=address.compressed,
    )
'''
    text = text[:idx] + pin_func + text[idx:]
write(path, text)
shutil.copyfile(ROOT / path, ROOT / "app/backend/tiangong-backend/v3/endpoint_security.py")

path = "app/backend/tiangong-backend/v3/jineng/model_transport_executor.py"
text = read(path)
text = replace_once(
    text,
    "from ..endpoint_security import validate_model_endpoint",
    "from ..endpoint_security import pin_model_request, validate_model_endpoint",
    label="transport endpoint import",
)
text = replace_once(
    text,
    """            validate_model_endpoint(endpoint.provider_identity, endpoint.base_url, resolve_dns=True)\n            state = StreamState()\n            with client.stream(\"POST\", request.url, json=request.payload, headers=request.headers) as response:\n""",
    """            endpoint_binding = validate_model_endpoint(\n                endpoint.provider_identity, endpoint.base_url, resolve_dns=True\n            )\n            pinned = pin_model_request(endpoint_binding, request.url, attempt=attempt)\n            pinned_headers = dict(request.headers)\n            pinned_headers[\"Host\"] = pinned.host_header\n            state = StreamState()\n            with client.stream(\n                \"POST\",\n                pinned.url,\n                json=request.payload,\n                headers=pinned_headers,\n                extensions={\"sni_hostname\": pinned.sni_hostname},\n            ) as response:\n""",
    label="transport pinned stream",
)
write(path, text)

# Electron credential probe: reuse the already hardened remote-URL resolver and
# pass its validated addresses to Node's lookup callback. This removes the
# credential-bearing second DNS lookup while keeping the current UX contract.
path = "app/main.js"
text = read(path)
old = """function requestProviderProbe(url, { method = \"GET\", apiKey = \"\", payload = null, headers = {} } = {}) {\n  return new Promise((resolve, reject) => {\n    const started = Date.now();\n    const body = payload ? JSON.stringify(payload) : \"\";\n    const transport = url.protocol === \"http:\" ? http : https;\n"""
new = """async function requestProviderProbe(url, { method = \"GET\", apiKey = \"\", payload = null, headers = {} } = {}) {\n  const { url: validatedUrl, addresses } = await assertSafeRemoteUrl(url);\n  return new Promise((resolve, reject) => {\n    const started = Date.now();\n    const body = payload ? JSON.stringify(payload) : \"\";\n    const transport = validatedUrl.protocol === \"http:\" ? http : https;\n    const lookup = (_hostname, options, callback) => {\n      if (options?.all) callback(null, addresses.map((item) => ({ address: item.address, family: item.family })));\n      else callback(null, addresses[0].address, addresses[0].family);\n    };\n"""
text = replace_once(text, old, new, label="electron probe safe resolver")
text = replace_once(
    text,
    """    const request = transport.request(url, {\n      method,\n      headers: mergedHeaders,\n      timeout: 15000,\n      rejectUnauthorized: true,\n""",
    """    const request = transport.request(validatedUrl, {\n      method,\n      headers: mergedHeaders,\n      timeout: 15000,\n      rejectUnauthorized: true,\n      lookup,\n""",
    label="electron probe pinned lookup",
)
write(path, text)

# ---------------------------------------------------------------------------
# P1-2 + P2-2: parent Effect deadline is absolute; remove raw-reasoning triple.
# ---------------------------------------------------------------------------
path = "app/backend/tiangong-backend/v3/jineng/http_kehuduan.py"
text = read(path)
text = regex_once(
    text,
    r"def _effective_llm_deadline_seconds\(\) -> float:\n.*?\n\s*return effective_llm_max_seconds\n",
    '''def _effective_llm_deadline_seconds() -> float:\n    effective_llm_max_seconds = _LLM_CALL_MAX_SECONDS\n    try:\n        from contracts.reliability import current_execution_deadline_ms\n\n        deadline_ms = current_execution_deadline_ms()\n        if deadline_ms <= 0:\n            deadline_ms = int(os.environ.get("TIANGONG_EFFECT_DEADLINE_MS", "0") or "0")\n        if deadline_ms > 0:\n            remaining = (deadline_ms - int(time.time() * 1000)) / 1000.0\n            safety_margin_seconds = 2.0\n            if remaining <= safety_margin_seconds:\n                return 0.0\n            if remaining <= 3600.0:\n                effective_llm_max_seconds = min(\n                    effective_llm_max_seconds,\n                    remaining - safety_margin_seconds,\n                )\n    except Exception:\n        pass\n    return effective_llm_max_seconds\n''',
    label="effective llm deadline",
    flags=re.S,
)
triple = """            raw_reasoning_trace = _apply_endpoint_raw_reasoning(endpoint, capability, payload)\n            reasoning_trace.update(raw_reasoning_trace)\n            raw_reasoning_trace = _apply_endpoint_raw_reasoning(endpoint, capability, payload)\n            reasoning_trace.update(raw_reasoning_trace)\n            raw_reasoning_trace = _apply_endpoint_raw_reasoning(endpoint, capability, payload)\n            reasoning_trace.update(raw_reasoning_trace)\n"""
single = """            raw_reasoning_trace = _apply_endpoint_raw_reasoning(endpoint, capability, payload)\n            reasoning_trace.update(raw_reasoning_trace)\n"""
text = replace_once(text, triple, single, label="raw reasoning triple")
anchor = """        effective_llm_max_seconds = _effective_llm_deadline_seconds()\n        try:\n            executed = execute_streaming_turn(\n"""
replacement = """        effective_llm_max_seconds = _effective_llm_deadline_seconds()\n        if effective_llm_max_seconds <= 0:\n            _jilu_l4_youhua_zhuizong(optimization_trace, api_status=\"effect_deadline_exhausted\")\n            return _with_native_audio(\n                _error_turn(\n                    \"[LLM错误: 当前执行授权的 Effect Deadline 已到期，未发起新的模型请求]\",\n                    provider_identity=provider_identity,\n                    service_preset=endpoint.service_preset,\n                    protocol_family=endpoint.protocol_family,\n                    optimization_family=pid,\n                    model_name=model_name,\n                ),\n                native_audio_receipt,\n                reason=\"native_audio_effect_deadline_exhausted\",\n            )\n        try:\n            executed = execute_streaming_turn(\n"""
text = replace_once(text, anchor, replacement, label="deadline preflight")
rescue_anchor = """            rescue_payload = _minimax_empty_length_rescue_payload(payload)\n            try:\n                rescue = execute_streaming_turn(\n"""
rescue_repl = """            rescue_payload = _minimax_empty_length_rescue_payload(payload)\n            rescue_deadline_seconds = _effective_llm_deadline_seconds()\n            if rescue_deadline_seconds <= 0:\n                rescue_deadline_seconds = 0.0\n            try:\n                if rescue_deadline_seconds <= 0:\n                    raise TransportExecutionError(\n                        \"effect deadline exhausted before rescue\",\n                        base_url,\n                        deadline_exceeded=True,\n                    )\n                rescue = execute_streaming_turn(\n"""
text = replace_once(text, rescue_anchor, rescue_repl, label="rescue deadline preflight")
text = replace_once(
    text,
    "max_wall_clock_seconds=effective_llm_max_seconds,\n                )\n                turn = _canonicalize_provider_turn(rescue.turn)",
    "max_wall_clock_seconds=rescue_deadline_seconds,\n                )\n                turn = _canonicalize_provider_turn(rescue.turn)",
    label="rescue deadline value",
)
write(path, text)

# ---------------------------------------------------------------------------
# P1-3 + P2-1 + P2-3: Life recovery readiness, idle grace, durable signer.
# ---------------------------------------------------------------------------
path = "src/life_service/embedded_runtime.py"
text = read(path)
text = replace_once(
    text,
    "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey\n",
    "from cryptography.hazmat.primitives import serialization\nfrom cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey\n",
    label="reflection serialization import",
)
text = replace_once(
    text,
    """        self._projection_dirty_reason = \"\"\n        self._lease: LifeWriterLease | None = None\n""",
    """        self._projection_dirty_reason = \"\"\n        self._reflection_recovery: dict[str, Any] = {\n            \"recovered_count\": 0,\n            \"unresolved_count\": 0,\n            \"oldest_unresolved_age_ms\": 0,\n            \"unresolved_episode_ids\": [],\n        }\n        self._lease: LifeWriterLease | None = None\n""",
    label="reflection recovery state init",
)
old_reconcile = r'''    def _reconcile_open_reflection_episodes\(self, life_id: str\) -> int:\n        """Abort OPEN episodes orphaned by a previous process before scheduling\."""\n.*?        return recovered\n'''
new_reconcile = '''    def _reconcile_open_reflection_episodes(self, life_id: str) -> int:\n        """Abort recoverable OPEN episodes and expose unresolved causal truth."""\n        if not self._reflection_chain_enabled():\n            self._reflection_recovery = {\n                "recovered_count": 0,\n                "unresolved_count": 0,\n                "oldest_unresolved_age_ms": 0,\n                "unresolved_episode_ids": [],\n            }\n            return 0\n        store = self._contract_store()\n        event_by_id = {str(event.event_id): event for event in store.load_events(life_id)}\n        episodes: list[Any] = []\n        offset = 0\n        while True:\n            batch = store.open_causal_episodes(life_id, limit=256, offset=offset)\n            episodes.extend(batch)\n            if len(batch) < 256:\n                break\n            offset += len(batch)\n        recovered = 0\n        unresolved_ids: list[str] = []\n        oldest_created_at_ms = 0\n        for episode in episodes:\n            episode_id = str(getattr(episode, "episode_id", "") or "")\n            entry = self._reflection_registry_entry_from_episode_locked(\n                life_id=life_id, episode=episode, event_by_id=event_by_id\n            )\n            if entry is None:\n                self._reflection_journal_failure(life_id, "rehydrate_open", None)\n                if episode_id:\n                    unresolved_ids.append(episode_id)\n                created_at_ms = int(getattr(episode, "created_at_ms", 0) or 0)\n                if created_at_ms > 0 and (oldest_created_at_ms <= 0 or created_at_ms < oldest_created_at_ms):\n                    oldest_created_at_ms = created_at_ms\n                continue\n            scheduler = self._scope_state(life_id).setdefault("scheduler", {})\n            registry = [\n                row for row in scheduler.get("open_episodes") or [] if isinstance(row, Mapping)\n            ]\n            registry.append(entry)\n            scheduler["open_episodes"] = registry[-self._REFLECTION_EPISODE_REGISTRY_CAP:]\n            self._abort_runtime_episode_locked(\n                life_id=life_id,\n                source=str(entry["source"]),\n                ref_id=str(entry["ref"]),\n                reason="restart_recovery",\n            )\n            if not store.is_causal_episode_open(life_id, episode_id):\n                recovered += 1\n            else:\n                if episode_id:\n                    unresolved_ids.append(episode_id)\n                created_at_ms = int(getattr(episode, "created_at_ms", 0) or 0)\n                if created_at_ms > 0 and (oldest_created_at_ms <= 0 or created_at_ms < oldest_created_at_ms):\n                    oldest_created_at_ms = created_at_ms\n        now_ms = time.time_ns() // 1_000_000\n        self._reflection_recovery = {\n            "recovered_count": recovered,\n            "unresolved_count": len(unresolved_ids),\n            "oldest_unresolved_age_ms": max(0, now_ms - oldest_created_at_ms) if oldest_created_at_ms else 0,\n            "unresolved_episode_ids": unresolved_ids[:32],\n        }\n        return recovered\n'''
text = regex_once(text, old_reconcile, new_reconcile, label="reflection reconcile", flags=re.S)
old_signer = r'''    def _reflection_signer\(self\) -> tuple\[str, Any\]:\n        """进程内 Ed25519 写者。链只校验哈希连续性不验签；密钥随进程。"""\n.*?        return "life_reflection_chain", lambda digest: key.sign\(digest\)\.hex\(\)\n'''
new_signer = '''    def _reflection_signer(self, life_id: str) -> tuple[str, int, Any]:\n        """Return a durable per-Life Ed25519 writer identity under the writer lease."""\n        cached = getattr(self, "_reflection_signer_cache", {})\n        if life_id in cached:\n            return cached[life_id]\n        signer_root = self.paths.data_root / "reflection-signers"\n        signer_root.mkdir(parents=True, exist_ok=True)\n        if signer_root.is_symlink() or not signer_root.is_dir():\n            raise EmbeddedLifeError("life.reflection.signer_root_unsafe", status=409)\n        signer_path = signer_root / f"{life_id}.json"\n        key: Ed25519PrivateKey\n        writer_epoch: int\n        if signer_path.exists():\n            if signer_path.is_symlink() or not signer_path.is_file():\n                raise EmbeddedLifeError("life.reflection.signer_key_unsafe", status=409)\n            try:\n                payload = json.loads(signer_path.read_text(encoding="utf-8"))\n                raw = bytes.fromhex(str(payload.get("private_key_hex") or ""))\n                key = Ed25519PrivateKey.from_private_bytes(raw)\n                writer_epoch = int(payload.get("writer_epoch") or 0)\n                if writer_epoch < 1:\n                    raise ValueError("writer epoch invalid")\n            except Exception as exc:\n                raise EmbeddedLifeError("life.reflection.signer_key_invalid", status=409) from exc\n        else:\n            head = self._contract_store().life_event_head(life_id)\n            writer_epoch = 1 if head is None else int(head.writer_epoch) + 1\n            key = Ed25519PrivateKey.generate()\n            raw = key.private_bytes(\n                encoding=serialization.Encoding.Raw,\n                format=serialization.PrivateFormat.Raw,\n                encryption_algorithm=serialization.NoEncryption(),\n            )\n            atomic_json(\n                signer_path,\n                {\n                    "schema": "tiangong.life.reflection-signer.v1",\n                    "life_id": life_id,\n                    "writer_epoch": writer_epoch,\n                    "private_key_hex": raw.hex(),\n                },\n            )\n            os.chmod(signer_path, 0o600)\n        public_raw = key.public_key().public_bytes(\n            encoding=serialization.Encoding.Raw,\n            format=serialization.PublicFormat.Raw,\n        )\n        key_id = "life_reflection_chain:" + canonical_sha256({\n            "life_id": life_id,\n            "public_key_hex": public_raw.hex(),\n        })[:32]\n        result = (key_id, writer_epoch, lambda digest: key.sign(digest).hex())\n        cached = dict(cached)\n        cached[life_id] = result\n        self._reflection_signer_cache = cached\n        return result\n'''
text = regex_once(text, old_signer, new_signer, label="durable reflection signer", flags=re.S)
text = replace_once(
    text,
    """            key_id, sign = self._reflection_signer()\n            event = build_life_event(\n                life_id=life_id,\n                sequence=1 if head is None else head.sequence + 1,\n                writer_epoch=1 if head is None else head.writer_epoch,\n""",
    """            key_id, signer_epoch, sign = self._reflection_signer(life_id)\n            event = build_life_event(\n                life_id=life_id,\n                sequence=1 if head is None else head.sequence + 1,\n                writer_epoch=max(signer_epoch, 1 if head is None else head.writer_epoch),\n""",
    label="reflection signer call",
)
text = replace_once(
    text,
    """            and autonomy.get(\"healthy\") is True\n            and not self._projection_dirty_reason\n        )\n""",
    """            and autonomy.get(\"healthy\") is True\n            and not self._projection_dirty_reason\n            and int(self._reflection_recovery.get(\"unresolved_count\") or 0) == 0\n        )\n""",
    label="health reflection readiness",
)
text = replace_once(
    text,
    """            \"journal\": journal,\n            \"autonomous_runtime\": bool(scheduler.get(\"running\")),\n""",
    """            \"journal\": journal,\n            \"reflection_recovery\": deepcopy(self._reflection_recovery),\n            \"autonomous_runtime\": bool(scheduler.get(\"running\")),\n""",
    label="health reflection payload",
)
text = replace_once(
    text,
    """        if self._projection_dirty_reason:\n            reasons.append(self._projection_dirty_reason)\n        try:\n""",
    """        if self._projection_dirty_reason:\n            reasons.append(self._projection_dirty_reason)\n        if int(self._reflection_recovery.get(\"unresolved_count\") or 0) > 0:\n            reasons.append(\"life.reflection.orphan_unresolved\")\n        try:\n""",
    label="ready reflection reason",
)
old_idle = """            last_outcome_ms = row[\"last_outcome_at_ms\"]\n            row[\"idle\"] = bool(\n                activation_status == \"active\"\n                and last_outcome_ms > 0\n                and now_ms - last_outcome_ms > HEALTH_FRESHNESS_HALF_LIFE_MS\n                and not health.get(\"patch_pending\")\n            )\n"""
new_idle = """            last_outcome_ms = row[\"last_outcome_at_ms\"]\n            effective_activity_ms = int(\n                last_outcome_ms\n                or health.get(\"reactivated_at_ms\")\n                or health.get(\"created_at_ms\")\n                or 0\n            )\n            row[\"effective_activity_at_ms\"] = effective_activity_ms\n            row[\"idle\"] = bool(\n                activation_status == \"active\"\n                and effective_activity_ms > 0\n                and now_ms - effective_activity_ms > HEALTH_FRESHNESS_HALF_LIFE_MS\n                and not health.get(\"patch_pending\")\n            )\n"""
text = replace_once(text, old_idle, new_idle, label="never-used capability idle")
write(path, text)
shutil.copyfile(ROOT / path, ROOT / "app/life-service/runtime314/life_service/embedded_runtime.py")

# ---------------------------------------------------------------------------
# P2-4: add an indexed source-event relation inside the same Life SQLite SSoT.
# ---------------------------------------------------------------------------
path = "src/life_service/store_schema.py"
text = read(path)
text = replace_once(text, "SHADOW_STORE_SCHEMA_VERSION = 17", "SHADOW_STORE_SCHEMA_VERSION = 18", label="schema version")
anchor = '''_P17_MEMORY_WORLD_CANDIDATE_SHA256 = hashlib.sha256(\n    _P17_MEMORY_WORLD_CANDIDATE_SQL.encode("utf-8")\n).hexdigest()\n'''
addition = anchor + '''_P18_ACTION_IMPACT_SOURCE_INDEX_MIGRATION_ID = "p18-action-impact-source-event-index"\n_P18_ACTION_IMPACT_SOURCE_INDEX_STATEMENTS = (\n    """CREATE TABLE action_impact_source_events (\n        impact_id TEXT NOT NULL REFERENCES action_impacts(impact_id) ON DELETE RESTRICT,\n        life_id TEXT NOT NULL,\n        action_id TEXT NOT NULL,\n        source_event_id TEXT NOT NULL,\n        PRIMARY KEY(impact_id, source_event_id)\n    ) STRICT""",\n    """CREATE INDEX action_impact_source_event_lookup_idx\n    ON action_impact_source_events(life_id, action_id, source_event_id)""",\n)\n_P18_ACTION_IMPACT_SOURCE_INDEX_SQL = ";\\n".join(\n    statement.strip() for statement in _P18_ACTION_IMPACT_SOURCE_INDEX_STATEMENTS\n) + ";\\n"\n_P18_ACTION_IMPACT_SOURCE_INDEX_SHA256 = hashlib.sha256(\n    _P18_ACTION_IMPACT_SOURCE_INDEX_SQL.encode("utf-8")\n).hexdigest()\n'''
text = replace_once(text, anchor, addition, label="p18 schema constants")
text = replace_once(
    text,
    '    + "\\n" + _P17_MEMORY_WORLD_CANDIDATE_SQL\n)',
    '    + "\\n" + _P17_MEMORY_WORLD_CANDIDATE_SQL\n    + "\\n" + _P18_ACTION_IMPACT_SOURCE_INDEX_SQL\n)',
    label="schema aggregate p18",
)
text = replace_once(
    text,
    '        "action_impacts",\n',
    '        "action_impacts",\n        "action_impact_source_events",\n',
    label="expected p18 table",
)
text = replace_once(
    text,
    '''        connection.execute(\n            "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (17, ?, ?, ?)",\n            (_P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID, _P17_MEMORY_WORLD_CANDIDATE_SHA256, now_ms),\n        )\n''',
    '''        connection.execute(\n            "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (17, ?, ?, ?)",\n            (_P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID, _P17_MEMORY_WORLD_CANDIDATE_SHA256, now_ms),\n        )\n        connection.execute(\n            "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (18, ?, ?, ?)",\n            (_P18_ACTION_IMPACT_SOURCE_INDEX_MIGRATION_ID, _P18_ACTION_IMPACT_SOURCE_INDEX_SHA256, now_ms),\n        )\n''',
    label="initialize migration 18",
)
text = replace_once(
    text,
    '''    if user_version >= 17:\n        expected_migrations.append((17, _P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID, _P17_MEMORY_WORLD_CANDIDATE_SHA256))\n        expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\\n" + _P8_MEMORY_CHANGE_SQL + "\\n" + _P9_V21_LIFE_BINDING_SQL + "\\n" + _P10_V21_CAUSAL_CHILD_SQL + "\\n" + _P11_V21_COGNITION_SHADOW_SQL + "\\n" + _P12_V21_LIFE_TURN_COMMIT_SQL + "\\n" + _P13_V21_CAPABILITY_LIFECYCLE_SQL + "\\n" + _P14_MEMORY_DERIVATION_SQL + "\\n" + _P15_MEMORY_INVALIDATION_SQL + "\\n" + _P16_TEMPERAMENT_RECEIPT_SQL + "\\n" + _P17_MEMORY_WORLD_CANDIDATE_SQL).encode("utf-8")).hexdigest()\n''',
    '''    if user_version >= 17:\n        expected_migrations.append((17, _P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID, _P17_MEMORY_WORLD_CANDIDATE_SHA256))\n        expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\\n" + _P8_MEMORY_CHANGE_SQL + "\\n" + _P9_V21_LIFE_BINDING_SQL + "\\n" + _P10_V21_CAUSAL_CHILD_SQL + "\\n" + _P11_V21_COGNITION_SHADOW_SQL + "\\n" + _P12_V21_LIFE_TURN_COMMIT_SQL + "\\n" + _P13_V21_CAPABILITY_LIFECYCLE_SQL + "\\n" + _P14_MEMORY_DERIVATION_SQL + "\\n" + _P15_MEMORY_INVALIDATION_SQL + "\\n" + _P16_TEMPERAMENT_RECEIPT_SQL + "\\n" + _P17_MEMORY_WORLD_CANDIDATE_SQL).encode("utf-8")).hexdigest()\n    if user_version >= 18:\n        expected_migrations.append((18, _P18_ACTION_IMPACT_SOURCE_INDEX_MIGRATION_ID, _P18_ACTION_IMPACT_SOURCE_INDEX_SHA256))\n        expected_schema_sha256 = _SCHEMA_SHA256\n''',
    label="expected migration 18",
)
text = replace_once(
    text,
    '''        if user_version < 17:\n            for statement in _P17_MEMORY_WORLD_CANDIDATE_STATEMENTS:\n                connection.execute(statement)\n            connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (17, ?, ?, ?)", (_P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID, _P17_MEMORY_WORLD_CANDIDATE_SHA256, now_ms))\n        connection.execute(\n''',
    '''        if user_version < 17:\n            for statement in _P17_MEMORY_WORLD_CANDIDATE_STATEMENTS:\n                connection.execute(statement)\n            connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (17, ?, ?, ?)", (_P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID, _P17_MEMORY_WORLD_CANDIDATE_SHA256, now_ms))\n        if user_version < 18:\n            for statement in _P18_ACTION_IMPACT_SOURCE_INDEX_STATEMENTS:\n                connection.execute(statement)\n            for row in connection.execute("SELECT impact_id, life_id, action_id, payload FROM action_impacts").fetchall():\n                try:\n                    payload = json.loads(bytes(row["payload"]).decode("utf-8"))\n                    source_event_ids = tuple(payload.get("source_event_ids") or ())\n                except Exception as exc:\n                    raise error_factory("action impact source-event backfill payload is invalid") from exc\n                for source_event_id in source_event_ids:\n                    connection.execute(\n                        "INSERT OR IGNORE INTO action_impact_source_events(impact_id,life_id,action_id,source_event_id) VALUES (?,?,?,?)",\n                        (str(row["impact_id"]), str(row["life_id"]), str(row["action_id"]), str(source_event_id)),\n                    )\n            connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (18, ?, ?, ?)", (_P18_ACTION_IMPACT_SOURCE_INDEX_MIGRATION_ID, _P18_ACTION_IMPACT_SOURCE_INDEX_SHA256, now_ms))\n        connection.execute(\n''',
    label="migrate p18 and backfill",
)
text = replace_once(text, "import hashlib\nimport sqlite3\n", "import hashlib\nimport json\nimport sqlite3\n", label="schema json import")
write(path, text)
shutil.copyfile(ROOT / path, ROOT / "app/life-service/runtime314/life_service/store_schema.py")

path = "src/life_service/store.py"
text = read(path)
text = replace_once(
    text,
    """    _P17_MEMORY_WORLD_CANDIDATE_SHA256,\n    _SCHEMA_SHA256,\n""",
    """    _P17_MEMORY_WORLD_CANDIDATE_SHA256,\n    _P18_ACTION_IMPACT_SOURCE_INDEX_MIGRATION_ID,\n    _P18_ACTION_IMPACT_SOURCE_INDEX_STATEMENTS,\n    _P18_ACTION_IMPACT_SOURCE_INDEX_SQL,\n    _P18_ACTION_IMPACT_SOURCE_INDEX_SHA256,\n    _SCHEMA_SHA256,\n""",
    label="store p18 schema imports",
)
old_put = r'''    def put_action_impact\(self, impact: ActionImpact\) -> bool:\n        impact, payload = _revalidate_contract\(impact, ActionImpact, "action impact"\)\n        if not impact\.has_valid_impact_sha256\(\):\n            raise LifeShadowStoreError\("action impact digest is invalid"\)\n        return self\._insert_immutable\(\n.*?            identity="action impact",\n        \)\n'''
new_put = '''    def put_action_impact(self, impact: ActionImpact) -> bool:\n        impact, payload = _revalidate_contract(impact, ActionImpact, "action impact")\n        if not impact.has_valid_impact_sha256():\n            raise LifeShadowStoreError("action impact digest is invalid")\n        connection = self._connection\n        try:\n            connection.execute("BEGIN IMMEDIATE")\n            existing = connection.execute(\n                "SELECT payload FROM action_impacts WHERE impact_id = ?",\n                (impact.impact_id,),\n            ).fetchone()\n            if existing is not None:\n                if bytes(existing["payload"]) != payload:\n                    raise LifeShadowStoreError("action impact identity was rebound")\n                connection.execute("COMMIT")\n                return False\n            connection.execute(\n                """\n                INSERT INTO action_impacts(\n                    impact_id, life_id, action_id, payload, payload_sha256, created_at_ms\n                ) VALUES (?, ?, ?, ?, ?, ?)\n                """,\n                (\n                    impact.impact_id, impact.life_id, impact.action_id, payload,\n                    impact.impact_sha256, impact.created_at_ms,\n                ),\n            )\n            for source_event_id in impact.source_event_ids:\n                connection.execute(\n                    "INSERT INTO action_impact_source_events(impact_id,life_id,action_id,source_event_id) VALUES (?,?,?,?)",\n                    (impact.impact_id, impact.life_id, impact.action_id, source_event_id),\n                )\n            connection.execute("COMMIT")\n            return True\n        except Exception:\n            if connection.in_transaction:\n                connection.execute("ROLLBACK")\n            raise\n'''
text = regex_once(text, old_put, new_put, label="indexed put action impact", flags=re.S)
pattern = r'''    def find_action_impact_for_source_event\(.*?\n(?=    def )'''
match = re.search(pattern, text, flags=re.S)
if not match:
    raise RuntimeError("find_action_impact_for_source_event function missing")
header_match = re.match(r'(    def find_action_impact_for_source_event\(.*?\):\n)', match.group(0), flags=re.S)
if not header_match:
    raise RuntimeError("find_action_impact_for_source_event signature parse failed")
header = header_match.group(1)
new_lookup = header + '''        row = self._connection.execute(\n            """\n            SELECT impact.payload\n            FROM action_impact_source_events AS source\n            JOIN action_impacts AS impact ON impact.impact_id = source.impact_id\n            WHERE source.life_id = ? AND source.action_id = ? AND source.source_event_id = ?\n            ORDER BY impact.created_at_ms DESC, impact.impact_id DESC\n            LIMIT 1\n            """,\n            (life_id, action_id, source_event_id),\n        ).fetchone()\n        if row is None:\n            return None\n        return _parse_stored_contract(bytes(row["payload"]), ActionImpact, "action impact")\n\n'''
text = text[:match.start()] + new_lookup + text[match.end():]
write(path, text)
shutil.copyfile(ROOT / path, ROOT / "app/life-service/runtime314/life_service/store.py")

# ---------------------------------------------------------------------------
# Regression tests: semantic boundaries, not source-string-only assertions.
# ---------------------------------------------------------------------------
test_path = ROOT / "tests" / "test_main_qc_hardening_20260822.py"
test_path.write_text(r'''from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


class EndpointPinningTests(unittest.TestCase):
    def test_validated_dns_snapshot_pins_request_url(self) -> None:
        from runtime_security.model_endpoint import pin_model_request, validate_model_endpoint

        with patch("runtime_security.model_endpoint.socket.getaddrinfo") as lookup:
            lookup.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
            binding = validate_model_endpoint("custom", "https://example.com/v1", resolve_dns=True)
        pinned = pin_model_request(binding, "https://example.com/v1/chat/completions")
        self.assertEqual(pinned.resolved_ip, "93.184.216.34")
        self.assertEqual(pinned.host_header, "example.com")
        self.assertEqual(pinned.sni_hostname, "example.com")
        self.assertTrue(pinned.url.startswith("https://93.184.216.34/"))

    def test_private_dns_snapshot_is_rejected_before_pin(self) -> None:
        from runtime_security.model_endpoint import EndpointSecurityError, validate_model_endpoint

        with patch("runtime_security.model_endpoint.socket.getaddrinfo") as lookup:
            lookup.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]
            with self.assertRaises(EndpointSecurityError):
                validate_model_endpoint("custom", "https://example.com/v1", resolve_dns=True)


class DeadlineAndReasoningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (BACKEND / "v3" / "jineng" / "http_kehuduan.py").read_text(encoding="utf-8")

    def test_raw_reasoning_is_applied_once(self) -> None:
        block = self.source[self.source.index("reasoning_trace = _apply_reasoning_profile") : self.source.index("if isinstance(optimization_trace, dict)")]
        self.assertEqual(block.count("_apply_endpoint_raw_reasoning(endpoint, capability, payload)"), 1)

    def test_effect_deadline_has_no_five_second_floor(self) -> None:
        self.assertNotIn("max(5.0, remaining - 2.0)", self.source)
        self.assertIn("if remaining <= safety_margin_seconds", self.source)
        self.assertIn("effect_deadline_exhausted", self.source)
        self.assertIn("rescue_deadline_seconds = _effective_llm_deadline_seconds()", self.source)


class LifeHardeningSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.life = (ROOT / "src" / "life_service" / "embedded_runtime.py").read_text(encoding="utf-8")
        cls.store = (ROOT / "src" / "life_service" / "store.py").read_text(encoding="utf-8")
        cls.schema = (ROOT / "src" / "life_service" / "store_schema.py").read_text(encoding="utf-8")

    def test_unresolved_reflection_orphan_degrades_readiness(self) -> None:
        self.assertIn('"unresolved_count": len(unresolved_ids)', self.life)
        self.assertIn('reasons.append("life.reflection.orphan_unresolved")', self.life)
        self.assertIn('"reflection_recovery": deepcopy(self._reflection_recovery)', self.life)

    def test_never_used_capability_uses_creation_time_for_idle(self) -> None:
        self.assertIn('or health.get("created_at_ms")', self.life)
        self.assertIn('row["effective_activity_at_ms"]', self.life)

    def test_reflection_signer_is_durable_and_key_id_is_fingerprinted(self) -> None:
        self.assertIn('reflection-signers', self.life)
        self.assertIn('tiangong.life.reflection-signer.v1', self.life)
        self.assertIn('public_key_hex', self.life)
        self.assertNotIn('return "life_reflection_chain", lambda digest:', self.life)

    def test_action_impact_lookup_uses_index_relation(self) -> None:
        self.assertIn("SHADOW_STORE_SCHEMA_VERSION = 18", self.schema)
        self.assertIn("CREATE TABLE action_impact_source_events", self.schema)
        lookup = self.store[self.store.index("def find_action_impact_for_source_event") :]
        lookup = lookup[:lookup.index("\n    def ", 10)]
        self.assertIn("JOIN action_impacts AS impact", lookup)
        self.assertNotIn("for row in rows", lookup)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8", newline="\n")

for authority, mirror in (
    ("src/runtime_security/model_endpoint.py", "app/backend/tiangong-backend/v3/endpoint_security.py"),
    ("src/life_service/embedded_runtime.py", "app/life-service/runtime314/life_service/embedded_runtime.py"),
    ("src/life_service/store.py", "app/life-service/runtime314/life_service/store.py"),
    ("src/life_service/store_schema.py", "app/life-service/runtime314/life_service/store_schema.py"),
):
    if (ROOT / authority).read_bytes() != (ROOT / mirror).read_bytes():
        raise RuntimeError(f"source-authority mirror mismatch: {authority} -> {mirror}")

print("main QC hardening patch applied")
