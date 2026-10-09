import * as T from "@radix-ui/react-toast";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { useToasts } from "@/state/toasts";

const ICON = { info: Info, error: AlertTriangle, success: CheckCircle2 } as const;
const TONE = { info: "text-flow", error: "text-danger-text", success: "text-safe" } as const;

export function Toaster() {
  const { items, dismiss } = useToasts();
  return (
    <T.Provider swipeDirection="right" duration={5000}>
      {items.map((t) => {
        const Icon = ICON[t.tone];
        return (
          <T.Root
            key={t.id}
            onOpenChange={(o) => !o && dismiss(t.id)}
            className="flex items-start gap-3 rounded-xl border border-line-strong bg-solid p-3 pr-2 shadow-2xl"
          >
            <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${TONE[t.tone]}`} aria-hidden />
            <div className="min-w-0 flex-1">
              <T.Title className="text-sm font-medium">{t.title}</T.Title>
              {t.description && <T.Description className="text-xs text-muted">{t.description}</T.Description>}
            </div>
            <T.Close aria-label="Dismiss" className="rounded p-1 text-muted hover:text-fg">
              <X className="h-3.5 w-3.5" />
            </T.Close>
          </T.Root>
        );
      })}
      <T.Viewport className="fixed bottom-4 right-4 z-[90] flex w-[min(92vw,380px)] flex-col gap-2 outline-none" />
    </T.Provider>
  );
}
