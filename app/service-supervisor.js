"use strict";

const SERVICE_STATUS = Object.freeze({
  STOPPED: "STOPPED",
  STARTING: "STARTING",
  RUNNING: "RUNNING",
  DEGRADED: "DEGRADED",
  RESTARTING: "RESTARTING",
  DRAINING: "DRAINING",
});

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

class ServiceSupervisor {
  constructor({
    services,
    failureThreshold = 12,
    restartDelayMs = 750,
    monitorIntervalMs = 5000,
    restartBackoffBaseMs = 2000,
    restartBackoffMaxMs = 30000,
    healthyResetMs = 300000,
    delay = sleep,
    onTransition = () => {},
  } = {}) {
    if (!Array.isArray(services) || services.length === 0) {
      throw new TypeError("services must be a non-empty array");
    }
    if (!Number.isInteger(failureThreshold) || failureThreshold < 1) {
      throw new TypeError("failureThreshold must be a positive integer");
    }
    if (!Number.isInteger(restartDelayMs) || restartDelayMs < 0) {
      throw new TypeError("restartDelayMs must be a non-negative integer");
    }
    if (!Number.isInteger(monitorIntervalMs) || monitorIntervalMs < 10) {
      throw new TypeError("monitorIntervalMs must be at least 10 milliseconds");
    }
    if (!Number.isInteger(restartBackoffBaseMs) || restartBackoffBaseMs < 0) {
      throw new TypeError("restartBackoffBaseMs must be a non-negative integer");
    }
    if (!Number.isInteger(restartBackoffMaxMs) || restartBackoffMaxMs < 0) {
      throw new TypeError("restartBackoffMaxMs must be a non-negative integer");
    }
    if (!Number.isInteger(healthyResetMs) || healthyResetMs < 0) {
      throw new TypeError("healthyResetMs must be a non-negative integer");
    }
    if (typeof delay !== "function" || typeof onTransition !== "function") {
      throw new TypeError("delay and onTransition must be functions");
    }

    this._services = new Map();
    this._states = new Map();
    for (const [order, raw] of services.entries()) {
      const definition = { phase: 0, ...raw, order };
      if (
        !definition
        || typeof definition.name !== "string"
        || !definition.name
        || !Number.isInteger(definition.phase)
        || definition.phase < 0
        || typeof definition.start !== "function"
        || typeof definition.health !== "function"
        || typeof definition.stop !== "function"
        || (definition.ready != null && typeof definition.ready !== "function")
      ) {
        throw new TypeError("service definition is invalid");
      }
      if (this._services.has(definition.name)) throw new TypeError("service names must be unique");
      this._services.set(definition.name, definition);
      this._states.set(definition.name, {
        name: definition.name,
        phase: definition.phase,
        status: SERVICE_STATUS.STOPPED,
        running: false,
        ready: false,
        consecutiveFailures: 0,
        restartCount: 0,
        lastError: "",
        restartBackoffLevel: 0,
        healthySince: 0,
        startPromise: null,
        restartPromise: null,
      });
    }

    this._failureThreshold = failureThreshold;
    this._restartDelayMs = restartDelayMs;
    this._monitorIntervalMs = monitorIntervalMs;
    this._restartBackoffBaseMs = restartBackoffBaseMs;
    this._restartBackoffMaxMs = restartBackoffMaxMs;
    this._healthyResetMs = healthyResetMs;
    this._delay = delay;
    this._onTransition = onTransition;
    this._draining = false;
    this._drainPromise = null;
    this._pollPromise = null;
    this._monitorTimer = null;
  }

  get draining() {
    return this._draining;
  }

  _state(name) {
    const state = this._states.get(name);
    if (!state) throw new Error(`unknown service: ${name}`);
    return state;
  }

  _transition(name, status, patch = {}) {
    const state = this._state(name);
    const previous = state.status;
    Object.assign(state, patch, { status });
    try {
      this._onTransition({ name, previous, status, ...this.snapshot()[name] });
    } catch (_error) {
      // Diagnostics must never own the service lifecycle.
    }
  }

