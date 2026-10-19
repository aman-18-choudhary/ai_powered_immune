import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Shell } from "./Shell";
import { Splash } from "./Splash";
import { AccountIndex, PostSignInRedirect, RequireAccount, SignInPage, SimOnly, SimulationGate } from "./gates";
import { ThemeProvider } from "@/lib/theme";
import { MotionPrefsProvider } from "@/lib/motion/prefs";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toaster";
import { LiveDataProvider } from "@/state/live";
import { useSession } from "@/state/session";
import { useAccount } from "@/state/account";
import Landing from "@/routes/Landing";
import NotFound from "@/routes/NotFound";

const Command = lazy(() => import("@/routes/Command"));
const Analyst = lazy(() => import("@/routes/Analyst"));
const Evidence = lazy(() => import("@/routes/Evidence"));
const Citizen = lazy(() => import("@/routes/Citizen"));
const Insights = lazy(() => import("@/routes/Insights"));
const About = lazy(() => import("@/routes/About"));
const CitizenDashboard = lazy(() => import("@/routes/account/CitizenDashboard"));
const NewComplaint = lazy(() => import("@/routes/account/NewComplaint"));
const ComplaintPage = lazy(() => import("@/routes/account/ComplaintPage"));
const Profile = lazy(() => import("@/routes/account/Profile"));
const InvestigatorQueue = lazy(() => import("@/routes/account/InvestigatorQueue"));
const Network = lazy(() => import("@/routes/account/Network"));
const ComplaintMap = lazy(() => import("@/routes/account/ComplaintMap"));
const Admin = lazy(() => import("@/routes/account/Admin"));
const CheckMessage = lazy(() => import("@/routes/account/CheckMessage"));
const Models = lazy(() => import("@/routes/account/Models"));
// Clerk is its own chunk, loaded only when /auth/config enables it.
const ClerkRoot = lazy(() => import("./auth/clerk").then((m) => ({ default: m.ClerkRoot })));

const STAFF = ["INVESTIGATOR", "ADMIN"] as const;

function Authed() {
  const simToken = useSession((s) => s.token);
  return (
    <LiveDataProvider>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={simToken ? <Landing /> : <AccountIndex />} />
          <Route path="command" element={<SimOnly><Command /></SimOnly>} />
          <Route path="analyst" element={<SimOnly><Analyst /></SimOnly>} />
          <Route path="evidence" element={<SimOnly><Evidence /></SimOnly>} />
          <Route path="citizen" element={<SimOnly><Citizen /></SimOnly>} />
          <Route path="insights" element={<SimOnly><Insights /></SimOnly>} />
          <Route path="about" element={<About />} />
          <Route path="simulation" element={<SimulationGate />} />
          <Route path="sign-in" element={<SignInPage />} />
          <Route path="account" element={<AccountIndex />} />
          <Route path="me" element={<RequireAccount roles={["CITIZEN"]}><CitizenDashboard /></RequireAccount>} />
          <Route path="me/complaints/new" element={<RequireAccount roles={["CITIZEN"]}><NewComplaint /></RequireAccount>} />
          <Route path="me/complaints/:id" element={<RequireAccount roles={["CITIZEN"]}><ComplaintPage /></RequireAccount>} />
          <Route path="me/profile" element={<RequireAccount><Profile /></RequireAccount>} />
          <Route path="investigator" element={<RequireAccount roles={STAFF}><InvestigatorQueue /></RequireAccount>} />
          <Route path="investigator/complaints/:id" element={<RequireAccount roles={STAFF}><ComplaintPage /></RequireAccount>} />
          <Route path="investigator/network" element={<RequireAccount roles={STAFF}><Network /></RequireAccount>} />
          <Route path="investigator/map" element={<RequireAccount roles={STAFF}><ComplaintMap /></RequireAccount>} />
          <Route path="admin" element={<RequireAccount roles={["ADMIN"]}><Admin /></RequireAccount>} />
          <Route path="check" element={<RequireAccount><CheckMessage /></RequireAccount>} />
          <Route path="models" element={<RequireAccount><Models /></RequireAccount>} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </LiveDataProvider>
  );
}

function Boot() {
  return <div className="min-h-dvh" aria-busy="true" />;
}

/** Wraps the app in Clerk only when the server enables it (the SDK is a lazily loaded chunk). */
function MaybeClerk({ children }: { children: ReactNode }) {
  const config = useAccount((s) => s.config);
  const key = config?.clerk.enabled ? config.clerk.publishable_key : null;
  if (!key) return <>{children}</>;
  return (
    <Suspense fallback={<Boot />}>
      <ClerkRoot publishableKey={key}>{children}</ClerkRoot>
    </Suspense>
  );
}

export default function App() {
  const simToken = useSession((s) => s.token);
  const acctStatus = useAccount((s) => s.status);
  const method = useAccount((s) => s.method);
  const configStatus = useAccount((s) => s.configStatus);
  const loadConfig = useAccount((s) => s.loadConfig);
  const refresh = useAccount((s) => s.refresh);

  useEffect(() => {
    void loadConfig();
    // a local sign-in kept in this tab: re-read the effective role from the service
    if (method === "local") void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signedIn = simToken !== null || acctStatus !== "none";
  return (
    <ThemeProvider>
      <MotionPrefsProvider>
        <TooltipProvider delayDuration={150}>
          <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
            {configStatus === "idle" || configStatus === "loading" ? (
              signedIn ? <Boot /> : <Splash />
            ) : (
              <MaybeClerk>
                <PostSignInRedirect />
                {signedIn ? <Authed /> : <Splash />}
              </MaybeClerk>
            )}
          </BrowserRouter>
          <Toaster />
        </TooltipProvider>
      </MotionPrefsProvider>
    </ThemeProvider>
  );
}
