"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, ClipboardList, Home, ListChecks } from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: typeof Home;
  /** Match the active state for nested routes too (e.g. /games/2/pregame). */
  match: (pathname: string) => boolean;
}

// Game-scoped links go through /games/latest/*, which redirects to the
// team's most recent game.
const NAV_ITEMS: NavItem[] = [
  {
    href: "/",
    label: "팀 홈",
    icon: Home,
    match: (p) => p === "/",
  },
  {
    href: "/games/latest/pregame",
    label: "프리게임 평가",
    icon: ClipboardList,
    match: (p) => p.endsWith("/pregame"),
  },
  {
    href: "/games/latest/postgame",
    label: "포스트게임 리뷰",
    icon: ListChecks,
    match: (p) => p.endsWith("/postgame"),
  },
  {
    href: "/admin/ingestion",
    label: "파이프라인 상태",
    icon: Activity,
    match: (p) => p.startsWith("/admin"),
  },
];

export function SidebarNav() {
  const pathname = usePathname();

  return (
    <nav className="space-y-1">
      {NAV_ITEMS.map(({ href, label, icon: Icon, match }) => {
        const active = match(pathname);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "group relative flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "bg-brand-50 font-bold text-brand-700"
                : "text-zinc-600 hover:bg-ink/5 hover:text-ink"
            )}
          >
            {/* Active left accent bar in LG crimson */}
            <span
              className={cn(
                "absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-full bg-brand-600 transition-opacity",
                active ? "opacity-100" : "opacity-0"
              )}
            />
            <Icon
              size={17}
              strokeWidth={2}
              className={cn(
                "shrink-0 transition-colors",
                active
                  ? "text-brand-600"
                  : "text-zinc-400 group-hover:text-zinc-600"
              )}
            />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
