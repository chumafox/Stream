from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass


REGION_DOMAINS = {
    "intl": "ap-southeast-1.maas.aliyuncs.com",
    "mainland": "cn-beijing.maas.aliyuncs.com",
}
LEGACY_ENDPOINTS = {
    "intl": "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime",
    "mainland": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
}
DEFAULT_MODEL = "qwen3.8-livetranslate-flash-realtime"
_WORKSPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")


@dataclass
class Settings:
    api_key: str = ""
    region: str = "intl"
    model: str = DEFAULT_MODEL
    workspace_id: str = ""
    ws_url_override: str = ""
    key_source: str = "none"

    def __post_init__(self) -> None:
        if self.ws_url_override:
            _ = self.ws_url
    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        region = os.getenv("DASHSCOPE_REGION", "intl").strip().lower()
        if region not in REGION_DOMAINS:
            raise ValueError("Регион должен быть intl или mainland")
        return cls(
            api_key=api_key,
        region=region,    
            model=os.getenv("DASHSCOPE_MODEL", DEFAULT_MODEL).strip(),
            workspace_id=os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip(),
            ws_url_override=os.getenv("DASHSCOPE_WS_URL", "").strip(),
            key_source="env" if api_key else "none",
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def ws_url(self) -> str:
        if self.ws_url_override:
            base = self.ws_url_override.split("?", 1)[0]
            parsed = urllib.parse.urlparse(base)
            if parsed.scheme != "wss" and not (
                parsed.scheme == "ws" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            ):
                raise ValueError("DASHSCOPE_WS_URL должен использовать wss://")
        elif self.workspace_id:
            if not _WORKSPACE_RE.fullmatch(self.workspace_id):
                raise ValueError("DASHSCOPE_WORKSPACE_ID содержит недопустимые символы")
            base = (
                f"wss://{self.workspace_id}.{REGION_DOMAINS[self.region]}"
                "/api-ws/v1/realtime"
            )
        else:
            base = LEGACY_ENDPOINTS[self.region]
        return f"{base}?model={urllib.parse.quote(self.model, safe='')}"

    def update_runtime(
        self,
        *,
        api_key: str = "",
        region: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
   
        normalized_api_key = api_key.strip()
        normalized_region: str | None = None
        if region is not None:
            normalized_region = region.strip().lower()
            if normalized_region not in REGION_DOMAINS:
                raise ValueError("Регион должен быть intl или mainland")
        normalized_workspace_id: str | None = None
        if workspace_id is not None:
            normalized_workspace_id = workspace_id.strip()
            if normalized_workspace_id and not _WORKSPACE_RE.fullmatch(normalized_workspace_id):
                raise ValueError("Некорректный Workspace ID")

        if normalized_api_key:
            self.api_key = normalized_api_key
            self.key_source = "runtime"
        if normalized_region is not None:
            self.region = normalized_region
        if normalized_workspace_id is not None:
            self.workspace_id = normalized_workspace_id
    def public_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "key_source": self.key_source,
            "region": self.region,
            "model": self.model,
            "workspace_id": self.workspace_id,
            "custom_endpoint": bool(self.ws_url_override),
            "source_language": "ru",
            "target_language": "zh",
        }
