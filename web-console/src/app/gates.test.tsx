// Role routing by the EFFECTIVE role from /auth/session (account store set directly; no server).
import { beforeEach, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { AccountIndex, RequireAccount } from "./gates";
import { navItems, pageTitle, isAccountPath } from "./nav";
import { homeFor, useAccount } from "@/state/account";
import { SESSION } from "@/lib/complaints/mockFetch.test-helper";

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function setRole(role: "CITIZEN" | "INVESTIGATOR" | "ADMIN" | null) {
  useAccount.setState(
    role
      ? { method: "local", token: "t", status: "ready", session: SESSION(role) as never, entryDenied: null }
      : { method: null, token: null, status: "none", session: null, configStatus: "ready", config: null },
  );
}

beforeEach(() => setRole(null));

describe("role routing", () => {
  it.each([
    ["CITIZEN", "/me"],
    ["INVESTIGATOR", "/investigator"],
    ["ADMIN", "/admin"],
  ] as const)("%s goes to %s", (role, path) => {
    setRole(role);
    expect(homeFor(role)).toBe(path);
    renderApp(
      <Routes>
        <Route path="/account" element={<AccountIndex />} />
        <Route path="*" element={<Where />} />
      </Routes>,
      "/account",
    );
    expect(screen.getByTestId("where")).toHaveTextContent(path);
  });

  it("a citizen opening the admin page sees that it is not available, not the page", () => {
    setRole("CITIZEN");
    renderApp(
      <RequireAccount roles={["ADMIN"]}>
        <p>admin content</p>
      </RequireAccount>,
    );
    expect(screen.getByTestId("role-mismatch")).toHaveTextContent("Your account role is Citizen");
    expect(screen.queryByText("admin content")).not.toBeInTheDocument();
  });

  it("an admin may open the investigator queue", () => {
    setRole("ADMIN");
    renderApp(
      <RequireAccount roles={["INVESTIGATOR", "ADMIN"]}>
        <p>queue</p>
      </RequireAccount>,
    );
    expect(screen.getByText("queue")).toBeInTheDocument();
  });

  it("signed out: the sign-in screen is shown in place of an account page", () => {
    renderApp(
      <RequireAccount>
        <p>private</p>
      </RequireAccount>,
    );
    expect(screen.getByTestId("account-sign-in")).toBeInTheDocument();
    expect(screen.queryByText("private")).not.toBeInTheDocument();
  });
});

describe("navigation by role", () => {
  const paths = (sim: boolean, role: "CITIZEN" | "INVESTIGATOR" | "ADMIN" | null, acct = role !== null) => navItems(sim, role, acct).map((n) => n.to);

  it("simulator pages stay when a demo role is active; account items follow the effective role", () => {
    expect(paths(true, null)).toEqual(["/", "/command", "/analyst", "/evidence", "/citizen", "/insights", "/about", "/sign-in"]);
    expect(paths(true, "CITIZEN")).toContain("/me/complaints/new");
    expect(paths(false, "INVESTIGATOR")).toEqual(["/check", "/investigator", "/models", "/me/profile", "/simulation"]);
    expect(paths(false, "ADMIN")).toEqual(["/check", "/investigator", "/admin", "/models", "/me/profile", "/simulation"]);
    expect(paths(false, "CITIZEN")).toEqual(["/me", "/me/complaints/new", "/check", "/models", "/me/profile", "/simulation"]);
    expect(paths(false, "CITIZEN")).not.toContain("/investigator");
  });

  it("titles and account paths", () => {
    expect(pageTitle("/")).toBe("Live Interception");
    expect(pageTitle("/me")).toBe("My complaints");
    expect(pageTitle("/me/complaints/new")).toBe("Register a complaint");
    expect(pageTitle("/me/complaints/abc")).toBe("Complaint details");
    expect(pageTitle("/investigator/complaints/abc")).toBe("Complaint review");
    expect(isAccountPath("/admin")).toBe(true);
    expect(isAccountPath("/analyst")).toBe(false);
  });
});
