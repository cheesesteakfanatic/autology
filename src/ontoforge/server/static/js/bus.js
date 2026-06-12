/* The inter-app bus — one tiny WM-owned pub/sub. Apps never import each
   other; they emit namespaced intents ('entity:open', 'evidence:atoms',
   'class:focus', 'ask:run') and the WM owns the routing policy. on()
   returns an unsubscribe so window lifecycles can dispose every
   subscription they made. */

export function createBus() {
  const topics = new Map(); // event -> Set<fn>

  function on(event, fn) {
    if (!topics.has(event)) topics.set(event, new Set());
    topics.get(event).add(fn);
    return () => {
      const set = topics.get(event);
      if (set) {
        set.delete(fn);
        if (!set.size) topics.delete(event);
      }
    };
  }

  function emit(event, payload = {}) {
    const set = topics.get(event);
    if (!set) return;
    for (const fn of [...set]) {
      try { fn(payload); } catch (e) { console.error(`bus handler for ${event} failed`, e); }
    }
  }

  return { on, emit };
}
