// Serialize commands so a slow move can never arrive after the release's Stop.
// Only the latest desired speed is retained; there is no queue of stale moves.
export function createLensControl(send: (speed: number) => Promise<unknown>, onError: (error: unknown) => void) {
  let desired = 0;
  let running = false;
  let dirty = false;
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  async function pump() {
    if (running) return;
    clearTimeout(timer);
    running = true;
    try {
      while (dirty) {
        dirty = false;
        const speed = desired;
        try { await send(speed); }
        catch (error) {
          desired = 0;
          dirty = false;
          // The move may have reached the device even if its response was lost.
          if (speed !== 0) {
            try { await send(0); } catch { /* Device-side timeout is the final stop. */ }
          }
          onError(error);
          break;
        }
      }
    } finally {
      running = false;
      if (desired !== 0 && !disposed) timer = setTimeout(() => { dirty = true; void pump(); }, 300);
    }
  }
  return {
    setSpeed(speed: number) {
      if (disposed) return;
      desired = Math.max(-1, Math.min(1, speed));
      dirty = true;
      void pump();
    },
    stop() {
      if (desired === 0 && !running) return;
      desired = 0;
      dirty = true;
      clearTimeout(timer);
      void pump();
    },
    dispose() {
      disposed = true;
      clearTimeout(timer);
      if (desired !== 0 || running) {
        desired = 0;
        dirty = true;
        void pump();
      }
    },
  };
}
