import { useEffect, useState } from "react";
import { api, post, errorText } from "../services/api";
import TaskProgress from "../components/TaskProgress";
import { useLiveCamera } from "../hooks/useLiveCamera";
import CameraPreview from "../components/CameraPreview";
export default function Cameras({ notify }: { notify: (s: string) => void }) {
  const [items, setItems] = useState<any[]>([]);
  const [editing, setEditing] = useState<any>(undefined);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [transport, setTransport] = useState("tcp");
  const [onvifUrl, setOnvifUrl] = useState("");
  const [onvifUsername, setOnvifUsername] = useState("");
  const [onvifPassword, setOnvifPassword] = useState("");
  const [onvifProfile, setOnvifProfile] = useState("");
  const [preview, setPreview] = useState("");
  const [task, setTask] = useState("");
  const live = useLiveCamera(preview);
  async function load() {
    setItems(await api("/cameras"));
  }
  useEffect(() => {
    void load().catch((e) => notify(errorText(e)));
  }, []);
  function open(c: any) {
    setEditing(c);
    setName(c?.name || "");
    setUrl(c?.url || "");
    setTransport(c?.transport || "tcp");
    setOnvifUrl(c?.onvif_url || "");
    setOnvifUsername(c?.onvif_username || "");
    setOnvifPassword(c?.onvif_password || "");
    setOnvifProfile(c?.onvif_profile || "");
  }
  async function save() {
    try {
      await api(`/cameras${editing ? "/" + editing.id : ""}`, {
        method: editing ? "PUT" : "POST",
        body: JSON.stringify({ name, url, transport, onvif_url: onvifUrl,
          onvif_username: onvifUsername, onvif_password: onvifPassword, onvif_profile: onvifProfile }),
      });
      setEditing(undefined);
      await load();
    } catch (e) {
      notify(errorText(e));
    }
  }
  async function remove(c: any) {
    if (!confirm(`确定删除摄像头「${c.name}」配置？`)) return;
    try {
      await api(`/cameras/${c.id}`, { method: "DELETE" });
      if (preview === c.id) setPreview("");
      await load();
    } catch (e) {
      notify(errorText(e));
    }
  }
  async function test(id: string) {
    try {
      setTask((await post(`/cameras/${id}/test`)).task_id);
    } catch (e) {
      notify(errorText(e));
    }
  }
  function safeUrl(value: string) {
    return value.replace(/\/\/[^/@]+@/, "//••••@");
  }
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">SOURCES / CAMERAS</div>
          <h1>摄像头</h1>
          <p>管理 RTSP 数据源，测试连接并查看实时画面。</p>
        </div>
        <button className="primary" onClick={() => open(null)}>
          ＋ 新增摄像头
        </button>
      </div>
      <section className="panel">
        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th>RTSP 地址</th>
              <th>传输方式</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td className="camera-url">{safeUrl(c.url)}</td>
                <td>{c.transport.toUpperCase()}</td>
                <td>
                  <div className="actions">
                    <button onClick={() => setPreview(c.id)}>实时预览</button>
                    <button onClick={() => void test(c.id)}>连接测试</button>
                    <button onClick={() => open(c)}>编辑</button>
                    <button className="danger" onClick={() => void remove(c)}>
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && (
          <div className="empty">添加一个 RTSP 摄像头，开始采集。</div>
        )}
      </section>
      {task && <TaskProgress id={task} notify={notify} />}{" "}
      {preview && (
        <section className="camera-preview panel">
          <div className="panel-title">
            {live.status.connected ? "实时画面" : live.status.error} ·{" "}
            {live.status.fps} FPS
            <button onClick={() => setPreview("")}>关闭预览</button>
          </div>
          <CameraPreview key={preview} cameraId={preview} live={live} />
        </section>
      )}
      {editing !== undefined && (
        <div className="modal-shade">
          <form
            className="modal"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <h2>{editing ? "编辑摄像头" : "新增摄像头"}</h2>
            <label>
              名称
              <input
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              RTSP 地址
              <input
                required
                type="password"
                autoComplete="off"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="rtsp://user:password@host:554/path"
              />
            </label>
            <label>
              传输方式
              <select
                value={transport}
                onChange={(e) => setTransport(e.target.value)}
              >
                <option value="tcp">TCP（推荐）</option>
                <option value="udp">UDP</option>
              </select>
            </label>
            <details className="camera-onvif-settings">
              <summary>镜头控制设置（ONVIF，可选）</summary>
              <p>默认使用 RTSP 中的摄像头地址及账号，通过 HTTP 80 端口连接 ONVIF。不同端口或独立账号可在这里填写。</p>
              <label>ONVIF 服务地址<input type="url" value={onvifUrl} onChange={e => setOnvifUrl(e.target.value)} placeholder="http://摄像头IP:端口/onvif/device_service" /></label>
              <label>ONVIF 用户名<input autoComplete="off" value={onvifUsername} onChange={e => setOnvifUsername(e.target.value)} placeholder="留空使用 RTSP 账号" /></label>
              <label>ONVIF 密码<input type="password" autoComplete="new-password" value={onvifPassword} onChange={e => setOnvifPassword(e.target.value)} /></label>
              <label>Profile Token<input value={onvifProfile} onChange={e => setOnvifProfile(e.target.value)} placeholder="留空自动匹配当前 RTSP 视频流" /></label>
            </details>
            <div className="actions">
              <button type="button" onClick={() => setEditing(undefined)}>
                取消
              </button>
              <button className="primary">保存</button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
