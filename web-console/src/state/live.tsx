import { useEffect, useRef, useState, type ReactNode } from "react";
import { endpoints } from "@/lib/api/endpoints";
import { StreamClient, streamUrl } from "@/lib/ws/stream";
import { useSession } from "./session";
import { useRunStore } from "./runStore";
import type { RunStatus } from "@/types/api";
import { toast } from "./toasts";

/** Owns the app-wide WebSocket and the /demo/state poller. Mounted once when signed in. */
export function LiveDataProvider({ children }: { children: ReactNode }) {
  const token = useSession((s) => s.token);
  const apply = useRunStore((s) => s.apply);
  const setConn = useRunStore((s) => s.setConn);
  const noteResync = useRunStore((s) => s.noteResync);
  const mergeState = useRunStore((s) => s.mergeState);
  const reset = useRunStore((s) => s.reset);
  const status = useRunStore((s) => s.run.status);
  const [, bump] = useState(0);
  const wasLost = useRef(false);
  const everOpen = useRef(false);
  const statusRef = useRef<RunStatus>(status);
  statusRef.current = status;

  useEffect(() => {
    if (!token) {
      setConn("idle");
      return;
    }
    reset();
    const client = new StreamClient({
      getUrl: () => {
        const t = useSession.getState().token;
        return t ? streamUrl(t) : null;
      },
      onBatch: apply,
      onState: (s) => {
        setConn(s);
        if (s === "open") {
          if (wasLost.current) toast.success("Live stream reconnected", "Events are flowing again.");
          wasLost.current = false;
        } else if ((s === "reconnecting" || s === "closed") && everOpen.current && !wasLost.current) {
          wasLost.current = true;
          toast.error("Live stream disconnected", "Trying to reconnect automatically.");
        }
        if (s === "open") everOpen.current = true;
        if (s === "unauthorized") useSession.getState().logout("expired");
      },
      onResync: noteResync,
    });
    // Deferred so React StrictMode's mount/unmount probe never opens (and aborts) a socket.
    const h = setTimeout(() => client.start(), 0);
    return () => {
      clearTimeout(h);
      client.stop();
    };
  }, [token, apply, setConn, noteResync, reset]);

  useEffect(() => {
    if (!token) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ctl = new AbortController();
    const tick = async () => {
      try {
        const st = await endpoints.demoState(ctl.signal);
        if (!stop) mergeState(st);
      } catch {
        /* transient: next tick retries; 401 is handled by the client */
      }
      if (stop) return;
      const s = statusRef.current;
      const live = s === "running" || s === "starting" || s === "paused" || s === "seeking";
      timer = setTimeout(tick, live ? 800 : 4000);
      bump((n) => n + 1);
    };
    void tick();
    return () => {
      stop = true;
      ctl.abort();
      if (timer) clearTimeout(timer);
    };
  }, [token, mergeState]);

  return <>{children}</>;
}
