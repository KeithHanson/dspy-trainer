import { render, screen } from "@testing-library/react";
import { Button } from "./Button";

describe("Button", () => {
  it("applies primary variant class", () => {
    render(<Button variant="primary">Launch</Button>);
    expect(screen.getByRole("button", { name: "Launch" })).toHaveClass("btn-primary");
  });
});
