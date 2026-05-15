import React from "react";

type Status = "uploading" | "ready" | "error";

interface Props {
  status: Status;
  filename: string;
  sizeBytes: number;
  error?: string;
  onRemove: () => void;
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

export function AttachmentChip({ status, filename, sizeBytes, error, onRemove }: Props) {
  return (
    <div className={`attachment-chip attachment-chip--${status}`} data-testid="attachment-chip">
      {status === "uploading" && <span aria-label="uploading">⟳</span>}
      {status === "ready" && <span aria-label="ready">✓</span>}
      {status === "error" && (
        <span aria-label="error" title={error || "parse error"}>⚠</span>
      )}
      <span className="attachment-chip__name">{filename}</span>
      <span className="attachment-chip__size">{formatBytes(sizeBytes)}</span>
      <button
        type="button"
        className="attachment-chip__remove"
        aria-label="remove"
        onClick={onRemove}
      >
        ✕
      </button>
    </div>
  );
}
