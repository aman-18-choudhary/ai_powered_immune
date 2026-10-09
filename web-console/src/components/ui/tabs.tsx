import * as T from "@radix-ui/react-tabs";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { cn } from "@/lib/utils/cn";

export const Tabs = T.Root;
export const TabsList = forwardRef<ElementRef<typeof T.List>, ComponentPropsWithoutRef<typeof T.List>>(
  ({ className, ...p }, ref) => (
    <T.List ref={ref} className={cn("inline-flex items-center gap-1 rounded-xl border border-line bg-chip p-1", className)} {...p} />
  ),
);
TabsList.displayName = "TabsList";
export const TabsTrigger = forwardRef<ElementRef<typeof T.Trigger>, ComponentPropsWithoutRef<typeof T.Trigger>>(
  ({ className, ...p }, ref) => (
    <T.Trigger
      ref={ref}
      className={cn(
        "rounded-lg px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:text-fg data-[state=active]:bg-raised data-[state=active]:text-fg data-[state=active]:shadow",
        className,
      )}
      {...p}
    />
  ),
);
TabsTrigger.displayName = "TabsTrigger";
export const TabsContent = T.Content;
