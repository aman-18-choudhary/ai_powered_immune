import * as S from "@radix-ui/react-scroll-area";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { cn } from "@/lib/utils/cn";

export const ScrollArea = forwardRef<ElementRef<typeof S.Root>, ComponentPropsWithoutRef<typeof S.Root>>(
  ({ className, children, ...p }, ref) => (
    <S.Root ref={ref} className={cn("relative overflow-hidden", className)} {...p}>
      <S.Viewport className="h-full w-full rounded-[inherit]">{children}</S.Viewport>
      <S.Scrollbar orientation="vertical" className="flex w-2 touch-none select-none p-0.5">
        <S.Thumb className="relative flex-1 rounded-full bg-line-strong" />
      </S.Scrollbar>
      <S.Corner />
    </S.Root>
  ),
);
ScrollArea.displayName = "ScrollArea";
