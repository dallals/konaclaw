import { useCallback, useMemo, useRef, useState } from "react";
import { uploadAttachment, deleteAttachment } from "../api/attachments";

export type ChipStatus = "uploading" | "ready" | "error";

export interface Chip {
  localId: string;
  status: ChipStatus;
  filename: string;
  sizeBytes: number;
  attachmentId?: string;
  error?: string;
}

let _seq = 0;
const nextLocalId = () => `local-${++_seq}`;

const MAX_FILES = Number(import.meta.env.VITE_KC_ATTACH_MAX_FILES || 10);

export function useAttachmentUpload(conversationId: number | null) {
  const [chips, setChips] = useState<Chip[]>([]);
  // Mirror chips in a ref so callbacks can read the latest value synchronously
  // without depending on stale closures (e.g. React 18 StrictMode double-invokes
  // updater functions, which makes relying on closure-captured `target` flaky).
  const chipsRef = useRef<Chip[]>([]);
  const setChipsBoth = useCallback((updater: (cs: Chip[]) => Chip[]) => {
    setChips((cs) => {
      const next = updater(cs);
      chipsRef.current = next;
      return next;
    });
  }, []);

  const addFiles = useCallback(
    async (files: File[]) => {
      if (conversationId == null) return;
      const available = MAX_FILES - chips.length;
      const accept = files.slice(0, available);
      for (const file of accept) {
        const localId = nextLocalId();
        setChipsBoth((cs) => [
          ...cs,
          {
            localId,
            status: "uploading",
            filename: file.name,
            sizeBytes: file.size,
          },
        ]);
        try {
          const resp = await uploadAttachment(conversationId, file);
          setChipsBoth((cs) =>
            cs.map((c) =>
              c.localId === localId
                ? {
                    ...c,
                    status: resp.parse_status === "ok" ? "ready" : "error",
                    attachmentId: resp.attachment_id,
                    error: resp.parse_error,
                  }
                : c,
            ),
          );
        } catch (e: any) {
          setChipsBoth((cs) =>
            cs.map((c) =>
              c.localId === localId
                ? { ...c, status: "error", error: e?.message ?? "upload failed" }
                : c,
            ),
          );
        }
      }
    },
    [conversationId, setChipsBoth, chips.length],
  );

  const remove = useCallback(
    async (localId: string) => {
      const target = chipsRef.current.find((c) => c.localId === localId);
      setChipsBoth((cs) => cs.filter((c) => c.localId !== localId));
      if (target?.attachmentId) {
        try {
          await deleteAttachment(target.attachmentId);
        } catch {
          // best-effort
        }
      }
    },
    [setChipsBoth],
  );

  const clear = useCallback(() => {
    chipsRef.current = [];
    setChips([]);
  }, []);

  const allReady = useMemo(
    () => chips.every((c) => c.status === "ready" || c.status === "error"),
    [chips],
  );

  const readyAttachmentIds = useMemo(
    () =>
      chips
        .filter((c) => c.status === "ready" && c.attachmentId)
        .map((c) => c.attachmentId as string),
    [chips],
  );

  return { chips, addFiles, remove, clear, allReady, readyAttachmentIds };
}
