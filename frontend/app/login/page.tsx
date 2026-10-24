"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/hooks/useAuth";

const DEMO_ACCOUNTS = [
  ["maria@opspilot.dev", "Engineer"],
  ["sam@opspilot.dev", "Support agent"],
  ["nikos@opspilot.dev", "Admin"],
  ["viewer@opspilot.dev", "Viewer"],
] as const;

export default function LoginPage() {
  const { user, loading, login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("maria@opspilot.dev");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => { if (!loading && user) router.replace("/"); }, [loading, user, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password);
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-sky-500 text-xl font-black text-slate-950">O</div>
          <h1 className="text-2xl font-semibold">OpsPilot</h1>
          <p className="mt-1 text-sm text-slate-400">Application Support &amp; Incident Intelligence</p>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6">
          <label className="block text-sm">
            <span className="text-slate-300">E-mail</span>
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username"
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-sky-500" />
          </label>
          <label className="block text-sm">
            <span className="text-slate-300">Password</span>
            <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-sky-500" />
          </label>
          {error && <p className="text-sm text-red-300" role="alert">{error}</p>}
          <button disabled={submitting}
            className="w-full rounded-md bg-sky-500 py-2 text-sm font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-60">
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <div className="mt-4 rounded-lg border border-dashed border-slate-800 p-3 text-xs text-slate-500">
          Demo password <code className="text-slate-300">opspilot-demo</code> ·{" "}
          {DEMO_ACCOUNTS.map(([addr, role], i) => (
            <span key={addr}>
              <button type="button" onClick={() => setEmail(addr)} className="text-sky-400 hover:underline">{role}</button>
              {i < DEMO_ACCOUNTS.length - 1 ? " · " : ""}
            </span>
          ))}
        </div>
      </div>
    </main>
  );
}
