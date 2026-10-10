import { lazy } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Shell } from "./Shell";
import { Splash } from "./Splash";
import { ThemeProvider } from "@/lib/theme";
import { MotionPrefsProvider } from "@/lib/motion/prefs";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toaster";
import { LiveDataProvider } from "@/state/live";
import { useSession } from "@/state/session";
import Landing from "@/routes/Landing";
import NotFound from "@/routes/NotFound";

const Command = lazy(() => import("@/routes/Command"));
const Analyst = lazy(() => import("@/routes/Analyst"));
const Evidence = lazy(() => import("@/routes/Evidence"));
const Citizen = lazy(() => import("@/routes/Citizen"));
const Phase2 = lazy(() => import("@/routes/Phase2"));

function Authed() {
  return (
    <LiveDataProvider>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Landing />} />
          <Route path="command" element={<Command />} />
          <Route path="analyst" element={<Analyst />} />
          <Route path="evidence" element={<Evidence />} />
          <Route path="citizen" element={<Citizen />} />
          <Route path="insights" element={<Phase2 page="insights" />} />
          <Route path="about" element={<Phase2 page="about" />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </LiveDataProvider>
  );
}

export default function App() {
  const token = useSession((s) => s.token);
  return (
    <ThemeProvider>
      <MotionPrefsProvider>
        <TooltipProvider delayDuration={150}>
          <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{token ? <Authed /> : <Splash />}</BrowserRouter>
          <Toaster />
        </TooltipProvider>
      </MotionPrefsProvider>
    </ThemeProvider>
  );
}
