import { Navigate, Route, Routes } from "react-router-dom";
import { useState } from "react";
import { getToken } from "./api/client";
import { EventsProvider } from "./api/events";
import Shell from "./components/Shell";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Requests from "./pages/Requests";
import ReceiptPage from "./pages/Receipt";
import Policies from "./pages/Policies";
import RouterLab from "./pages/RouterLab";
import CachePage from "./pages/Cache";
import FinOps from "./pages/FinOps";
import Infrastructure from "./pages/Infrastructure";

export default function App() {
  const [authed, setAuthed] = useState(() => !!getToken());
  if (!authed) return <Login onLogin={() => setAuthed(true)} />;
  return (
    <EventsProvider>
      <Shell onLogout={() => setAuthed(false)}>
        <Routes>
          <Route path="/" element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/requests" element={<Requests />} />
          <Route path="/requests/:receiptId" element={<ReceiptPage />} />
          <Route path="/policies" element={<Policies />} />
          <Route path="/router-lab" element={<RouterLab />} />
          <Route path="/cache" element={<CachePage />} />
          <Route path="/finops" element={<FinOps />} />
          <Route path="/infrastructure" element={<Infrastructure />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Routes>
      </Shell>
    </EventsProvider>
  );
}
