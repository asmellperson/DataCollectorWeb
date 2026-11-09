export async function withRequestTimeout<T>(
  request: (signal: AbortSignal) => Promise<T>,
  {
    signal,
    timeoutMs,
    message,
  }: { signal?: AbortSignal; timeoutMs: number; message: string },
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => controller.abort(new Error(message)), timeoutMs);
  try {
    controller.signal.throwIfAborted();
    // Keep the deadline active while the response body is being read, too.
    return await request(controller.signal);
  } catch (error) {
    if (controller.signal.aborted) throw controller.signal.reason;
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
