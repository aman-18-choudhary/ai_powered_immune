import type { Transition, Variants } from "framer-motion";

export const EASE_OUT = [0.22, 1, 0.36, 1] as const;
export const ROUTE_TRANSITION: Transition = { duration: 0.22, ease: EASE_OUT };
export const SPRING: Transition = { type: "spring", stiffness: 260, damping: 28, mass: 0.8 };

export const routeVariants: Variants = {
  initial: { opacity: 0, y: 10 },
  enter: { opacity: 1, y: 0, transition: ROUTE_TRANSITION },
  exit: { opacity: 0, y: -6, transition: { duration: 0.18, ease: EASE_OUT } },
};

export const listContainer: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05, delayChildren: 0.04 } },
};
export const listItem: Variants = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { duration: 0.24, ease: EASE_OUT } },
};
export const popIn: Variants = {
  hidden: { opacity: 0, scale: 0.96, y: 8 },
  show: { opacity: 1, scale: 1, y: 0, transition: SPRING },
};
