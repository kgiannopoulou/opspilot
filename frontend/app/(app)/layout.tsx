"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { AppShell } from "@/components/AppShell";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";

export default function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => { if (!loading && !user) router.replace("/login"); }, [loading, user, router]);

  if (loading || !user) return <Spinner label="Restoring session…" />;
  return <AppShell>{children}</AppShell>;
}
