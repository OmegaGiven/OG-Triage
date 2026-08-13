import { Navigate, Route, Routes, Link, useLocation } from "react-router-dom";
import { QueueView } from "./pages/QueueView";
import { DetailView } from "./pages/DetailView";
import { DashboardView } from "./pages/DashboardView";
import { ProfilesView } from "./pages/ProfilesView";

function TopNav() {
  const location = useLocation();
  const onQueue = location.pathname === "/" || location.pathname.startsWith("/denials");
  const onDashboard = location.pathname.startsWith("/dashboard");
  const onProfiles = location.pathname.startsWith("/profiles");
  return (
    <header className="sticky top-0 z-10 border-b border-ink-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1800px] items-center justify-between px-6 py-3.5">
        <Link to="/" className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white shadow-sm">
            <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path
                fillRule="evenodd"
                d="M10 1.5a1 1 0 01.894.553l7 14A1 1 0 0117 17.5H3a1 1 0 01-.894-1.447l7-14A1 1 0 0110 1.5zm-.75 5.25a.75.75 0 011.5 0v4.5a.75.75 0 01-1.5 0v-4.5zM10 13a1 1 0 100 2 1 1 0 000-2z"
                clipRule="evenodd"
              />
            </svg>
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-ink-900">Claims Denial Triage</div>
            <div className="text-[11px] text-ink-400">AI-assisted appeal drafting</div>
          </div>
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          <Link
            to="/"
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              onQueue ? "bg-brand-50 text-brand-700" : "text-ink-500 hover:bg-ink-50 hover:text-ink-800"
            }`}
          >
            Queue
          </Link>
          <Link
            to="/dashboard"
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              onDashboard ? "bg-brand-50 text-brand-700" : "text-ink-500 hover:bg-ink-50 hover:text-ink-800"
            }`}
          >
            Monitoring
          </Link>
          <Link
            to="/profiles"
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              onProfiles ? "bg-brand-50 text-brand-700" : "text-ink-500 hover:bg-ink-50 hover:text-ink-800"
            }`}
          >
            Profiles
          </Link>
        </nav>
      </div>
    </header>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-ink-50">
      <TopNav />
      <main className="mx-auto max-w-[1800px] px-6 py-8">
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
