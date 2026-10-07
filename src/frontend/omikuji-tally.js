(() => {
  "use strict";

  // A tiny per-browser snapshot, never individual results or messages.
  const STORAGE_KEY = "chat-como.omikuji-tally.v1";

  class ComoFortuneTally {
    constructor(ids, { storage, onChange = () => {} } = {}) {
      this.ids = [...new Set(ids)];
      this.onChange = onChange;
      this.counts = this.emptyCounts();
      this.persistent = false;
      try {
        this.storage = storage === undefined ? window.localStorage : storage;
      } catch {
        this.storage = null;
      }
      this.counts = this.readCounts() || this.counts;
      this.emit();
      window.addEventListener("storage", (event) => {
        if (event.storageArea !== this.storage || (event.key !== STORAGE_KEY && event.key !== null)) return;
        const stored = this.readCounts();
        if (this.persistent) this.counts = stored || this.emptyCounts();
        this.emit();
      });
    }

    emptyCounts() {
      return Object.fromEntries(this.ids.map((id) => [id, 0]));
    }

    readCounts() {
      if (!this.storage) return null;
      try {
        const raw = this.storage.getItem(STORAGE_KEY);
        this.persistent = true;
        if (raw === null) return null;
        const saved = JSON.parse(raw);
        if (saved?.version !== 1 || !saved.counts || typeof saved.counts !== "object" || Array.isArray(saved.counts)) return null;
        const counts = Object.fromEntries(this.ids.map((id) => {
          const value = saved.counts[id];
          return [id, Number.isSafeInteger(value) && value >= 0 ? value : 0];
        }));
        return Number.isSafeInteger(Object.values(counts).reduce((sum, count) => sum + count, 0)) ? counts : null;
      } catch {
        // Corrupt data or blocked storage must never prevent drawing or chatting.
        this.persistent = false;
        return null;
      }
    }

    snapshot() {
      return {
        counts: { ...this.counts },
        total: Object.values(this.counts).reduce((sum, count) => sum + count, 0),
        persistent: this.persistent,
      };
    }

    emit() {
      this.onChange(this.snapshot());
    }

    record(id) {
      if (!this.ids.includes(id)) return false;
      const stored = this.readCounts();
      if (stored) {
        // Read the latest tab's totals, keeping unsaved in-memory increments too.
        for (const key of this.ids) this.counts[key] = Math.max(this.counts[key], stored[key]);
      }
      if (this.snapshot().total >= Number.MAX_SAFE_INTEGER) return false;
      this.counts[id] += 1;
      try {
        if (!this.storage) throw new Error("Storage unavailable");
        this.storage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, counts: this.counts }));
        this.persistent = true;
      } catch {
        this.persistent = false;
      }
      this.emit();
      return true;
    }
  }

  window.ComoFortuneTally = ComoFortuneTally;
})();
