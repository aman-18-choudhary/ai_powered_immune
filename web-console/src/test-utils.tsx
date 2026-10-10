import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { MotionPrefsProvider } from "@/lib/motion/prefs";

export function renderApp(ui: ReactElement, route = "/") {
  return render(
    <MotionPrefsProvider>
      <TooltipProvider>
        <MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{ui}</MemoryRouter>
      </TooltipProvider>
    </MotionPrefsProvider>,
  );
}
