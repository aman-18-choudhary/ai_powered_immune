// MOCKED fetch: the complaint form's request body, Idempotency-Key, 422 mapping and evidence statuses.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { complaint, json, mockFetch, SESSION } from "@/lib/complaints/mockFetch.test-helper";
import { useAccount } from "@/state/account";
import NewComplaint from "./NewComplaint";

function Landed() {
  const loc = useLocation();
  const st = loc.state as { uploads?: { status: string; name: string }[] } | null;
  return (
    <div>
      <span data-testid="landed">{loc.pathname}</span>
      <span data-testid="landed-uploads">{(st?.uploads ?? []).map((u) => `${u.name}:${u.status}`).join(",")}</span>
    </div>
  );
}

function renderForm() {
  return renderApp(
    <Routes>
      <Route path="/me/complaints/new" element={<NewComplaint />} />
      <Route path="/me/complaints/:id" element={<Landed />} />
    </Routes>,
    "/me/complaints/new",
  );
}

async function fillRequired(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(screen.getByTestId("f-category"), "UPI_FRAUD");
  await user.type(screen.getByTestId("f-title"), "Fake refund call");
  await user.type(screen.getByTestId("f-description"), "A caller asked me to approve a UPI collect request.");
}

beforeEach(() => useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION("CITIZEN") as never }));
afterEach(() => vi.unstubAllGlobals());

describe("new complaint form", () => {
  it("mandatory-only submit sends just the three required keys with an Idempotency-Key", async () => {
    const { calls } = mockFetch({ "POST /complaints": json(201, complaint()) });
    const user = userEvent.setup();
    renderForm();
    await fillRequired(user);
    await user.click(screen.getByTestId("submit-complaint"));
    expect(await screen.findByTestId("landed")).toHaveTextContent("/me/complaints/c0ffee00-0000-4000-8000-000000000001");
    const post = calls.find((c) => c.method === "POST" && c.path === "/complaints")!;
    expect(post.body).toEqual({ category: "UPI_FRAUD", title: "Fake refund call", description: "A caller asked me to approve a UPI collect request." });
    expect(post.headers["Idempotency-Key"]).toMatch(/^web-[A-Za-z0-9._:-]{1,60}$/);
    expect(post.headers.Authorization).toBe("Bearer tok");
  });

  it("blocks submission and marks the fields when required values are missing", async () => {
    const { calls } = mockFetch({});
    const user = userEvent.setup();
    renderForm();
    await user.click(screen.getByTestId("submit-complaint"));
    expect(await screen.findByTestId("form-error")).toHaveTextContent("Please fix 3 fields");
    expect(screen.getByTestId("f-title")).toHaveAttribute("aria-invalid", "true");
    expect(calls).toHaveLength(0);
  });

  it("optional fields are labelled optional and sent when filled", async () => {
    const { calls } = mockFetch({ "POST /complaints": json(201, complaint()) });
    const user = userEvent.setup();
    renderForm();
    expect(screen.getAllByText("(optional)").length).toBeGreaterThan(15);
    await fillRequired(user);
    await user.type(screen.getByTestId("f-upi_id"), "scam@okaxis");
    await user.type(screen.getByTestId("f-ifsc"), "SBIN0001234");
    await user.type(screen.getByTestId("f-city"), "Pune");
    await user.click(screen.getByTestId("submit-complaint"));
    await screen.findByTestId("landed");
    expect(calls[0]!.body).toEqual(expect.objectContaining({ upi_id: "scam@okaxis", ifsc: "SBIN0001234", city: "Pune" }));
    expect(Object.keys(calls[0]!.body as object)).toHaveLength(6);
  });

  it("maps a 422 from the service onto the field and keeps the form", async () => {
    mockFetch({ "POST /complaints": json(422, { error: "validation_error", detail: [{ loc: ["body", "suspect_phone"], msg: "Value error, not a valid phone number", type: "value_error" }] }) });
    const user = userEvent.setup();
    renderForm();
    await fillRequired(user);
    await user.type(screen.getByTestId("f-suspect_phone"), "9876543210");
    await user.click(screen.getByTestId("submit-complaint"));
    expect(await screen.findByText("not a valid phone number")).toBeInTheDocument();
    expect(screen.getByTestId("f-suspect_phone")).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByTestId("landed")).not.toBeInTheDocument();
  });

  it("uploads evidence one by one after creating and reports each file honestly", async () => {
    let n = 0;
    mockFetch({
      "POST /complaints": json(201, complaint()),
      "POST /complaints/c0ffee00-0000-4000-8000-000000000001/evidence": () => {
        n += 1;
        return n === 1
          ? json(201, { id: "e1", complaint_id: "c", original_filename: "shot.png", mime_type: "image/png", size_bytes: 4, sha256: "ab".repeat(32), status: "stored", created_at: null })
          : json(415, { error: "file_type_mismatch", message: "The file content does not match its name." });
      },
    });
    const user = userEvent.setup();
    renderForm();
    await fillRequired(user);
    await user.upload(screen.getByTestId("f-evidence"), [new File(["png"], "shot.png", { type: "image/png" }), new File(["txt"], "fake.png", { type: "image/png" })]);
    await user.click(screen.getByTestId("submit-complaint"));
    await waitFor(() => expect(screen.getByTestId("landed-uploads")).toHaveTextContent("shot.png:stored,fake.png:rejected"));
  });
});
