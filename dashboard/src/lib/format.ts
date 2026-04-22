/**
 * Display formatters for dates, durations, numbers, and bytes.
 *
 * Used by every list view in the dashboard. Centralised here so
 * the same task in two different views shows the same human
 * representation.
 *
 * Implementation note (npm supply-chain hardening, April 2026):
 * this module previously depended on ``date-fns``. The two
 * functions we used (``formatDistanceToNow`` + a fixed-pattern
 * ``format``) are 30 lines of native code against ``Intl``, and
 * dropping the dep removes one direct + ~30 transitive npm
 * packages from our publish surface. See SECURITY.md §16 for the
 * supply-chain policy that motivated this.
 */

const _RTF = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

/** Render an ISO timestamp as "5 minutes ago". */
export function formatRelative(value: string | Date | null | undefined): string {
  if (!value) return "-";
  try {
    const date = typeof value === "string" ? new Date(value) : value;
    const diffMs = date.getTime() - Date.now();
    const absSec = Math.abs(diffMs) / 1000;
    if (absSec < 45) return _RTF.format(Math.round(diffMs / 1000), "second");
    if (absSec < 2700) return _RTF.format(Math.round(diffMs / 60_000), "minute");
    if (absSec < 64_800) return _RTF.format(Math.round(diffMs / 3_600_000), "hour");
    if (absSec < 2_160_000) return _RTF.format(Math.round(diffMs / 86_400_000), "day");
    if (absSec < 31_104_000) return _RTF.format(Math.round(diffMs / 2_592_000_000), "month");
    return _RTF.format(Math.round(diffMs / 31_536_000_000), "year");
  } catch {
    return "-";
  }
}

/** Render an ISO timestamp as "2026-04-11 14:30:05" (UTC-naive local time). */
export function formatAbsolute(
  value: string | Date | null | undefined,
): string {
  if (!value) return "-";
  try {
    const d = typeof value === "string" ? new Date(value) : value;
    if (Number.isNaN(d.getTime())) return "-";
    const pad = (n: number) => n.toString().padStart(2, "0");
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
      `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    );
  } catch {
    return "-";
  }
}

/** Render a millisecond duration as "1.2s" or "230ms" or "-". */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "-";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
  return `${Math.floor(ms / 3_600_000)}h ${Math.round((ms % 3_600_000) / 60_000)}m`;
}

/** Compact integer rendering: 1234 → "1.2k". */
export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  if (Math.abs(value) < 1000) return value.toString();
  if (Math.abs(value) < 1_000_000) return `${(value / 1000).toFixed(1)}k`;
  if (Math.abs(value) < 1_000_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  return `${(value / 1_000_000_000).toFixed(1)}B`;
}

/** "12.5%" with a sensible default for 0/0 and NaN. */
export function formatPercent(
  value: number | null | undefined,
  fractionDigits = 1,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "0%";
  return `${(value * 100).toFixed(fractionDigits)}%`;
}

/** Truncate a long string with an ellipsis. */
export function truncate(value: string, max = 60): string {
  if (value.length <= max) return value;
  return value.slice(0, max - 1) + "…";
}
