import { useEffect, useState } from "react";
import { Navigate, Route, Routes, Link, useLocation } from "react-router-dom";
import { QueueView } from "./pages/QueueView";
import { DetailView } from "./pages/DetailView";
import { DashboardView } from "./pages/DashboardView";
import { ProfilesView } from "./pages/ProfilesView";

const NAV_LINKS = [
  { to: "/", label: "Queue", match: (p: string) => p === "/" || p.startsWith("/denials") },
  { to: "/dashboard", label: "Monitoring", match: (p: string) => p.startsWith("/dashboard") },
  { to: "/profiles", label: "Profiles", match: (p: string) => p.startsWith("/profiles") },
];

function TopNav() {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  // Close the mobile menu on every route change so a link tap doesn't leave
  // a stale open panel behind.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  return (
    <header className="sticky top-0 z-10 border-b border-ink-200 bg-white/90 backdrop-blur">
      <div className="flex w-full items-center justify-between gap-3 px-4 py-3.5 sm:px-6 lg:px-10">
        <Link to="/" className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-600 text-white shadow-sm">
            <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path
                fillRule="evenodd"
                d="M10 1.5a1 1 0 01.894.553l7 14A1 1 0 0117 17.5H3a1 1 0 01-.894-1.447l7-14A1 1 0 0110 1.5zm-.75 5.25a.75.75 0 011.5 0v4.5a.75.75 0 01-1.5 0v-4.5zM10 13a1 1 0 100 2 1 1 0 000-2z"
                clipRule="evenodd"
              />
            </svg>
          </div>
          <div className="min-w-0 leading-tight">
            <div className="truncate text-sm font-semibold text-ink-900">Claims Denial Triage</div>
            <div className="hidden truncate text-[11px] text-ink-400 sm:block">AI-assisted appeal drafting</div>
          </div>
        </Link>

        {/* Desktop nav -- horizontal links, unchanged above the md breakpoint. */}
        <nav className="hidden items-center gap-1 text-sm md:flex">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.to}
              to={link.to}
              className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
                link.match(location.pathname)
                  ? "bg-brand-50 text-brand-700"
                  : "text-ink-500 hover:bg-ink-50 hover:text-ink-800"
              }`}
            >
              {link.label}
            </Link>
          ))}
        </nav>

        {/* Mobile hamburger toggle -- below md, replaces the horizontal nav. */}
        <button
          type="button"
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-ink-600 hover:bg-ink-50 md:hidden"
          aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((v) => !v)}
        >
          {menuOpen ? (
            <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          ) : (
            <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M3 5.75A.75.75 0 013.75 5h12.5a.75.75 0 010 1.5H3.75A.75.75 0 013 5.75zm0 4.25a.75.75 0 01.75-.75h12.5a.75.75 0 010 1.5H3.75A.75.75 0 013 10zm.75 3.5a.75.75 0 000 1.5h12.5a.75.75 0 000-1.5H3.75z" clipRule="evenodd" />
            </svg>
          )}
        </button>
      </div>

      {/* Mobile dropdown panel -- shown below md when toggled open. */}
      {menuOpen && (
        <nav className="flex flex-col gap-1 border-t border-ink-200 bg-white px-4 py-3 md:hidden">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.to}
              to={link.to}
              className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                link.match(location.pathname)
                  ? "bg-brand-50 text-brand-700"
                  : "text-ink-600 hover:bg-ink-50 hover:text-ink-800"
              }`}
            >
              {link.label}
            </Link>
          ))}
        </nav>
      )}
    </header>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-ink-50">
      <TopNav />
      <main className="w-full px-4 py-8 sm:px-6 lg:px-10">
        <Routes>
          <Route path="/" element={<Navigate to="/denials" replace />} />
          <Route path="/denials" element={<QueueView />} />
          <Route path="/denials/:id" element={<DetailView />} />
          <Route path="/dashboard" element={<DashboardView />} />
          <Route path="/profiles" element={<ProfilesView />} />
          <Route
            path="*"
            element={
              <div className="py-24 text-center text-ink-500">Page not found.</div>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
