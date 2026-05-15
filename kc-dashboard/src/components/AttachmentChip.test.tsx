import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { AttachmentChip } from "./AttachmentChip";

describe("AttachmentChip", () => {
  it("renders filename and size", () => {
    render(
      <AttachmentChip
        status="ready"
        filename="report.pdf"
        sizeBytes={245000}
        onRemove={() => {}}
      />,
    );
    expect(screen.getByText(/report\.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/239 KB|240 KB|245 KB/)).toBeInTheDocument();
  });

  it("shows spinner when uploading", () => {
    render(
      <AttachmentChip
        status="uploading"
        filename="x.txt"
        sizeBytes={100}
        onRemove={() => {}}
      />,
    );
    expect(screen.getByLabelText(/uploading/i)).toBeInTheDocument();
  });

  it("shows error indicator when status is error", () => {
    render(
      <AttachmentChip
        status="error"
        filename="x.txt"
        sizeBytes={100}
        error="parse failed"
        onRemove={() => {}}
      />,
    );
    expect(screen.getByLabelText(/error/i)).toBeInTheDocument();
  });

  it("calls onRemove when remove clicked", () => {
    const onRemove = vi.fn();
    render(
      <AttachmentChip
        status="ready"
        filename="x.txt"
        sizeBytes={100}
        onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /remove/i }));
    expect(onRemove).toHaveBeenCalledOnce();
  });
});
