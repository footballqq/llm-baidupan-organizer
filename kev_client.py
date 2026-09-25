import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, List

from config import KEV_BASE_URL, KEV_MODEL_NAME


class KevClient:
    """本地 Kev 决策模型客户端 (TypeSafe System One 架构，支持 0.5B / 4B 毫秒级推理)"""

    def __init__(self, base_url: str = KEV_BASE_URL, model: str = KEV_MODEL_NAME):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.endpoint = f"{self.base_url}/v1/systemone"

    def is_online(self, timeout: float = 1.0) -> bool:
        """快速探测本地 Kev 服务是否在线"""
        try:
            url = f"{self.base_url}/v1/models"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            return False
        return False

    def classify_choice(
        self,
        state_text: str,
        choices: Dict[str, str],
        instructions: str = "请为以下内容选择最贴切的归类子系列：",
        timeout: float = 3.0
    ) -> Optional[str]:
        """使用 Kev 0.5B/4B 进行 Choice 单选分类决策。返回置信度最高的选项键名。"""
        if not choices:
            return None

        payload = {
            "state": state_text,
            "model": self.model,
            "questions": {
                "sub_category": {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": choices
                }
            }
        }

        try:
            req = urllib.request.Request(
                self.endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                answers = data.get("answers", {})
                chosen_obj = answers.get("sub_category")
                if isinstance(chosen_obj, dict):
                    return chosen_obj.get("choice")
                return chosen_obj
        except Exception as e:
            return None
