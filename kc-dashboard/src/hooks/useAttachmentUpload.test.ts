import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

import { useAttachmentUpload } from "./useAttachmentUpload";

vi.mock("../api/attachments", () => ({
  uploadAttachment: vi.fn(),
  deleteAttachment: vi.fn().mockResolvedValue(undefined),
}));

import { uploadAttachment, deleteAttachment } from "../api/attachments";

describe("useAttachmentUpload", () => {
  beforeEach(() => {
    (uploadAttachment as any).mockReset();
    (deleteAttachment as any).mockReset();
    (deleteAttachment as any).mockResolvedValue(undefined);
  });

  it("starts in idle state with no chips", () => {
    const { result } = renderHook(() => useAttachmentUpload(123));
    expect(result.current.chips).toEqual([]);
    expect(result.current.allReady).toBe(true);
  });

  it("transitions to ready after successful upload", async () => {
    (uploadAttachment as any).mockResolvedValue({
      attachment_id: "att_abc",
      filename: "a.txt",
      mime: "text/plain",
      size_bytes: 6,
      parse_status: "ok",
    });
    const { result } = renderHook(() => useAttachmentUpload(123));
    await act(async () => {
      await result.current.addFiles([new File(["hello"], "a.txt", { type: "text/plain" })]);
    });
    await waitFor(() => expect(result.current.chips[0].status).toBe("ready"));
    expect(result.current.chips[0].attachmentId).toBe("att_abc");
  });

  it("transitions to error on upload failure", async () => {
    (uploadAttachment as any).mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useAttachmentUpload(123));
    await act(async () => {
      await result.current.addFiles([new File(["x"], "x.txt", { type: "text/plain" })]);
    });
    await waitFor(() => expect(result.current.chips[0].status).toBe("error"));
  });

  it("remove deletes server-side when chip was ready", async () => {
    (uploadAttachment as any).mockResolvedValue({
      attachment_id: "att_abc",
      filename: "a.txt",
      mime: "text/plain",
      size_bytes: 1,
      parse_status: "ok",
    });
    const { result } = renderHook(() => useAttachmentUpload(123));
    await act(async () => {
      await result.current.addFiles([new File(["x"], "a.txt", { type: "text/plain" })]);
    });
    await waitFor(() => expect(result.current.chips.length).toBe(1));
    const localId = result.current.chips[0].localId;
    await act(async () => {
      await result.current.remove(localId);
    });
    expect(deleteAttachment).toHaveBeenCalledWith("att_abc");
    expect(result.current.chips.length).toBe(0);
  });
});
