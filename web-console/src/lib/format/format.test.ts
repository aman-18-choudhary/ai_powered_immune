import { describe, expect, it } from "vitest";
import {
  bankLabel, formatBucket, formatBytes, formatInr, formatLatencyMs, formatPct, formatSimClock, groupIndian, inrToPaise, median,
  shortRef, sumInr, formatPaise,
} from "./index";
import { countdown, remainingSeconds, clamp01 } from "./countdown";

describe("Indian digit grouping", () => {
  it.each([
    ["0", "0"], ["999", "999"], ["1000", "1,000"], ["12345", "12,345"], ["100000", "1,00,000"],
    ["569394", "5,69,394"], ["12345678", "1,23,45,678"], ["-1234567", "-12,34,567"],
  ])("%s -> %s", (i, o) => expect(groupIndian(i)).toBe(o));

  it("formats exact INR strings without float error", () => {
    expect(formatInr("569394.00")).toBe("₹5,69,394.00");
    expect(formatInr("16000.00")).toBe("₹16,000.00");
    expect(formatInr("0.10")).toBe("₹0.10");
    expect(formatInr("16000.00", { paise: "never" })).toBe("₹16,000");
    expect(formatInr("99999999999999999.99")).toBe("₹99,99,99,99,99,99,99,999.99");
    expect(formatInr("abc")).toBe("—");
    expect(formatInr(null)).toBe("—");
  });
  it("sums as integer paise", () => {
    expect(sumInr(["0.10", "0.20", "bad", null])).toBe(30n);
    expect(formatPaise(sumInr(["16000.00", "13332.00"]))).toBe("₹29,332.00");
    expect(inrToPaise("12.5")).toBe(1250n);
    expect(inrToPaise("1.234")).toBeNull();
  });
});

describe("labels and buckets", () => {
  it("formats amount buckets", () => {
    expect(formatBucket("10k-100k")).toBe("₹10k – ₹100k");
    expect(formatBucket("<1k")).toBe("under ₹1k");
    expect(formatBucket("1L+")).toBe("₹1L+");
    expect(formatBucket(null)).toBe("—");
    expect(formatBucket("odd bucket")).toBe("odd bucket");
  });
  it("formats latency, clocks, percentages, bytes", () => {
    expect(formatLatencyMs(7.4)).toBe("7.4 ms");
    expect(formatLatencyMs(150.2)).toBe("150 ms");
    expect(formatLatencyMs(2500)).toBe("2.50 s");
    expect(formatLatencyMs(null)).toBe("—");
    expect(formatSimClock(0)).toBe("0:00");
    expect(formatSimClock(872.9)).toBe("14:32");
    expect(formatSimClock(3725)).toBe("1:02:05");
    expect(formatPct(0.8265)).toBe("82.7%");
    expect(formatPct(null)).toBe("—");
    expect(formatBytes(49300)).toBe("48.1 KB");
  });
  it("shortens opaque refs", () => {
    expect(shortRef("txn_ceb756dbc2258882")).toBe("txn_ceb756db…");
    expect(shortRef("call_ref:799607a7e1bcb336", 6)).toBe("call_ref:799607…");
    expect(shortRef("abc")).toBe("abc");
  });
  it("labels banks and median", () => {
    expect(bankLabel("bank_a", { bank_a: "hdfc", bank_b: "axis" })).toBe("HDFC");
    expect(bankLabel("bank_icici")).toBe("ICICI");
    expect(median([3, 1, 2])).toBe(2);
    expect(median([1, 2, 3, 4])).toBe(2.5);
    expect(median([])).toBeNull();
  });
});

describe("countdown", () => {
  it("never goes negative and labels compactly", () => {
    expect(remainingSeconds(100, 130)).toBe(0);
    expect(remainingSeconds(100, 40)).toBe(60);
    expect(countdown(95)).toBe("1:35");
    expect(countdown(42.2)).toBe("43s");
    expect(countdown(-5)).toBe("0s");
    expect(clamp01(1.5)).toBe(1);
    expect(clamp01(-1)).toBe(0);
  });
});
