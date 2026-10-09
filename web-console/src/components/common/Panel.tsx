import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface PanelProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  blur?: boolean;
  bodyClassName?: string;
  as?: "section" | "div" | "article";
}

export function Panel({ title, icon, actions, blur, className, bodyClassName, children, as: As = "section", ...p }: PanelProps) {
  return (
    <As className={cn("panel flex min-h-0 flex-col", blur && "panel-blur", className)} {...p}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="flex min-w-0 items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
            {icon}
            <span className="truncate">{title}</span>
          </h2>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </As>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} aria-hidden />;
}

export function EmptyState({ icon, title, hint, children }: { icon?: ReactNode; title: string; hint?: string; children?: ReactNode }) {
  return (
    <div className="flex h-full min-h-[96px] flex-col items-center justify-center gap-1.5 px-4 py-6 text-center">
      {icon && <div className="text-subtle">{icon}</div>}
      <p className="text-sm font-medium text-muted">{title}</p>
      {hint && <p className="max-w-[28ch] text-xs text-subtle">{hint}</p>}
      {children}
    </div>
  );
}
