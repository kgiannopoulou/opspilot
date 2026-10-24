"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import * as client from "@/services/api";
import type { CurrentUser } from "@/services/api";

interface AuthState {
  user: CurrentUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // A page reload loses the in-memory access token; the refresh cookie restores the session.
    client.refreshSession().then((u) => { setUser(u); setLoading(false); });
    client.setSessionExpiredHandler(() => setUser(null));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    setUser(await client.login(email, password));
  }, []);

  const logout = useCallback(async () => {
    await client.logout();
    setUser(null);
  }, []);

  const can = useCallback((permission: string) => !!user?.permissions.includes(permission), [user]);

  const value = useMemo(() => ({ user, loading, login, logout, can }), [user, loading, login, logout, can]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