  snapshot() {
    const result = {};
    for (const [name, state] of this._states) {
      result[name] = {
        name,
        phase: state.phase,
        status: state.status,
        running: state.running,
        ready: state.ready,
        consecutiveFailures: state.consecutiveFailures,
        restartCount: state.restartCount,
        lastError: state.lastError,
      };
    }
    return result;
  }

  async _start(name) {
    const definition = this._services.get(name);
    const state = this._state(name);
    if (state.startPromise) return state.startPromise;
    const operation = (async () => {
      this._transition(name, SERVICE_STATUS.STARTING, {
        running: false,
        ready: false,
        lastError: "",
      });
      try {
        if ((await definition.start()) !== true) throw new Error("service_start_failed");
        if ((await definition.health()) !== true) throw new Error("service_health_failed_after_start");
        let ready = true;
        let readyError = "";
        try {
          ready = definition.ready ? (await definition.ready()) === true : true;
        } catch (error) {
          ready = false;
          readyError = error?.message || String(error);
        }
        this._transition(name, ready ? SERVICE_STATUS.RUNNING : SERVICE_STATUS.DEGRADED, {
          running: true,
          ready,
          consecutiveFailures: 0,
          healthySince: Date.now(),
          lastError: ready ? "" : (readyError || "service_not_ready"),
        });
        return { running: true, ready };
      } catch (error) {
        const message = error?.message || String(error);
        this._transition(name, SERVICE_STATUS.STOPPED, {
          running: false,
          ready: false,
          healthySince: 0,
          lastError: message,
        });
        return { running: false, ready: false, error: message };
      }
    })();
    state.startPromise = operation;
    try {
      return await operation;
    } finally {
      if (state.startPromise === operation) state.startPromise = null;
    }
  }

  async start(name) {
    if (this._draining) return { running: false, ready: false, error: "services_draining" };
    return this._start(name);
  }

  async startAll() {
    if (this._drainPromise) await this._drainPromise;
    this._draining = false;
    const phases = [...new Set([...this._services.values()].map((item) => item.phase))].sort((a, b) => a - b);
    for (const phase of phases) {
      if (this._draining) break;
      const names = [...this._services.values()].filter((item) => item.phase === phase).map((item) => item.name);
      await Promise.all(names.map((name) => this._start(name)));
    }
    return this.snapshot();
  }

  async stop(name, reason = "service-stop") {
    const definition = this._services.get(name);
    const state = this._state(name);
    const inFlight = [state.startPromise, state.restartPromise].filter(Boolean);
    if (inFlight.length) await Promise.allSettled(inFlight);
    this._transition(name, SERVICE_STATUS.DRAINING, { ready: false });
    try {
      await definition.stop(reason);
      this._transition(name, SERVICE_STATUS.STOPPED, {
        running: false,
        ready: false,
        consecutiveFailures: 0,
        lastError: "",
      });
      return { running: false, ready: false };
    } catch (error) {
      const message = error?.message || String(error);
      this._transition(name, SERVICE_STATUS.STOPPED, {
        running: false,
        ready: false,
        consecutiveFailures: 0,
        lastError: message,
      });
      return { running: false, ready: false, error: message };
    }
  }

  async restart(name, reason = "health-check-failed") {
    const definition = this._services.get(name);
    const state = this._state(name);
    if (this._draining) return { running: false, ready: false, error: "services_draining" };
    if (state.restartPromise) return state.restartPromise;
    const operation = (async () => {
      this._transition(name, SERVICE_STATUS.RESTARTING, {
        running: false,
        ready: false,
        restartCount: state.restartCount + 1,
        healthySince: 0,
        lastError: String(reason || "service_restart"),
      });
      try {
        await definition.stop(reason);
      } catch (error) {
        state.lastError = error?.message || String(error);
      }
      if (this._restartDelayMs) await this._delay(this._restartDelayMs);
      // Exponential restart backoff (2s, 4s, 8s, ... capped at
      // restartBackoffMaxMs) prevents a busy service from being cold-restarted
      // in a tight loop.  The level resets after healthyResetMs of continuous
      // healthy polling (see _pollService).
      const backoffMs = Math.min(
        this._restartBackoffBaseMs * (2 ** state.restartBackoffLevel),
        this._restartBackoffMaxMs,
      );
      state.restartBackoffLevel += 1;
      if (backoffMs) await this._delay(backoffMs);
      if (this._draining) return { running: false, ready: false, error: "services_draining" };
      return this._start(name);
    })();
    state.restartPromise = operation;
    try {
      return await operation;
    } finally {
      if (state.restartPromise === operation) state.restartPromise = null;
    }
  }

