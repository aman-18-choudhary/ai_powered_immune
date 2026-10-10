import { Wifi, WifiOff, RefreshCw } from "lucide-react";
import { useRunStore } from "@/state/runStore";
import { cn } from "@/lib/utils/cn";

const LABEL = {
  idle: "Offline",
  connecting: "Connecting",
  open: "Live",
  reconnecting: "Reconnecting",
  closed: "Disconnected",
  unauthorized: "Signed out",
  refused: "Origin refused",
} as const;

export function ConnectionStatus() {
  const conn = useRunStore((s) => s.conn);
  const live = conn === "open";
  const Icon = live ? Wifi : conn === "connecting" || conn === "reconnecting" ? RefreshCw : WifiOff;
  return (
    <span
      role="status"
      aria-label={`Stream ${LABEL[conn]}`}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[11px] font-medium",
        live ? "border-safe/40 text-safe" : "border-warn/40 text-warn",
      )}
    >
      <Icon className={cn("h-3.5 w-3.5", (conn === "connecting" || conn === "reconnecting") && "animate-spin", live && "live-dot")} aria-hidden />
      <span>{LABEL[conn]}</span>
    </span>
  );
}
