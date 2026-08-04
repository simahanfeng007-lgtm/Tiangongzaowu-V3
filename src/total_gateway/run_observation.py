"""G4 路由影子的 RunObservation 隔离审计存储（草案 §5.2）。

结构性纪律（本模块整体遵守，而非逐条约定）：

- 本模块只允许写自己的隔离 RunObservation/audit SQLite 文件；
  对 request/effect/task/life/memory/投影/缓存 pointer 的写入恒为 0——
  因此本模块不 import、也不持有 total_gateway.store（GatewayStateStore）、
  life-service、后端 request/task store 等任何业务存储的引用；
- business handler 调用恒为 0：本模块不含任何业务处理器入口；
- 输入、附件、context、memory revision、registry/policy/coverage、
  model/prompt/router code hash 一经写入即冻结（append-only）；
- inference config 以 ModelSnapshot 形式整体冻结（provider/model 快照、
  temperature/top_p/seed 策略、tool schema、timeout/retry/fallback）；
  任何字段变化都会派生出新的 cohort_id（变化即新 cohort）；
- 完整终态 timeout 是质量失败（终态完整、不计入不完整，但也不构成
  完整 pair）；遥测缺失/无法配对/hash 不一致/终态 envelope 不完整
  属于"不完整 observation"：永久留在审计全集并计数、不删除、
  不进入需要完整 pair 的统计量。
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from contracts import canonical_sha256
from contracts.models import (
    SCHEMA_BASE,
    SCHEMA_VERSION,
    ContractModel,
    OpaqueId,
    ReasonCode,
    Sha256,
)


# 默认隔离库路径（相对进程工作目录；测试与部署均可注入其它路径）。
DEFAULT_OBSERVATION_DB_PATH = Path(
    "runtime/state/execution_shadow/run-observations.sqlite3"
)

# 终态：completed=双侧输出齐全的完整 pair；timeout=终态完整但属质量失败；
# incomplete=不完整 observation（永久保留、不计入完整 pair 统计）。
TerminalState = Literal["completed", "timeout", "incomplete"]

# 冻结的 temperature/top_p 快照字符串（canonical JSON 禁止浮点，
# 因此以十进制字符串形式冻结原始配置文本）。
_FROZEN_DECIMAL = r"^-?\d{1,3}(\.\d{1,6})?$"

_COHORT_ID = re.compile(r"^coh_[0-9a-f]{64}$")
_PAIR_KEY = re.compile(r"^pair_[0-9a-f]{64}$")
_OBSERVATION_ID = re.compile(r"^runobs_[0-9a-f]{64}$")


class ObservationStoreError(RuntimeError):
    """隔离存储通用错误（IO/解码/校验失败）。"""


class ObservationConflictError(ObservationStoreError):
    """同一 observation_id 重复追加（append-only 下不允许覆盖）。"""


class AppendOnlyViolationError(ObservationStoreError):
    """对 append-only 审计全集发起 UPDATE/DELETE 的违规调用。"""


def derive_cohort_id(
    *,
    model_snapshot: "ModelSnapshot",
    registry_sha256: str,
    policy_sha256: str,
    coverage_sha256: str,
    router_code_sha256: str,
    prompt_sha256: str,
) -> str:
    """变化即新 cohort：冻结的 inference config 与各类 hash 任一变即变。"""
    return "coh_" + canonical_sha256(
        {
            "domain": "tiangong.shadow.run-observation-cohort.v1",
            "model_snapshot": model_snapshot.model_dump(mode="json"),
            "registry_sha256": registry_sha256,
            "policy_sha256": policy_sha256,
            "coverage_sha256": coverage_sha256,
            "router_code_sha256": router_code_sha256,
            "prompt_sha256": prompt_sha256,
        }
    )


def derive_pair_key(
    *,
    scenario_cluster_id: str,
    gold_id: str,
    gold_version: int,
    input_sha256: str,
) -> str:
    """同一 gold 场景在不同 cohort 下的配对键（跨 cohort 对齐用）。"""
    return "pair_" + canonical_sha256(
        {
            "domain": "tiangong.shadow.run-observation-pair.v1",
            "scenario_cluster_id": scenario_cluster_id,
            "gold_id": gold_id,
            "gold_version": gold_version,
            "input_sha256": input_sha256,
        }
    )


class ModelSnapshot(ContractModel):
    """冻结的 inference config 快照；任一字段变化即派生新 cohort。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:RunObservationModelSnapshot",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    provider: OpaqueId
    model: OpaqueId
    temperature: str = Field(pattern=_FROZEN_DECIMAL)
    top_p: str = Field(pattern=_FROZEN_DECIMAL)
    seed_strategy: OpaqueId
    tool_schema_sha256: Sha256
    timeout_ms: int = Field(ge=0)
    retry_limit: int = Field(ge=0)
    fallback: OpaqueId


