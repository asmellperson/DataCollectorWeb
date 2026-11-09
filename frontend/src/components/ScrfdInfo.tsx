import { useEffect, useState } from "react";
import { api } from "../services/api";

export default function ScrfdInfo() {
  const [info, setInfo] = useState<{model_path: string; available: boolean} | null>(null);
  useEffect(() => {
    let active = true;
    api("/models").then((data) => { if (active) setInfo(data.scrfd); }).catch(() => {});
    return () => { active = false; };
  }, []);
  return <p className="muted">
    SCRFD-10G-KPS · 固定类别 0 face。模型由服务器环境变量 SCRFD_MODEL_PATH 配置。
    {info && <><br /><code style={{overflowWrap: "anywhere"}}>{info.model_path}</code>
      <br />{info.available ? "模型文件已就绪，首次预标时加载。" : "当前服务器没有模型，请到实际部署服务器验证；可继续导入、手工标注和导出。"}</>}
  </p>;
}
