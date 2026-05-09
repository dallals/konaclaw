export function formatTokensPerSecond(tps: number): string {
  if (tps >= 10) return `${Math.round(tps)} t/s`;
  return `${tps.toFixed(1)} t/s`;
}

export function formatTokenCount(n: number): string {
  if (n < 10000) return `${n} tok`;
  return `${(n / 1000).toFixed(1)}k tok`;
}

export function formatTtfb(ms: number): string {
  const s = ms / 1000;
  if (s < 10) return `${s.toFixed(2)} s`;
  return `${s.toFixed(1)} s`;
}
