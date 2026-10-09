import * as P from "@radix-ui/react-popover";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { cn } from "@/lib/utils/cn";

export const Popover = P.Root;
export const PopoverTrigger = P.Trigger;
export const PopoverClose = P.Close;

export const PopoverContent = forwardRef<ElementRef<typeof P.Content>, ComponentPropsWithoutRef<typeof P.Content>>(
  ({ className, sideOffset = 8, ...p }, ref) => (
    <P.Portal>
      <P.Content
        ref={ref}
        sideOffset={sideOffset}
        collisionPadding={12}
        className={cn(
          "z-[70] w-[min(92vw,380px)] rounded-2xl border border-line-strong bg-solid p-4 text-sm text-fg shadow-2xl outline-none",
          className,
        )}
        {...p}
      />
    </P.Portal>
  ),
);
PopoverContent.displayName = "PopoverContent";
