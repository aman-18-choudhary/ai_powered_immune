// Unit tests with a MOCKED fetch (no server): sign-in method choice, local code flow, investigator entry.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test-utils";
import { json, LOCAL_CONFIG, mockFetch, SESSION } from "@/lib/complaints/mockFetch.test-helper";
import { useAccount } from "@/state/account";

vi.mock("./clerk", () => ({
  ClerkSignInPanel: () => <div data-testid="clerk-panel-mock">Clerk sign-in (mocked)</div>,
  ClerkRoot: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { AccountSignIn } from "./AccountSignIn";

function reset() {
  sessionStorage.clear();
  useAccount.setState({
    config: null,
    configStatus: "idle",
    configError: null,
    method: null,
    token: null,
    status: "none",
    session: null,
    error: null,
    notice: null,
    entryDenied: null,
    pendingEntry: "citizen",
    justSignedIn: false,
  });
}

beforeEach(reset);
afterEach(() => vi.unstubAllGlobals());

describe("sign-in method from GET /auth/config", () => {
  it("local mode shows the email + code form with the dev-only explanation", async () => {
    mockFetch({ "GET /auth/config": json(200, LOCAL_CONFIG) });
    renderApp(<AccountSignIn />);
    expect(await screen.findByTestId("local-sign-in")).toBeInTheDocument();
    expect(screen.getByTestId("local-dev-note")).toHaveTextContent("Local development sign-in: the code is printed in the server terminal, not emailed.");
    expect(screen.queryByTestId("clerk-panel-mock")).not.toBeInTheDocument();
  });

  it("clerk mode with a publishable key loads the Clerk panel (lazily) and no local form", async () => {
    mockFetch({ "GET /auth/config": json(200, { ...LOCAL_CONFIG, auth_mode: "clerk", clerk: { enabled: true, publishable_key: "pk_test_abc" }, local: { ...LOCAL_CONFIG.local, enabled: false } }) });
    renderApp(<AccountSignIn />);
    expect(await screen.findByTestId("clerk-panel-mock")).toBeInTheDocument();
    expect(screen.queryByTestId("local-sign-in")).not.toBeInTheDocument();
  });

  it("clerk mode without a key says so instead of showing a form", async () => {
    mockFetch({ "GET /auth/config": json(200, { ...LOCAL_CONFIG, auth_mode: "clerk", clerk: { enabled: true, publishable_key: null }, local: { ...LOCAL_CONFIG.local, enabled: false } }) });
    renderApp(<AccountSignIn />);
    expect(await screen.findByTestId("clerk-missing-key")).toHaveTextContent("CLERK_PUBLISHABLE_KEY");
  });

  it("an unreachable service is reported as unavailable with a retry", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    renderApp(<AccountSignIn />);
    expect(await screen.findByTestId("auth-unavailable")).toHaveTextContent("Account sign-in is unavailable");
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});

describe("local code flow", () => {
  it("start -> verify -> /auth/session; role from the service; token kept in sessionStorage", async () => {
    const { calls } = mockFetch({
      "GET /auth/config": json(200, LOCAL_CONFIG),
      "POST /local-auth/start": json(202, { status: "code_sent", delivery: "server_terminal", expires_in: 600, message: "A sign-in code was issued. Local development mode: the code is printed in the server terminal instead of being emailed." }),
      "POST /local-auth/verify": json(200, { token: "header.eyJleHAiOjk5OTk5OTk5OTl9.sig", token_type: "Bearer", expires_in: 28800 }),
      "GET /auth/session": json(200, SESSION("CITIZEN")),
    });
    const user = userEvent.setup();
    renderApp(<AccountSignIn />);
    await user.type(await screen.findByTestId("local-email"), "asha@example.org");
    await user.click(screen.getByTestId("local-send"));
    expect(await screen.findByTestId("local-status")).toHaveTextContent("printed in the server terminal");
    await user.type(screen.getByTestId("local-code"), "12a3456");
    expect(screen.getByTestId("local-code")).toHaveValue("123456");
    await user.click(screen.getByTestId("local-verify"));
    await waitFor(() => expect(useAccount.getState().status).toBe("ready"));
    expect(useAccount.getState().session?.user.role).toBe("CITIZEN");
    const start = calls.find((c) => c.path === "/local-auth/start")!;
    expect(start.body).toEqual({ email: "asha@example.org" });
    expect(start.headers.Authorization).toBeUndefined();
    const sess = calls.find((c) => c.path === "/auth/session")!;
    expect(sess.headers.Authorization).toBe("Bearer header.eyJleHAiOjk5OTk5OTk5OTl9.sig");
    // the console never sends a role
    for (const c of calls) expect(JSON.stringify(c.body ?? {})).not.toMatch(/role/i);
    expect(JSON.parse(sessionStorage.getItem("ssi.account")!)).toEqual({ method: "local", token: "header.eyJleHAiOjk5OTk5OTk5OTl9.sig" });
  });

  it("shows the service's message for a wrong code and stays signed out", async () => {
    mockFetch({
      "GET /auth/config": json(200, LOCAL_CONFIG),
      "POST /local-auth/start": json(202, { status: "code_sent", delivery: "server_terminal", expires_in: 600, message: "ok" }),
      "POST /local-auth/verify": json(400, { error: "invalid_code", message: "The code is wrong or has expired. Request a new code." }),
    });
    const user = userEvent.setup();
    renderApp(<AccountSignIn />);
    await user.type(await screen.findByTestId("local-email"), "asha@example.org");
    await user.click(screen.getByTestId("local-send"));
    await user.type(await screen.findByTestId("local-code"), "000000");
    await user.click(screen.getByTestId("local-verify"));
    expect(await screen.findByTestId("sign-in-error")).toHaveTextContent("The code is wrong or has expired. Request a new code.");
    expect(useAccount.getState().status).toBe("none");
  });

  it("validates the email locally before asking for a code", async () => {
    const { calls } = mockFetch({ "GET /auth/config": json(200, LOCAL_CONFIG) });
    const user = userEvent.setup();
    renderApp(<AccountSignIn />);
    await user.type(await screen.findByTestId("local-email"), "not-an-email");
    await user.click(screen.getByTestId("local-send"));
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/local-auth/start")).toBe(false);
  });
});

describe("investigator entry", () => {
  const routes = (entry: Response) => ({
    "GET /auth/config": json(200, LOCAL_CONFIG),
    "POST /local-auth/start": json(202, { status: "code_sent", delivery: "server_terminal", expires_in: 600, message: "ok" }),
    "POST /local-auth/verify": json(200, { token: "t.e.s", token_type: "Bearer", expires_in: 28800 }),
    "GET /auth/session": json(200, SESSION("CITIZEN")),
    "POST /auth/entry": entry,
  });

  async function signInAsInvestigator() {
    const user = userEvent.setup();
    renderApp(<AccountSignIn />);
    await user.click(await screen.findByTestId("entry-investigator"));
    expect(screen.getByTestId("entry-investigator")).toHaveAttribute("aria-checked", "true");
    await user.type(screen.getByTestId("local-email"), "someone@example.org");
    await user.click(screen.getByTestId("local-send"));
    await user.type(await screen.findByTestId("local-code"), "123456");
    await user.click(screen.getByTestId("local-verify"));
    await waitFor(() => expect(useAccount.getState().status).toBe("ready"));
  }

  it("not approved: keeps the citizen session and keeps the service's friendly message", async () => {
    const msg = "Investigator access is limited to approved accounts. You can continue as a citizen.";
    const { calls } = mockFetch(routes(json(403, { error: "investigator_not_approved", message: msg })));
    await signInAsInvestigator();
    expect(calls.find((c) => c.path === "/auth/entry")?.body).toEqual({ entry: "investigator" });
    const s = useAccount.getState();
    expect(s.session?.user.role).toBe("CITIZEN");
    expect(s.entryDenied).toBe(msg);
    expect(s.method).toBe("local");
  });

  it("approved: the role in the store is the one the service returned", async () => {
    mockFetch(routes(json(200, { ...SESSION("INVESTIGATOR"), entry: "investigator" })));
    await signInAsInvestigator();
    expect(useAccount.getState().session?.user.role).toBe("INVESTIGATOR");
    expect(useAccount.getState().entryDenied).toBeNull();
  });
});

describe("sign out", () => {
  it("clears the stored token and the session", () => {
    sessionStorage.setItem("ssi.account", JSON.stringify({ method: "local", token: "x" }));
    useAccount.setState({ method: "local", token: "x", status: "ready", session: SESSION("CITIZEN") as never });
    useAccount.getState().signOut();
    expect(sessionStorage.getItem("ssi.account")).toBeNull();
    expect(useAccount.getState()).toMatchObject({ method: null, token: null, status: "none", session: null });
  });
});
