import { useEffect, useState } from "react";
import { api } from "./api";
import UploadForm from "./components/UploadForm";
import RunView from "./components/RunView";
import Glossary from "./components/Glossary";

function useHashRun(): [string | null, (id: string | null) => void] {
  const read = () => {
    const m = window.location.hash.match(/^#\/run\/([a-f0-9]{12})$/);
    return m ? m[1] : null;
  };
  const [id, setId] = useState<string | null>(read);
  useEffect(() => {
    const on = () => setId(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = (next: string | null) => {
    window.location.hash = next ? `#/run/${next}` : "#/";
  };
  return [id, go];
}

export default function App() {
  const [runId, go] = useHashRun();
  const [page, setPage] = useState(window.location.hash === "#/glossary");
  useEffect(() => {
    const on = () => setPage(window.location.hash === "#/glossary");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const [llm, setLlm] = useState<string>("");

  useEffect(() => {
    api.health().then((h) => setLlm(h.llm)).catch(() => setLlm("backend offline"));
  }, []);

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="#/" onClick={() => go(null)}>
          <span className="logo">◆</span> Analyst Studio
        </a>
        <div className="topright">
          <a className="navlink" href="#/glossary">Glossary</a>
          <span className={`pill ${llm.startsWith("rules") || llm === "backend offline" ? "pill-muted" : "pill-ok"}`} title="Which model plans, critiques and writes">
            {llm ? `Brain: ${llm}` : "…"}
          </span>
        </div>
      </header>
      <main>{page ? <Glossary /> : runId ? <RunView key={runId} runId={runId} /> : <UploadForm onStarted={(id) => go(id)} />}</main>
    </div>
  );
}
