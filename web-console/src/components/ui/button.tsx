import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils/cn";

const button = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl text-sm font-medium transition-[background,border-color,box-shadow,transform,opacity] duration-150 disabled:pointer-events-none disabled:opacity-50 active:scale-[0.98] select-none",
  {
    variants: {
      variant: {
        primary:
          "bg-[linear-gradient(135deg,var(--intel-strong),var(--flow))] text-[#06090f] font-semibold shadow-[0_8px_28px_-10px_var(--intel-strong)] hover:brightness-110",
        secondary: "border border-line-strong bg-chip text-fg hover:bg-hover",
        ghost: "text-muted hover:bg-hover hover:text-fg",
        danger: "bg-danger-soft text-danger-text border border-[color-mix(in_srgb,var(--danger)_40%,transparent)] hover:brightness-125",
        safe: "bg-safe-soft text-safe border border-[color-mix(in_srgb,var(--safe)_40%,transparent)] hover:brightness-125",
        intel: "bg-intel-soft text-intel border border-[color-mix(in_srgb,var(--intel)_40%,transparent)] hover:brightness-125",
      },
      size: { sm: "h-8 px-3 text-xs", md: "h-10 px-4", lg: "h-12 px-6 text-base", icon: "h-9 w-9" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof button> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, ...p }, ref) => (
  <button ref={ref} className={cn(button({ variant, size }), className)} {...p} />
));
Button.displayName = "Button";
