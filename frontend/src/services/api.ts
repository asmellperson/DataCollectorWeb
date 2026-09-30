export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api" + path, {
    ...options,
    headers:
      options.body instanceof FormData
        ? options.headers
        : { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: `请求失败 (${response.status})` }));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : JSON.stringify(body.detail),
    );
  }
  return response.json();
}
export const post = <T = any>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
export function errorText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

export type UploadProgress = { loaded: number; total: number | null };

export function uploadForm<T = { task_id: string }>(
  path: string,
  form: FormData,
  onProgress: (progress: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let delivered = false;
    // 文件已传完时先接收 task_id，再取消后台任务，避免遗留无法追踪的导入。
    const abort = () => {
      if (!delivered) xhr.abort();
    };
    const cleanup = () => signal?.removeEventListener("abort", abort);
    if (signal?.aborted) {
      reject(new DOMException("上传已取消", "AbortError"));
      return;
    }
    xhr.open("POST", "/api" + path);
    xhr.upload.onload = () => {
      delivered = true;
    };
    xhr.upload.onprogress = (event) =>
      onProgress({
        loaded: event.loaded,
        total: event.lengthComputable ? event.total : null,
      });
    xhr.onload = () => {
      cleanup();
      try {
        const body = JSON.parse(xhr.responseText);
        if (xhr.status < 200 || xhr.status >= 300) {
          throw new Error(
            typeof body.detail === "string"
              ? body.detail
              : JSON.stringify(body.detail || `上传失败 (${xhr.status})`),
          );
        }
        resolve(body as T);
      } catch (error) {
        reject(
          error instanceof SyntaxError
            ? new Error(`上传响应无效 (${xhr.status})，请检查服务和网络`)
            : error,
        );
      }
    };
    xhr.onerror = () => {
      cleanup();
      reject(new Error("上传失败，请检查网络连接"));
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException("上传已取消", "AbortError"));
    };
    signal?.addEventListener("abort", abort, { once: true });
    xhr.send(form);
  });
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}
