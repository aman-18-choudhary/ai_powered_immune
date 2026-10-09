import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { cn } from "@/lib/utils/cn";

export const Dialog = D.Root;
export const DialogTrigger = D.Trigger;
export const DialogClose = D.Close;
export const DialogTitle = D.Title;
export const DialogDescription = D.Description;

export const DialogContent = forwardRef<ElementRef<typeof D.Content>, ComponentPropsWithoutRef<typeof D.Content>>(
  ({ className, children, ...p }, ref) => (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-[60] bg-black/60 backdrop-blur-sm" />
      <D.Content
        ref={ref}
        className={cn(
          "fixed left-1/2 top-1/2 z-[61] max-h-[88dvh] w-[min(94vw,640px)] -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-2xl border border-line-strong bg-solid p-6 shadow-2xl",
          className,
        )}
        {...p}
      >
        {children}
        <D.Close
          aria-label="Close"
          className="absolute right-3 top-3 rounded-lg p-1.5 text-muted hover:bg-hover hover:text-fg"
        >
          <X className="h-4 w-4" aria-hidden />
        </D.Close>
      </D.Content>
    </D.Portal>
  ),
);
DialogContent.displayName = "DialogContent";
