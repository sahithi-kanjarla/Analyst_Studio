import { LensHowView, LensTableView } from "./LensAnswer";
import { useLens } from "./LensContext";

/** "Added by you from Lens": the answers pinned to this page. They sit apart from the report, which is not changed, and can be removed. */
export default function LensPins({ target }: { target: "report" | "dashboard" }) {
  const lens = useLens();
  const pins = (lens?.pins ?? []).filter((p) => p.target === target);
  if (!lens || !pins.length) return null;
  return (
    <section className="lens-pins rp-section" aria-labelledby={`lens-pins-${target}`} data-kind="lens">
      <h2 id={`lens-pins-${target}`}>Added by you from Lens</h2>
      <p className="muted lens-small">These answers were added from your questions. Everything above is the original {target === "report" ? "report" : "dashboard"}, unchanged.</p>
      <div className="grid">
        {pins.map((p) => (
          <figure className="lens-pin surface span-6" key={p.id}>
            <h3>{p.title}</h3>
            <p>{p.text}</p>
            {p.chart?.url ? <img src={p.chart.url} alt={p.text} loading="lazy" /> : p.table && <LensTableView table={p.table} max={12} />}
            {p.how && <details><summary>How this was calculated</summary><LensHowView how={p.how} /></details>}
            <button className="link no-print" onClick={() => lens.unpin(p.id)}>Remove from {target}</button>
          </figure>
        ))}
      </div>
    </section>
  );
}
