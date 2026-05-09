import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SecretInput from "./SecretInput";

describe("SecretInput", () => {
  it("shows masked placeholder when has_value is true", () => {
    render(<SecretInput label="Bot token" hasValue tokenHint="...abcd" onSave={() => {}} />);
    expect(screen.getByPlaceholderText(/abcd/)).toBeInTheDocument();
  });

  it("calls onSave with the typed value when Save is clicked", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<SecretInput label="API key" hasValue={false} onSave={onSave} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "zk_xyz" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("zk_xyz"));
  });
});
