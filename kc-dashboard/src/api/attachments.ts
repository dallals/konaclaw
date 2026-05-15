export interface AttachmentUploadResponse {
  attachment_id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  parse_status: "ok" | "error";
  parse_error?: string;
  snippet?: string;
  page_count?: number;
}

export async function uploadAttachment(
  conversationId: number,
  file: File,
  onProgress?: (loaded: number, total: number) => void,
): Promise<AttachmentUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  return await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/attachments/upload?conversation_id=${conversationId}`);
    if (onProgress) {
      xhr.upload.onprogress = (e) => onProgress(e.loaded, e.total);
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(`upload failed (${xhr.status}): ${xhr.responseText}`));
      }
    };
    xhr.onerror = () => reject(new Error("upload network error"));
    xhr.send(form);
  });
}

export async function deleteAttachment(attachmentId: string): Promise<void> {
  const resp = await fetch(`/attachments/${attachmentId}`, { method: "DELETE" });
  if (!resp.ok) {
    throw new Error(`delete failed (${resp.status})`);
  }
}
