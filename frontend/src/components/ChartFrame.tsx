import { useState } from "react";
import Dialog from "./Dialog";

/** The one frame every chart picture sits in: the same border, padding and radius, a thumbnail with a set maximum height, and an "Open full size" control that
 *  shows the picture large in a popup. Inside a fold ("Details, trust and next steps", "Show technical detail") the thumbnail is smaller still. */
export default function ChartFrame({ src, alt, title, onOpen }: { src: string; alt: string; title?: string; onOpen?: () => void }) {
  const [open, setOpen] = useState(false);
  const show = () => (onOpen ? onOpen() : setOpen(true));          // a dashboard tile opens its own full-size view (the chart with its details)
  const name = title || alt || "Chart";
  return (
    <figure className="chart-frame">
      <button type="button" className="chart-frame-img" onClick={show} aria-label={`Open full size: ${name}`}>
        <img src={src} alt={alt} loading="lazy" />
      </button>
      <button type="button" className="link chart-frame-open no-print" onClick={show}>Open full size</button>
      {open && (
        <Dialog title={name} onClose={() => setOpen(false)} wide>
          <img className="chart-full" src={src} alt={alt} />
        </Dialog>
      )}
    </figure>
  );
}
