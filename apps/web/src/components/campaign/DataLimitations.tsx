"use client";

/**
 * What the AI did not know.
 *
 * Shown next to the strategy rather than hidden in a tooltip: a reviewer
 * deciding whether to spend budget needs to see that a recommendation was made
 * without historical performance data. Renders nothing when the list is empty —
 * an empty panel would imply the data was complete.
 */
export function DataLimitations({
  items,
  title = "Data limitations",
}: {
  items: string[];
  title?: string;
}) {
  if (!items.length) return null;

  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-amber-900">{title}</div>
      <ul className="mt-2 space-y-1 text-sm text-amber-900">
        {items.map((item, index) => (
          <li key={`${index}-${item}`} className="flex gap-2">
            <span aria-hidden>·</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
