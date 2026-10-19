"""通过现有千问服务按重叠窗口提出人物、轮次和时序动作草稿。"""

import base64
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.core.storage import require

PROMPT_VERSION = "security-temporal-v1"
SYSTEM_PROMPT = """你是安检视频的辅助标注员。输入是连续原视频中按时间排序的帧，
每张图片前标有原视频全局时间（秒）与帧号。只记录画面支持的事实，不判断合规性。
即使动作很快也不能标为不合规；未采到、模糊、遮挡、身份不清不等于没有发生。
必须区分安检员与每位受检人；受检人切换时建立新轮次，不能把前后人的动作凑成一轮。
没有发生安检但经过/停留的受检人也应形成候选轮次。局部人物 ID 不是姓名或跨录像身份。

输出一个 JSON 对象，仅含 persons 和 rounds，所有时间都是原视频全局秒数、左闭右开。
persons: [{id, role: inspector|subject|unknown, description,
evidence: [{frame_index, bbox: [x1,y1,x2,y2]}]}]。bbox 是 0~1 归一化人物框。
rounds: [{id, subject_id: 人物ID或null, inspector_id: 人物ID或null,
start_sec, end_sec, inspection: present|absent|uncertain, boundary_uncertain: bool,
quality: usable|uncertain|unusable, notes,
events: [{id, action, region, actor_id: 人物ID或null, start_sec, end_sec,
observation: observed|uncertain, boundary_uncertain: bool, evidence_frames: [帧号], notes}]}]。
action 填具体安检动作描述，region 可留空；部位左右以受检人的自身左右为准。
只有全程可见且明确没有安检才能填 absent；缺少证据时填 uncertain 并在 notes 写原因。
边界只能估计时 boundary_uncertain=true；横跨当前窗口末尾的轮次也必须设为 true。
不要根据静态姿势猜测未观察到的扫描动作。动作须位于本轮内，同类同部位事件不能重叠。
events 只标注安检相关动作。不要输出 compliant、合规与否或人工已审核状态。

输入包含前窗草稿。只有共享画面证据能确认是同一人物/同一轮时才复用其 ID，
并补充边界和事件；已有事件调整边界时也复用事件 ID。不得因时间接近而合并受检人。
无法确认跨窗身份时使用新 ID，quality=uncertain 并注明“跨窗口人物待确认”，留待人工拆合。
新 ID 均使用输入指定的 id_prefix。输出引用的旧人物要在 persons 中原样给出，
延续旧轮次时带上它完整的原有事件并补充。无候选人物/轮次时可返回空数组。
"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def settings():
    base = os.getenv("INSPECTION_VLM_BASE_URL", "http://127.0.0.1:18100/v1").rstrip("/")
    parsed = urlsplit(base)
    require(
        parsed.scheme in ("http", "https")
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment,
        "INSPECTION_VLM_BASE_URL 必须是不含凭据的 HTTP(S) 地址",
    )
    return {
        "base_url": base,
        "model": os.getenv("INSPECTION_VLM_MODEL", "fuda-qwen38-27b"),
        "timeout_sec": float(os.getenv("INSPECTION_VLM_TIMEOUT_SEC", "120")),
        "max_tokens": int(os.getenv("INSPECTION_VLM_MAX_TOKENS", "8192")),
    }


def parse_completion(body):
    require(
        isinstance(body, dict) and len(body.get("choices", [])) == 1,
        "千问返回候选数量无效",
    )
    choice = body["choices"][0]
    require(
        choice.get("finish_reason") == "stop",
        "千问输出未完整结束，请缩短窗口或增加输出长度",
    )
    message = choice.get("message", {})
    require(not message.get("refusal"), "千问拒绝了本次标注")
    content = message.get("content")
    require(isinstance(content, str) and content.strip(), "千问没有返回标注内容")
    content = re.sub(r"\A<think>\s*</think>\s*", "", content.strip(), count=1)
    fenced = re.fullmatch(
        r"```(?:json)?\s*\n(.*?)\n```", content, flags=re.DOTALL | re.IGNORECASE
    )
    if fenced:
        content = fenced[1]
    try:
        value = json.loads(content)
    except ValueError as exc:
        raise ValueError("千问返回了无效 JSON，原始响应已保留供检查") from exc
    require(
        isinstance(value, dict) and set(value) == {"persons", "rounds"},
        "千问标注必须包含且仅包含 persons、rounds",
    )
    return value


def request_window(frames, context, action_hint, progress):
    config = settings()
    content = [
        {
            "type": "text",
            "text": json.dumps(
                {
                    **context,
                    "action_hint": action_hint,
                    "instruction": "动作提示仅为候选清单，不代表这些动作必须出现；按图像事实标注。",
                },
                ensure_ascii=False,
            ),
        }
    ]
    for frame in frames:
        content.extend(
            [
                {
                    "type": "text",
                    "text": f"原视频帧 {frame['frame_index']}，时间 {frame['time_sec']:.6f} 秒",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64,"
                        + base64.b64encode(frame["jpeg"]).decode("ascii")
                    },
                },
            ]
        )
    payload = {
        "model": config["model"],
        "stream": False,
        "temperature": 0.1,
        "max_tokens": config["max_tokens"],
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    }
    headers = {"Content-Type": "application/json"}
    key = os.getenv("INSPECTION_VLM_API_KEY") or os.getenv("SECURITY_VLM_API_KEY")
    if key:
        headers["Authorization"] = "Bearer " + key
    progress.check()
    try:
        request = Request(
            config["base_url"] + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        with build_opener(NoRedirect()).open(
            request, timeout=config["timeout_sec"]
        ) as response:
            body = response.read(8 * 1024 * 1024 + 1)
        require(len(body) <= 8 * 1024 * 1024, "千问响应超出大小限制")
        progress.check()
        return json.loads(body)
    except HTTPError as exc:
        raise ValueError(f"千问请求失败，HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(
            f"千问服务连接或读取失败（{type(exc).__name__}），请检查服务与超时设置"
        ) from None
