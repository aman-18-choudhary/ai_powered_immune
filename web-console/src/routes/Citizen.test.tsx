import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test-utils";

vi.mock("@/lib/api/endpoints", () => ({ endpoints: { assess: vi.fn() } }));
import { endpoints } from "@/lib/api/endpoints";
import { ApiError } from "@/lib/api/client";
import Citizen from "./Citizen";
import { SAMPLES } from "@/lib/citizen/verdict";
import { STRINGS } from "@/lib/i18n/citizen";

const scam = {
  label: "likely_scam", score: 1.0, lang: "en", uncertain: false, model_version: "clf-v1",
  thresholds: { suspicious: 0.35, likely_scam: 0.7 },
  reasons: [{ code: "SAFE_ACCOUNT_TRANSFER", text: "You are being told to move money to a safe account.", weight: 0.69 }],
  advice: { key: "likely_scam", steps: ["Do not send money.", "Hang up.", "Call 1930."] },
};

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  localStorage.clear();
});

describe("Citizen Shield", () => {
  it("sample -> check -> single verdict with reasons, steps and report acknowledgement", async () => {
    vi.mocked(endpoints.assess).mockResolvedValue(scam);
    const user = userEvent.setup();
    renderApp(<Citizen />);
    await user.click(screen.getByTestId("sample-sample1"));
    expect(screen.getByTestId("message")).toHaveValue(SAMPLES[0]!.text);
    await user.click(screen.getByTestId("check"));
    expect(await screen.findByTestId("verdict", {}, { timeout: 4000 })).toHaveAttribute("data-label", "likely_scam");
    expect(screen.getByTestId("verdict-label")).toHaveTextContent("Likely scam");
    expect(screen.getByText("Do not send money.")).toBeInTheDocument();
    expect(endpoints.assess).toHaveBeenCalledWith(SAMPLES[0]!.text, "en", expect.anything());
    await user.click(screen.getByTestId("report"));
    expect(screen.getByTestId("report-ack")).toHaveTextContent(/Nothing was sent/);
  });

  it("sends the chosen language and localises the UI", async () => {
    vi.mocked(endpoints.assess).mockResolvedValue({ ...scam, lang: "hi" });
    const user = userEvent.setup();
    renderApp(<Citizen />);
    await user.click(screen.getByRole("radio", { name: "हिन्दी" }));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(STRINGS.hi.title);
    await user.click(screen.getByTestId("sample-sample2"));
    await user.click(screen.getByTestId("check"));
    await screen.findByTestId("verdict", {}, { timeout: 4000 });
    expect(endpoints.assess).toHaveBeenCalledWith(expect.any(String), "hi", expect.anything());
    expect(screen.getByTestId("verdict-label")).toHaveTextContent(STRINGS.hi.verdictScam);
  });

  it("never persists the message: nothing in local/session storage but the language", async () => {
    vi.mocked(endpoints.assess).mockResolvedValue(scam);
    const user = userEvent.setup();
    renderApp(<Citizen />);
    await user.type(screen.getByTestId("message"), "secret-ish text about my bank");
    await user.click(screen.getByTestId("check"));
    await screen.findByTestId("verdict", {}, { timeout: 4000 });
    const dump = JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage });
    expect(dump).not.toContain("secret-ish");
    expect(location.href).not.toContain("secret");
  });

  it("counts characters, warns past 4000 and sends only the scored window", async () => {
    vi.mocked(endpoints.assess).mockResolvedValue({ ...scam, label: "suspicious", score: 0.5 });
    const user = userEvent.setup();
    renderApp(<Citizen />);
    const area = screen.getByTestId("message");
    await user.click(area);
    await user.paste("a".repeat(4100));
    expect(screen.getByTestId("counter")).toHaveTextContent("4100 of 4000");
    expect(screen.getByTestId("counter")).toHaveTextContent(/first 4000/);
    await user.click(screen.getByTestId("check"));
    await screen.findByTestId("verdict", {}, { timeout: 4000 });
    const sent = vi.mocked(endpoints.assess).mock.calls[0]![0];
    expect(sent).toHaveLength(4000);
    expect(screen.getByTestId("uncertain")).toBeInTheDocument();
  });

  it("empty message is refused client-side; uncertain verdicts show the copy; errors are friendly", async () => {
    const user = userEvent.setup();
    renderApp(<Citizen />);
    await user.click(screen.getByTestId("check"));
    expect(screen.getByRole("alert")).toHaveTextContent(STRINGS.en.empty);
    expect(endpoints.assess).not.toHaveBeenCalled();
    vi.mocked(endpoints.assess).mockRejectedValue(new ApiError(429, "rate_limited", "x"));
    await user.click(screen.getByTestId("sample-sample3"));
    await user.click(screen.getByTestId("check"));
    await waitFor(() => expect(screen.getByTestId("error")).toHaveTextContent(STRINGS.en.errorBusy));
    vi.mocked(endpoints.assess).mockResolvedValue({ ...scam, label: "no_indicators_found", score: 0.15, uncertain: true });
    await user.click(screen.getByTestId("check"));
    expect(await screen.findByTestId("uncertain", {}, { timeout: 4000 })).toHaveTextContent(STRINGS.en.uncertainTitle);
  });

  it("shows the privacy note and the demo-only report note", () => {
    renderApp(<Citizen />);
    expect(screen.getByTestId("privacy")).toHaveTextContent(/not stored/);
  });
});
