import { useEffect, useState } from "react";
import { api, Brief, RunResult } from "../../api";
import Dialog from "../Dialog";
import { BriefView } from "../lens/LensAnswer";
import { useLens } from "../lens/LensContext";
import Menu from "./Menu";

export type ExportItem = "briefs" | "powerbi" | "print";

/** Everything that leaves the app, in one menu next to the report name: the PDF, the briefs, the Power BI files and printing. */
export default function ExportMenu({ run, onPick }: { run: RunResult; onPick: (item: ExportItem) => void }) {
  return (
    <Menu label="Export" className="export-menu">
      {(close) => (
        <>
          <a role="menuitem" className="menu-item" href={`/api/runs/${run.run_id}/export/report`} download onClick={close}>Download PDF</a>
          <button role="menuitem" type="button" className="menu-item" onClick={() => { close(); onPick("briefs"); }}>Briefs</button>
          <button role="menuitem" type="button" className="menu-item" onClick={() => { close(); onPick("powerbi"); }}>Power BI files</button>
          <button role="menuitem" type="button" className="menu-item" onClick={() => { close(); onPick("print"); }}>Print</button>
        </>
      )}
    </Menu>
  );
}

/** The saved briefs of this report, each with its own Download PDF; or how to ask for one. */
export function BriefsDialog({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [briefs, setBriefs] = useState<Brief[] | null>(null);
  const [error, setError] = useState("");
  const lens = useLens();
  useEffect(() => { api.briefsList(runId).then((r) => setBriefs(r.briefs)).catch(() => setError("The briefs could not be loaded.")); }, [runId]);
  return (
    <Dialog title="Briefs" intro="Short documents for one reader, written from this report. Download any of them as a PDF." onClose={onClose} wide>
      {error && <div className="error">{error}</div>}
      {!briefs && !error && <p className="muted"><span className="spinner" /> Loading briefs…</p>}
      {briefs && briefs.length === 0 && (
        <p className="muted">
          There are no saved briefs yet. Ask Lens for one, for example “write a brief for the board”.{" "}
          {lens?.enabled && <button type="button" className="chip" onClick={() => { onClose(); lens.setOpen(true); }}>Ask Lens</button>}
        </p>
      )}
      {briefs && briefs.length > 0 && (
        <div className="lens-briefs">
          {briefs.map((b) => <BriefView key={b.id} b={b} runId={runId} />)}
          {briefs.length > 1 && <p><a className="link" href={`/api/runs/${runId}/export/all_briefs`}>Download all briefs (zip of PDFs)</a></p>}
        </div>
      )}
    </Dialog>
  );
}