  async _pollService(name) {
    const definition = this._services.get(name);
    const state = this._state(name);
    if (this._draining || state.startPromise || state.restartPromise) return;
    if (!state.running) {
      await this.restart(name, "service-not-running");
      return;
    }
    let healthy = false;
    try {
      healthy = (await definition.health()) === true;
    } catch (error) {
      state.lastError = error?.message || String(error);
    }
    if (!healthy) {
      const failures = state.consecutiveFailures + 1;
      this._transition(name, SERVICE_STATUS.DEGRADED, {
        ready: false,
        consecutiveFailures: failures,
        healthySince: 0,
        lastError: state.lastError || "service_health_failed",
      });
      if (failures >= this._failureThreshold) await this.restart(name, "health-check-failed");
      return;
    }
    // A continuously healthy service earns back the shortest restart backoff.
    const now = Date.now();
    if (!state.healthySince) state.healthySince = now;
    if (state.restartBackoffLevel && now - state.healthySince >= this._healthyResetMs) {
      state.restartBackoffLevel = 0;
    }
    let ready = true;
    let readyError = "";
    try {
      ready = definition.ready ? (await definition.ready()) === true : true;
    } catch (error) {
      ready = false;
      readyError = error?.message || String(error);
    }
    this._transition(name, ready ? SERVICE_STATUS.RUNNING : SERVICE_STATUS.DEGRADED, {
      running: true,
      ready,
      consecutiveFailures: 0,
      lastError: ready ? "" : (readyError || "service_not_ready"),
    });
  }

  async poll() {
    if (this._draining) return this.snapshot();
    if (this._pollPromise) return this._pollPromise;
    const operation = (async () => {
      await Promise.all([...this._services.keys()].map((name) => this._pollService(name)));
      return this.snapshot();
    })();
    this._pollPromise = operation;
    try {
      return await operation;
    } finally {
      if (this._pollPromise === operation) this._pollPromise = null;
    }
  }

  startMonitoring() {
    if (this._monitorTimer || this._draining) return;
    this._monitorTimer = setInterval(() => {
      this.poll().catch(() => {});
    }, this._monitorIntervalMs);
  }

  stopMonitoring() {
    if (!this._monitorTimer) return;
    clearInterval(this._monitorTimer);
    this._monitorTimer = null;
  }

  async drainAll(reason = "app-exit") {
    if (this._drainPromise) return this._drainPromise;
    this._draining = true;
    this.stopMonitoring();
    const operation = (async () => {
      if (this._pollPromise) await this._pollPromise.catch(() => {});
      const inFlight = [];
      for (const state of this._states.values()) {
        if (state.startPromise) inFlight.push(state.startPromise);
        if (state.restartPromise) inFlight.push(state.restartPromise);
      }
      if (inFlight.length) await Promise.allSettled(inFlight);
      const definitions = [...this._services.values()].sort(
        (left, right) => (right.phase - left.phase) || (right.order - left.order),
      );
      for (const definition of definitions) {
        const state = this._state(definition.name);
        this._transition(definition.name, SERVICE_STATUS.DRAINING, { ready: false });
        try {
          await definition.stop(reason);
        } catch (error) {
          state.lastError = error?.message || String(error);
        }
        this._transition(definition.name, SERVICE_STATUS.STOPPED, {
          running: false,
          ready: false,
          consecutiveFailures: 0,
        });
      }
      return this.snapshot();
    })();
    this._drainPromise = operation;
    try {
      return await operation;
    } finally {
      if (this._drainPromise === operation) this._drainPromise = null;
      // 复位 draining：唯一旧复位点是 startAll()，drainAll 后走 start()/
      // restart() 的调用方会恒收 services_draining，服务永远拉不起来。
      if (!this._drainPromise) this._draining = false;
    }
  }
}

module.exports = { SERVICE_STATUS, ServiceSupervisor };