class RunObservation(ContractModel):
    """一次影子路由运行的隔离审计记录（不可变、自校验）。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:RunObservation",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    observation_schema: Literal["tiangong.shadow.run-observation.v1"] = (
        "tiangong.shadow.run-observation.v1"
    )
    observation_id: str = Field(pattern=_OBSERVATION_ID.pattern)
    cohort_id: str = Field(pattern=_COHORT_ID.pattern)
    scenario_cluster_id: OpaqueId
    gold_id: OpaqueId
    gold_version: int = Field(ge=0)
    input_sha256: Sha256
    context_sha256: Sha256
    memory_revision: int = Field(ge=0)
    registry_sha256: Sha256
    policy_sha256: Sha256
    coverage_sha256: Sha256
    model_snapshot: ModelSnapshot
    router_code_sha256: Sha256
    prompt_sha256: Sha256
    candidate_output: str | None = Field(default=None, max_length=1_000_000)
    legacy_output: str | None = Field(default=None, max_length=1_000_000)
    latency_ms: int = Field(ge=0)
    terminal_state: TerminalState
    incomplete_reasons: tuple[ReasonCode, ...] = Field(default=(), max_length=16)
    pair_key: str = Field(pattern=_PAIR_KEY.pattern)
    created_at_ms: int = Field(ge=0)
    record_sha256: Sha256

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        # 终态纪律：completed 必须双侧输出齐全且无残缺原因；
        # timeout 是质量失败而非残缺（incomplete_reasons 必须为空）；
        # incomplete 必须携带至少一条机器可读的残缺原因。
        if self.terminal_state == "completed":
            if self.candidate_output is None or self.legacy_output is None:
                raise ValueError("completed observation requires both outputs")
            if self.incomplete_reasons:
                raise ValueError("completed observation cannot carry incomplete reasons")
        elif self.terminal_state == "timeout":
            if self.incomplete_reasons:
                raise ValueError("timeout is a quality failure, not an incomplete observation")
        else:
            if not self.incomplete_reasons:
                raise ValueError("incomplete observation requires incomplete reasons")
        if self.incomplete_reasons != tuple(sorted(set(self.incomplete_reasons))):
            raise ValueError("incomplete reasons must be sorted and unique")
        # 派生身份自校验：cohort 与 pair_key 必须与冻结字段一致。
        if self.cohort_id != derive_cohort_id(
            model_snapshot=self.model_snapshot,
            registry_sha256=self.registry_sha256,
            policy_sha256=self.policy_sha256,
            coverage_sha256=self.coverage_sha256,
            router_code_sha256=self.router_code_sha256,
            prompt_sha256=self.prompt_sha256,
        ):
            raise ValueError("run observation cohort identity is invalid")
        if self.pair_key != derive_pair_key(
            scenario_cluster_id=self.scenario_cluster_id,
            gold_id=self.gold_id,
            gold_version=self.gold_version,
            input_sha256=self.input_sha256,
        ):
            raise ValueError("run observation pair key is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"record_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.record_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"record_sha256": self.computed_sha256()})


def _derive_observation_id(
    *,
    cohort_id: str,
    pair_key: str,
    created_at_ms: int,
    terminal_state: str,
    latency_ms: int,
    candidate_output: str | None,
    legacy_output: str | None,
) -> str:
    return "runobs_" + canonical_sha256(
        {
            "domain": "tiangong.shadow.run-observation.v1",
            "cohort_id": cohort_id,
            "pair_key": pair_key,
            "created_at_ms": created_at_ms,
            "terminal_state": terminal_state,
            "latency_ms": latency_ms,
            "candidate_sha256": canonical_sha256(candidate_output or ""),
            "legacy_sha256": canonical_sha256(legacy_output or ""),
        }
    )


def build_run_observation(
    *,
    scenario_cluster_id: str,
    gold_id: str,
    gold_version: int,
    input_sha256: str,
    context_sha256: str,
    memory_revision: int,
    registry_sha256: str,
    policy_sha256: str,
    coverage_sha256: str,
    model_snapshot: ModelSnapshot,
    router_code_sha256: str,
    prompt_sha256: str,
    candidate_output: str | None,
    legacy_output: str | None,
    latency_ms: int,
    terminal_state: TerminalState,
    incomplete_reasons: tuple[str, ...] = (),
    created_at_ms: int,
) -> RunObservation:
    """构造并自校验一条 RunObservation（派生 cohort/pair/identity/record hash）。"""
    cohort_id = derive_cohort_id(
        model_snapshot=model_snapshot,
        registry_sha256=registry_sha256,
        policy_sha256=policy_sha256,
        coverage_sha256=coverage_sha256,
        router_code_sha256=router_code_sha256,
        prompt_sha256=prompt_sha256,
    )
    pair_key = derive_pair_key(
        scenario_cluster_id=scenario_cluster_id,
        gold_id=gold_id,
        gold_version=gold_version,
        input_sha256=input_sha256,
    )
    return RunObservation(
        observation_id=_derive_observation_id(
            cohort_id=cohort_id,
            pair_key=pair_key,
            created_at_ms=created_at_ms,
            terminal_state=terminal_state,
            latency_ms=latency_ms,
            candidate_output=candidate_output,
            legacy_output=legacy_output,
        ),
        cohort_id=cohort_id,
        scenario_cluster_id=scenario_cluster_id,
        gold_id=gold_id,
        gold_version=gold_version,
        input_sha256=input_sha256,
        context_sha256=context_sha256,
        memory_revision=memory_revision,
        registry_sha256=registry_sha256,
        policy_sha256=policy_sha256,
        coverage_sha256=coverage_sha256,
        model_snapshot=model_snapshot,
        router_code_sha256=router_code_sha256,
        prompt_sha256=prompt_sha256,
        candidate_output=candidate_output,
        legacy_output=legacy_output,
        latency_ms=latency_ms,
        terminal_state=terminal_state,
        incomplete_reasons=tuple(incomplete_reasons),
        pair_key=pair_key,
        created_at_ms=created_at_ms,
        record_sha256="0" * 64,
    ).with_computed_sha256()


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS run_observation (
    observation_id TEXT PRIMARY KEY,
    cohort_id TEXT NOT NULL,
    scenario_cluster_id TEXT NOT NULL,
    pair_key TEXT NOT NULL,
    terminal_state TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    record_sha256 TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_observation_cohort
    ON run_observation (cohort_id);
CREATE INDEX IF NOT EXISTS idx_run_observation_cluster
    ON run_observation (scenario_cluster_id);
-- append-only 结构强制：数据库层触发器拒绝任何 UPDATE/DELETE，
-- 使"不完整 observation 永久保留、不删除"不依赖调用方自觉。
CREATE TRIGGER IF NOT EXISTS run_observation_no_update
BEFORE UPDATE ON run_observation
BEGIN
    SELECT RAISE(ABORT, 'run_observation.append_only');
END;
CREATE TRIGGER IF NOT EXISTS run_observation_no_delete
BEFORE DELETE ON run_observation
BEGIN
    SELECT RAISE(ABORT, 'run_observation.append_only');
END;
"""


