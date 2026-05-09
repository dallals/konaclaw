import { useEffect, useState } from "react";

const TICK_MS = 250;
const CHARS_PER_TOKEN = 4;

export function useLiveTokensPerSecond(
  streamingBuf: string,
  startedAtMs: number | null,
): number | null {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!streamingBuf || startedAtMs == null) return;
    const id = setInterval(() => setTick((t) => t + 1), TICK_MS);
    return () => clearInterval(id);
  }, [streamingBuf, startedAtMs]);

  if (!streamingBuf || startedAtMs == null) return null;
  const elapsedSeconds = Math.max((Date.now() - startedAtMs) / 1000, 0.001);
  const estimatedTokens = streamingBuf.length / CHARS_PER_TOKEN;
  void tick;
  return estimatedTokens / elapsedSeconds;
}
