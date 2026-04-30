import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Link, NavLink, Route, Routes } from "react-router-dom";
import Aggregate from "./routes/Aggregate";
import Sessions from "./routes/Sessions";
import SessionDetail from "./routes/SessionDetail";
import CallDetail from "./routes/CallDetail";
import Calls from "./routes/Calls";
import Leaderboard from "./routes/Leaderboard";

function Layout({ children }: { children: React.ReactNode }) {
  const navLink = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-1.5 rounded text-sm ${
      isActive ? "bg-zinc-800 text-white" : "text-zinc-400 hover:text-white hover:bg-zinc-900"
    }`;
  return (
    <div className="min-h-screen">
      <header className="border-b border-zinc-800 bg-zinc-950 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-6">
          <Link to="/" className="font-mono text-lg font-semibold text-emerald-400">
            owlex
          </Link>
          <nav className="flex gap-1">
            <NavLink to="/" end className={navLink}>
              Overview
            </NavLink>
            <NavLink to="/sessions" className={navLink}>
              Sessions
            </NavLink>
            <NavLink to="/calls" className={navLink}>
              Calls
            </NavLink>
            <NavLink to="/leaderboard" className={navLink}>
              Leaderboard
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Aggregate />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/sessions/:cid" element={<SessionDetail />} />
          <Route path="/calls" element={<Calls />} />
          <Route path="/calls/:tid" element={<CallDetail />} />
          <Route path="/leaderboard" element={<Leaderboard />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  </React.StrictMode>
);
