import { useEffect, useState } from "react";
import StartPage from "./components/StartPage";
import RunView from "./components/RunView";
import Glossary from "./components/Glossary";
import History from "./components/History";
import Logo from "./components/Logo";

/** The open run and, for an old link (#/run/<id>/powerbi, /chat, /cleaning ...), the name of the tab it points to. */
function useHashRun(): [string | null, string | null, (id: string | null) => void] {
  const read = () => {
    const m = window.location.hash.match(/^#\/run\/([a-f0-9]{32}|[a-f0-9]{12})(?:\/([\w-]+))?\/?$/);
    return m ? { id: m[1], tab: m[2] ?? null } : { id: null, tab: null };
  };
  const [at, setAt] = useState(read);
  useEffect(() => {
    const on = () => setAt(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = (next: string | null) => {
    window.location.hash = next ? `#/run/${next}` : "#/";
  };
  return [at.id, at.tab, go];
}

export default function App() {
  const [runId, runTab, go] = useHashRun();
  const readPage = () => (window.location.hash === "#/glossary" ? "glossary" : window.location.hash === "#/history" ? "history" : window.location.hash === "#/new" ? "new" : "");
  const [page, setPage] = useState(readPage);
  useEffect(() => {
    const on = () => setPage(readPage());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return (
    <div className="shell">
      {runId ? <div id="runbar-slot" className="runbar-slot" /> : (
      <header className="topbar">
        <a className="brand" href="#/" onClick={() => go(null)}>
          <Logo size={30} />
          Analyst Studio
        </a>
        <div className="topright">
          <a className={`navlink ${page === "history" ? "navlink-on" : ""}`} href="#/history">History</a>
          <a className={`navlink ${page === "glossary" ? "navlink-on" : ""}`} href="#/glossary">Glossary</a>
          <a className="navlink navlink-primary" href="#/new">New analysis</a>
        </div>
      </header>
      )}
      <main>{page === "glossary" ? <Glossary /> : page === "history" ? <History /> : runId ? <RunView key={runId} runId={runId} tab={runTab} /> : <StartPage onOpenRun={(id) => go(id)} openNew={page === "new"} />}</main>
      <footer className="foot">
        <span>Analyst Studio</span>
        <span>The AI suggests. Code calculates.</span>
      </footer>
    </div>
  );
}
