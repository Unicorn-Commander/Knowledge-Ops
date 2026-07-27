/* Knowledge-Ops shell — tiny synchronous event bus.
   Contract events: 'ask:run' {query, scope, k} · 'ask:results' {query, passages}
   · 'constellation:focus' {ids} · 'nav' {view} · 'theme' {name}.
   A throwing handler never breaks other subscribers. */

export function createBus() {
  const handlers = new Map();
  return {
    on(evt, fn) {
      let set = handlers.get(evt);
      if (!set) { set = new Set(); handlers.set(evt, set); }
      set.add(fn);
      return () => set.delete(fn);
    },
    emit(evt, payload) {
      const set = handlers.get(evt);
      if (!set) return;
      for (const fn of [...set]) {
        try { fn(payload); } catch (e) { console.warn(`[ko:bus] handler for "${evt}" threw`, e); }
      }
    },
  };
}
