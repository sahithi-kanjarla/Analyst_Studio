/** The Analyst Studio mark.
 *
 *  The idea: a spreadsheet column that has been worked out. Three columns stand side by side; the
 *  two outer ones are still cells, the middle one has resolved into a single solid figure. It is
 *  the app's own rule drawn as a shape — the one thing being pointed at is solid and in the accent,
 *  everything beside it stays quiet — and it reads as a table and as a chart at the same time.
 *
 *  Five rectangles and nothing else, so it survives 16px. The plate carries the accent and the
 *  shapes carry the ink that goes on it, which keeps it legible in both themes.
 *  frontend/public/logo-mark.svg, logo-wordmark.svg and the favicons are the same geometry.
 */
export default function Logo({ size = 32, title = "" }: { size?: number; title?: string }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} className="logo-svg" role={title ? "img" : undefined} aria-hidden={title ? undefined : true} aria-label={title || undefined}>
      {title && <title>{title}</title>}
      <rect width="32" height="32" rx="7.5" fill="var(--brand)" />
      <g fill="var(--brand-ink)">
        <rect x="7.2" y="8" width="4.4" height="7.3" rx="1.3" opacity=".42" />
        <rect x="7.2" y="16.7" width="4.4" height="7.3" rx="1.3" opacity=".42" />
        <rect x="13.8" y="8" width="4.4" height="16" rx="1.6" />
        <rect x="20.4" y="8" width="4.4" height="7.3" rx="1.3" opacity=".42" />
        <rect x="20.4" y="16.7" width="4.4" height="7.3" rx="1.3" opacity=".42" />
      </g>
    </svg>
  );
}
