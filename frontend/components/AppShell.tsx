"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/hooks/useAuth";

const NAV = [
  { href: "/", label: "Dashboard", icon: "▦" },
  { href: "/incidents", label: "Incidents", icon: "⚑" },
  { href: "/tickets", label: "Tickets", icon: "☰" },
  { href: "/services", label: "Services", icon: "◎" },
  { href: "/knowledge", label: "Knowledge", icon: "❏" },
  { href: "/ai-quality", label: "AI quality", icon: "◔" },
  { href: "/admin", label: "Admin", icon: "⚙", permission: "users:manage" },
  { href: "/audit", label: "Audit log", icon: "⛬", permission: "audit:read" },
] as const;

const ROLE_LABEL: Record<string, string> = {
  admin: "Admin", engineer: "Engineer", support_agent: "Support Agent", viewer: "Viewer",
};

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout, can } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-slate-800 bg-slate-950/80 md:flex">
        <Link href="/" className="flex items-center gap-2 px-5 py-5">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-sky-500 text-sm font-black text-slate-950">O</span>
          <span className="text-lg font-semibold tracking-tight">OpsPilot</span>
        </Link>
        <nav className="flex flex-1 flex-col gap-0.5 px-3">
          {NAV.map((item) => {
            if ("permission" in item && !can(item.permission)) return null;
            return (
              <Link key={item.label} href={item.href}
                className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm transition ${
                  active(item.href) ? "bg-sky-500/10 font-medium text-sky-300" : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
                }`}>
                <span className="w-4 text-center">{item.icon}</span>{item.label}
              </Link>
            );
          })}
        </nav>
        <div className="px-5 py-4 text-[11px] text-slate-600">ShopStream · production</div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-slate-800 bg-[#0b1120]/90 px-4 backdrop-blur md:px-6">
          <nav className="flex gap-3 text-sm md:hidden">
            {NAV.filter((n) => !("permission" in n && !can(n.permission))).map((n) => (
              <Link key={n.label} href={n.href} className={active(n.href) ? "text-sky-300" : "text-slate-400"}>{n.label}</Link>
            ))}
          </nav>
          <div className="hidden text-sm text-slate-500 md:block">Application Support &amp; Incident Intelligence</div>
          <div ref={menuRef} className="relative">
            <button onClick={() => setMenuOpen((o) => !o)}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-slate-800/60">
              <span className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-700 text-xs font-semibold">
                {user?.full_name.split(" ").map((p) => p[0]).join("").slice(0, 2)}
              </span>
              <span className="hidden sm:inline">{user?.full_name.split(" ")[0]}</span>
              <span className="text-slate-500">▾</span>
            </button>
            {menuOpen && user && (
              <div className="absolute right-0 mt-1 w-60 rounded-lg border border-slate-700 bg-slate-900 p-1 shadow-xl">
                <div className="px-3 py-2">
                  <div className="text-sm font-medium">{user.full_name}</div>
                  <div className="text-xs text-slate-400">{user.email}</div>
                  <div className="mt-1 inline-block rounded bg-slate-800 px-1.5 py-0.5 text-[11px] text-slate-300">
                    {ROLE_LABEL[user.role] ?? user.role}
                  </div>
                </div>
                <button onClick={async () => { await logout(); router.replace("/login"); }}
                  className="w-full rounded-md px-3 py-2 text-left text-sm text-slate-300 hover:bg-slate-800">
                  Sign out
                </button>
              </div>
            )}
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
