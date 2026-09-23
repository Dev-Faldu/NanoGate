import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Stat, StateView } from "./ui";

describe("ui states", () => {
  it("shows explicit unavailable state", () => {
    render(<StateView kind="unavailable" title="Router artifacts not trained yet" />);
    expect(screen.getByRole("status")).toHaveTextContent("Router artifacts not trained yet");
  });
  it("renders provenance on stats", () => {
    render(<Stat label="P95 latency" value="Unavailable" prov="Measured" />);
    expect(screen.getByText("Measured")).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
  });
});
