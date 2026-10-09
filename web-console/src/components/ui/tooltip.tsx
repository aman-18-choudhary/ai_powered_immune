import * as T from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

export const TooltipProvider = T.Provider;

export function Tooltip({ content, children, side = "top" }: { content: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  return (
    <T.Root delayDuration={150}>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          side={side}
          sideOffset={6}
          className="z-[80] max-w-xs rounded-lg border border-line-strong bg-solid px-2.5 py-1.5 text-xs text-fg shadow-xl"
        >
          {content}
          <T.Arrow className="fill-[var(--surface-solid)]" />
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
