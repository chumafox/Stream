from __future__ import annotations

import os
import re
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

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        return cls(
            api_key=api_key,
            region=os.getenv("DASHSCOPE_REGION", "intl").strip().lower(),
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
        elif self.workspace_id:
            if not _WORKSPACE_RE.fullmatch(self.workspace_id):
                raise ValueError("DASHSCOPE_WORKSPACE_ID содержит недопустимые символы")
            base = (
                f"wss://{self.workspace_id}.{REGION_DOMAINS[self.region]}"
                "/api-ws/v1/realtime"
            )
        else:
            base = LEGACY_ENDPOINTS[self.region]
        return f"{base}?model={self.model}"

    def update_runtime(
        self,
        *,
        api_key: str = "",
        region: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        if api_key.strip():
            self.api_key = api_key.strip()
            self.key_source = "runtime"
        if region is not None:
            if region not in REGION_DOMAINS:
                raise ValueError("Регион должен быть intl или mainland")
            self.region = region
        if workspace_id is not None:
            workspace_id = workspace_id.strip()
            if workspace_id and not _WORKSPACE_RE.fullmatch(workspace_id):
                raise ValueError("Некорректный Workspace ID")
            self.workspace_id = workspace_id

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
