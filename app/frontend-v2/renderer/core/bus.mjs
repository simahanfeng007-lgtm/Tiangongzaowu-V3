export function createBus() {
  const listeners = new Map();

  function on(eventName, handler) {
    const handlers = listeners.get(eventName) || new Set();
    handlers.add(handler);
    listeners.set(eventName, handlers);
    return () => handlers.delete(handler);
  }

  function emit(eventName, payload) {
    for (const handler of listeners.get(eventName) || []) {
      handler(payload);
    }
  }

  return { on, emit };
}
