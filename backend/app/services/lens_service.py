"""ONVIF continuous zoom with a short device-side lease and explicit Stop.

PTZ contract: https://www.onvif.org/ver20/ptz/wsdl/
Uses WS-Security PasswordDigest and HTTP Digest without an extra dependency.
"""

import base64
import hashlib
import os
import logging
import threading
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import (
    Request, build_opener, HTTPPasswordMgrWithDefaultRealm,
    HTTPDigestAuthHandler, HTTPBasicAuthHandler, ProxyHandler,
)
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from fastapi import HTTPException
from app.core.storage import require

log = logging.getLogger(__name__)

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tt": "http://www.onvif.org/ver10/schema",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
}
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
TOKEN = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0"
SOAP_ENCODING = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"


class LensClient:
    def __init__(self, camera):
        rtsp = urlsplit(camera["url"])
        host = rtsp.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        self.device_url = camera.get("onvif_url") or f"http://{host}/onvif/device_service"
        self.username = camera.get("onvif_username") or unquote(rtsp.username or "")
        # A separate ONVIF user may intentionally have an empty password.
        self.password = camera.get("onvif_password") or (
            "" if camera.get("onvif_username") else unquote(rtsp.password or "")
        )
        self.requested_profile = camera.get("onvif_profile", "")
        self.rtsp = rtsp
        self.lock = threading.RLock()
        self.ready = False
        passwords = HTTPPasswordMgrWithDefaultRealm()
        passwords.add_password(None, self.device_url, self.username, self.password)
        self.passwords = passwords
        self.opener = build_opener(ProxyHandler({}), HTTPDigestAuthHandler(passwords),
                                   HTTPBasicAuthHandler(passwords))

    def soap(self, url, prefix, operation, content=""):
        self.passwords.add_password(None, url, self.username, self.password)
        nonce = os.urandom(20)
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()).decode()
        header = (f'<wsse:Security s:mustUnderstand="1"><wsse:UsernameToken>'
                  f'<wsse:Username>{escape(self.username)}</wsse:Username>'
                  f'<wsse:Password Type="{TOKEN}#PasswordDigest">{digest}</wsse:Password>'
                  f'<wsse:Nonce EncodingType="{SOAP_ENCODING}">{base64.b64encode(nonce).decode()}</wsse:Nonce>'
                  f'<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>') if self.username else ""
        namespaces = " ".join(f'xmlns:{key}="{value}"' for key, value in NS.items())
        payload = (f'<s:Envelope {namespaces} xmlns:wsse="{WSSE}" xmlns:wsu="{WSU}">'
                   f'<s:Header>{header}</s:Header><s:Body><{prefix}:{operation}>'
                   f'{content}</{prefix}:{operation}></s:Body></s:Envelope>').encode()
        action = f"{NS[prefix]}/{operation}"
        request = Request(url, data=payload, headers={
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"',
        })
        try:
            with self.opener.open(request, timeout=3) as response:
                data = response.read(2 * 1024 * 1024)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise HTTPException(502, "镜头控制认证失败，请检查 ONVIF 账号、密码和摄像头时间") from None
            # SOAP faults frequently use HTTP 500; do not expose raw device XML.
            raise HTTPException(502, f"摄像头拒绝 ONVIF {operation}（HTTP {exc.code}），请检查功能支持和权限") from None
        except (URLError, OSError, ValueError):
            raise HTTPException(502, "无法连接镜头控制，请启用摄像头 ONVIF 并检查服务地址、端口和网络") from None
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            raise HTTPException(502, "ONVIF 返回无效响应，请检查服务地址") from None
        require(root.find(".//s:Fault", NS) is None,
                f"摄像头拒绝 ONVIF {operation}，请检查功能支持、账号权限和摄像头时间", 502)
        return root

    def connect(self):
        if self.ready:
            return
        caps = self.soap(self.device_url, "tds", "GetCapabilities", "<tds:Category>All</tds:Category>")
        media_url = caps.findtext(".//tt:Media/tt:XAddr", namespaces=NS)
        ptz_url = caps.findtext(".//tt:PTZ/tt:XAddr", namespaces=NS)
        require(media_url and ptz_url, "摄像头没有提供 ONVIF PTZ 镜头控制，请确认已启用该功能", 422)
        profiles = self.soap(media_url, "trt", "GetProfiles").findall(".//trt:Profiles", NS)
        profiles = [p for p in profiles if p.find("tt:PTZConfiguration", NS) is not None]
        if self.requested_profile:
            profiles = [p for p in profiles if p.get("token") == self.requested_profile]
        require(profiles, "没有可用的 ONVIF PTZ 配置，请检查 Profile Token", 422)
        if len(profiles) > 1:
            matches = []
            for profile in profiles:
                token = escape(profile.get("token", ""))
                uri = self.soap(media_url, "trt", "GetStreamUri",
                                '<trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>'
                                '<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>'
                                f'<trt:ProfileToken>{token}</trt:ProfileToken>').findtext(".//tt:Uri", namespaces=NS)
                parsed = urlsplit(uri or "")
                if (parsed.path, parsed.query) == (self.rtsp.path, self.rtsp.query):
                    matches.append(profile)
            require(len(matches) == 1, "存在多个镜头配置，无法匹配当前 RTSP，请在摄像头设置中指定 ONVIF Profile Token", 422)
            profiles = matches
        profile = profiles[0]
        self.profile = profile.get("token")
        config = profile.find("tt:PTZConfiguration", NS)
        token = config.get("token", "")
        options = self.soap(ptz_url, "tptz", "GetConfigurationOptions",
                            f'<tptz:ConfigurationToken>{escape(token)}</tptz:ConfigurationToken>')
        space = options.find(".//tt:ContinuousZoomVelocitySpace", NS)
        require(space is not None, "该摄像头未提供 ONVIF 持续变焦能力；局部查看仍可使用", 422)
        self.space = space.findtext("tt:URI", namespaces=NS)
        self.minimum = float(space.findtext("tt:XRange/tt:Min", default="-1", namespaces=NS))
        self.maximum = float(space.findtext("tt:XRange/tt:Max", default="1", namespaces=NS))
        require(self.profile and self.space and self.minimum < 0 < self.maximum,
                "摄像头返回的持续变焦范围无效", 422)
        self.ptz_url = ptz_url
        self.ready = True

    def move(self, speed):
        with self.lock:
            if speed == 0 and not self.ready:
                return  # Releasing an unused control must not initiate discovery.
            self.connect()
            token = f'<tptz:ProfileToken>{escape(self.profile)}</tptz:ProfileToken>'
            if speed == 0:
                self.soap(self.ptz_url, "tptz", "Stop", token + '<tptz:PanTilt>false</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom>')
            else:
                velocity = speed * (self.maximum if speed > 0 else -self.minimum)
                self.soap(self.ptz_url, "tptz", "ContinuousMove", token +
                          f'<tptz:Velocity><tt:Zoom x="{velocity}" space={quoteattr(self.space)}/>'
                          '</tptz:Velocity><tptz:Timeout>PT1S</tptz:Timeout>')


clients_lock = threading.RLock()
clients = {}


def client(camera_id):
    from app.services import camera_service

    with clients_lock:
        if camera_id not in clients:
            clients[camera_id] = LensClient(camera_service.get(camera_id))
        return clients[camera_id]


def capabilities(camera_id):
    item = client(camera_id)
    with item.lock:
        item.connect()
    return {"continuous_zoom": True, "profile": item.profile}


def move(camera_id, speed):
    client(camera_id).move(speed)
    return {"ok": True}


def invalidate(camera_id):
    with clients_lock:
        item = clients.pop(camera_id, None)
    if item:
        try:
            item.move(0)
        except HTTPException:
            log.warning("摄像头 %s 的镜头停止请求失败，等待设备变焦超时停止", camera_id)


def shutdown():
    with clients_lock:
        ids = list(clients)
    for camera_id in ids:
        invalidate(camera_id)
