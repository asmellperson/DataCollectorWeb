import { useEffect, useState } from "react";
import { api, post, errorText } from "../services/api";
export default function TaskProgress({
  id,
  onDone,
  notify,
}: {
  id: string;
  onDone?: () => void;
  notify: (s: string) => void;
}) {
  const [task, setTask] = useState<any>(null);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const t = await api(`/tasks/${id}`);
        if (!live) return;
        setTask(t);
        if (["queued", "running"].includes(t.status))
          timer = setTimeout(tick, 1000);
        else onDone?.();
      } catch (e) {
        if (live) notify(errorText(e));
      }
    }
    void tick();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [id]);
  if (!task) return <div className="task">正在读取任务…</div>;
  return (
    <div className="task">
      <div className="actions">
        <strong>
          {
            {
              queued: "排队中",
              running: "处理中",
              completed: "已完成",
              failed: "失败",
              cancelled: "已取消",
            }[task.status as string]
          }
        </strong>
        <span>
          {task.processed} / {task.total} · 成功 {task.success} · 失败{" "}
          {task.failed}
        </span>
        {["queued", "running"].includes(task.status) && (
          <button
            type="button"
            onClick={() =>
              post(`/tasks/${id}/cancel`).catch((e) => notify(errorText(e)))
            }
          >
            取消任务
          </button>
        )}
      </div>
      <progress value={task.processed} max={task.total || 1} />
      {task.error && <small className="danger">{task.error}</small>}
      {task.result?.classes && (
        <details>
          <summary>模型原始类别（{task.result.classes.length}）</summary>
          <p>
            {task.result.classes
              .map((c: { id: number; name: string }) => `${c.id} ${c.name}`)
              .join(" · ")}
          </p>
        </details>
      )}
      {task.result?.connected && (
        <p>
          连接成功：{task.result.width} × {task.result.height}
        </p>
      )}
      {task.result?.download_url && (
        <>
          {task.result.counts && (
            <p>
              {Object.entries(task.result.counts)
                .map(
                  ([part, count]) =>
                    `${({ all: "全部图片", train: "训练集", val: "验证集", test: "测试集" } as Record<string, string>)[part]} ${count} 张`,
                )
                .join(" · ")}
            </p>
          )}
          <a href={task.result.download_url}>下载导出文件</a>
        </>
      )}
    </div>
  );
}
