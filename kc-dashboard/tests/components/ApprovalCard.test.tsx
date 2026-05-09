import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ApprovalCard } from "../../src/components/ApprovalCard";

describe("ApprovalCard", () => {
  it("renders the request and fires callbacks", () => {
    const onApprove = vi.fn(); const onDeny = vi.fn();
    render(<ApprovalCard
      req={{ request_id: "r1", agent: "kc", tool: "file.delete", arguments: { share: "r" } }}
      onApprove={onApprove} onDeny={onDeny}
    />);
    expect(screen.getAllByText(/file\.delete/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByText(/approve/i));
    expect(onApprove).toHaveBeenCalledWith("r1");
  });
});
