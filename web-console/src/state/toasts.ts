import { create } from "zustand";

export interface ToastItem {
  id: number;
  title: string;
  description?: string;
  tone: "info" | "error" | "success";
}
let n = 0;
export const useToasts = create<{
  items: ToastItem[];
  push: (t: Omit<ToastItem, "id">) => void;
  dismiss: (id: number) => void;
}>((set) => ({
  items: [],
  push: (t) => set((s) => ({ items: [...s.items.slice(-3), { ...t, id: ++n }] })),
  dismiss: (id) => set((s) => ({ items: s.items.filter((x) => x.id !== id) })),
}));
export const toast = {
  info: (title: string, description?: string) => useToasts.getState().push({ title, description, tone: "info" }),
  error: (title: string, description?: string) => useToasts.getState().push({ title, description, tone: "error" }),
  success: (title: string, description?: string) => useToasts.getState().push({ title, description, tone: "success" }),
};
