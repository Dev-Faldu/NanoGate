import { useState } from "react";
import { KeyRound, Loader2 } from "lucide-react";
import { post, setToken, ApiError } from "../api/client";
import { Logo } from "../components/Shell";

export default function Login({ onLogin }: { onLogin: () => void }) {
  const [key, setKey] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-[440px] animate-fadein">
        <Logo className="mb-10 justify-center" />
        <div className="panel p-8">
          <h1 className="text-[26px] font-semibold tracking-[-0.02em] text-ink">Sign in to the console</h1>
          <p className="mt-2 text-[14px] text-ink-2">
            Use an admin API key. It is exchanged for a 12-hour session token; the key itself is never stored in the browser.
          </p>
          <form className="mt-6 space-y-3" onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setErr(null);
            try {
              const r = await post<{ token: string }>("/api/session", { api_key: key.trim() });
              setToken(r.token);
              onLogin();
            } catch (ex) {
              setErr(ex instanceof ApiError ? `${ex.code}: ${ex.message}` : "Gateway unreachable");
            } finally {
              setBusy(false);
            }
          }}>
            <label className="block text-[12.5px] font-medium text-ink-2" htmlFor="apikey">Admin API key</label>
            <div className="relative">
              <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
              <input id="apikey" type="password" autoComplete="off" className="input !pl-9 font-mono" placeholder="ng_live_…"
                value={key} onChange={(e) => setKey(e.target.value)} required />
            </div>
            {err && <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">{err}</div>}
            <button className="btn-primary w-full justify-center" disabled={busy || !key}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />} Continue
            </button>
          </form>
          <p className="mt-5 text-[12px] text-ink-3">
            Keys are provisioned by <span className="mono">make setup</span> into <span className="mono">var/dev_keys.json</span> (mode 0600).
          </p>
        </div>
      </div>
    </div>
  );
}
