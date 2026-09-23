import { describe, expect, it } from "vitest";
import { fmtBytes, fmtInt, fmtMs, fmtPct, fmtUsd, UNAVAILABLE } from "./format";

describe("formatters never invent values", () => {
  it("renders missing values as Unavailable, not zero", () => {
    for (const f of [fmtInt, fmtPct, fmtMs, fmtUsd, fmtBytes]) {
      expect(f(null)).toBe(UNAVAILABLE);
      expect(f(undefined)).toBe(UNAVAILABLE);
      expect(f(Number.NaN)).toBe(UNAVAILABLE);
    }
  });
  it("formats real values", () => {
    expect(fmtPct(0.4567)).toBe("45.7%");
    expect(fmtMs(1534)).toBe("1.53 s");
    expect(fmtBytes(2048)).toBe("2.0 KB");
    expect(fmtUsd(0.000123)).toBe("$0.000123");
  });
});
