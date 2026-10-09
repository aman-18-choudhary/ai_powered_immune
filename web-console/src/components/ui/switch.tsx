import * as S from "@radix-ui/react-switch";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { cn } from "@/lib/utils/cn";

export const Switch = forwardRef<ElementRef<typeof S.Root>, ComponentPropsWithoutRef<typeof S.Root>>(
  ({ className, ...p }, ref) => (
    <S.Root
      ref={ref}
      className={cn(
        "relative h-5 w-9 shrink-0 rounded-full border border-line-strong bg-chip transition-colors data-[state=checked]:bg-intel-soft data-[state=checked]:border-intel",
        className,
      )}
      {...p}
    >
      <S.Thumb className="block h-3.5 w-3.5 translate-x-0.5 rounded-full bg-fg transition-transform data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-intel" />
    </S.Root>
  ),
);
Switch.displayName = "Switch";