class RunObservationStore:
    """隔离的 RunObservation/audit SQLite 存储（append-only）。

    纪律声明：本类不持有、不调用任何 request/effect/task/life/memory/
    投影/缓存 store；唯一的持久化目标是自己的独立 SQLite 文件
    （默认 runtime/state/execution_shadow/run-observations.sqlite3），
    绝不在 gateway.sqlite3 中开表。
    """

    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self._conn = connection
        self._path = path
        self._lock = threading.Lock()

    @classmethod
    def open(cls, path: str | Path | None = None) -> "RunObservationStore":
        db_path = Path(path) if path is not None else DEFAULT_OBSERVATION_DB_PATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(db_path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        with connection:
            connection.executescript(_SCHEMA_SQL)
        return cls(connection, db_path)

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        self._conn.close()

    # ---- 写入：仅 INSERT；UPDATE/DELETE 一律抛错（另由 DB 触发器兜底）----

    def append(self, observation: RunObservation) -> None:
        if not observation.has_valid_sha256():
            raise ValueError("run observation record digest is invalid")
        record_json = observation.model_dump_json()
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    "INSERT INTO run_observation ("
                    "observation_id, cohort_id, scenario_cluster_id, pair_key,"
                    " terminal_state, created_at_ms, record_sha256, record_json"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        observation.observation_id,
                        observation.cohort_id,
                        observation.scenario_cluster_id,
                        observation.pair_key,
                        observation.terminal_state,
                        observation.created_at_ms,
                        observation.record_sha256,
                        record_json,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ObservationConflictError(
                    f"duplicate run observation: {observation.observation_id}"
                ) from exc

    def update(self, *_args: object, **_kwargs: object) -> None:
        raise AppendOnlyViolationError("run observation store is append-only")

    def delete(self, *_args: object, **_kwargs: object) -> None:
        raise AppendOnlyViolationError("run observation store is append-only")

    # ---- 读取与统计 ----

    def _decode(self, row: sqlite3.Row) -> RunObservation:
        try:
            return RunObservation.model_validate_json(row["record_json"], strict=True)
        except ValueError as exc:
            raise ObservationStoreError(
                f"stored run observation is undecodable: {row['observation_id']}"
            ) from exc

    def get(self, observation_id: str) -> RunObservation | None:
        row = self._conn.execute(
            "SELECT record_json, observation_id FROM run_observation"
            " WHERE observation_id = ?",
            (observation_id,),
        ).fetchone()
        return None if row is None else self._decode(row)

    def query_by_cohort(self, cohort_id: str) -> tuple[RunObservation, ...]:
        rows = self._conn.execute(
            "SELECT record_json, observation_id FROM run_observation"
            " WHERE cohort_id = ? ORDER BY created_at_ms, observation_id",
            (cohort_id,),
        ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def query_by_cluster(self, scenario_cluster_id: str) -> tuple[RunObservation, ...]:
        rows = self._conn.execute(
            "SELECT record_json, observation_id FROM run_observation"
            " WHERE scenario_cluster_id = ? ORDER BY created_at_ms, observation_id",
            (scenario_cluster_id,),
        ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def complete_pair_count(self, cohort_id: str) -> int:
        # 仅 terminal_state=completed 的记录构成完整 pair；
        # timeout（质量失败）与 incomplete（永久保留的残缺）均不进入。
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM run_observation"
            " WHERE cohort_id = ? AND terminal_state = 'completed'",
            (cohort_id,),
        ).fetchone()
        return int(row["n"])

    def incomplete_observation_count(self, cohort_id: str | None = None) -> int:
        # 不完整 observation 永久留在审计全集并计数、不删除。
        if cohort_id is None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM run_observation"
                " WHERE terminal_state = 'incomplete'"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM run_observation"
                " WHERE terminal_state = 'incomplete' AND cohort_id = ?",
                (cohort_id,),
            ).fetchone()
        return int(row["n"])

    def timeout_observation_count(self, cohort_id: str) -> int:
        # 完整终态 timeout 是质量失败，单独计数以便质量维度可见。
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM run_observation"
            " WHERE cohort_id = ? AND terminal_state = 'timeout'",
            (cohort_id,),
        ).fetchone()
        return int(row["n"])

    def integrity_check(self) -> tuple[str, ...]:
        """扫描审计全集，返回 record_sha256 校验失败（疑似篡改）的 observation_id。"""
        rows = self._conn.execute(
            "SELECT observation_id, record_sha256, record_json FROM run_observation"
            " ORDER BY observation_id"
        ).fetchall()
        tampered: list[str] = []
        for row in rows:
            try:
                observation = RunObservation.model_validate_json(
                    row["record_json"], strict=True
                )
            except ValueError:
                tampered.append(str(row["observation_id"]))
                continue
            if (
                observation.observation_id != row["observation_id"]
                or observation.record_sha256 != row["record_sha256"]
                or not observation.has_valid_sha256()
            ):
                tampered.append(str(row["observation_id"]))
        return tuple(tampered)


__all__ = [
    "AppendOnlyViolationError",
    "DEFAULT_OBSERVATION_DB_PATH",
    "ModelSnapshot",
    "ObservationConflictError",
    "ObservationStoreError",
    "RunObservation",
    "RunObservationStore",
    "TerminalState",
    "build_run_observation",
    "derive_cohort_id",
    "derive_pair_key",
]
