"""AI 客户端 - DeepSeek API (Anthropic 兼容格式)

通过 DeepSeek 的 Anthropic 兼容端点调用模型。
base_url: https://api.deepseek.com/anthropic

注意: DeepSeek 不支持 cache_control 和 extended thinking，
但支持 system prompt 和基本的 messages API。
"""

import json
import logging
import os
import time
from typing import Optional, Dict, List

logger = logging.getLogger("fund_ai.engine.ai_client")


class AIClient:
    """DeepSeek API 客户端 (Anthropic 兼容格式)"""

    def __init__(self, config=None):
        """
        Args:
            config: AIConfig 对象或 None（从环境变量读取）
        """
        if config is not None:
            self.base_url = config.base_url
            # config 是 AIConfig，没有 api_key 属性；api_key_env 是环境变量名
            api_key_env = getattr(config, "api_key_env", "DEEPSEEK_API_KEY")
            self.api_key = os.environ.get(api_key_env, "")
            self.model = config.model                          # Flash 模型
            self.advanced_model = getattr(config, "advanced_model", config.model)  # Pro 模型
            self.max_tokens = config.max_tokens
            self.temperature = config.temperature              # Flash 温度
            self.pro_temperature = getattr(config, "pro_temperature", 0.2)  # Pro 温度
            self.force_pro = getattr(config, "force_pro", False)  # 全量Pro开关
        else:
            self.base_url = os.environ.get(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic"
            )
            self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
            self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
            self.advanced_model = os.environ.get("DEEPSEEK_MODEL_ADVANCED", "deepseek-v4-pro")
            self.max_tokens = 4096
            self.temperature = 0.3
            self.pro_temperature = 0.2
            self.force_pro = False

        if not self.api_key:
            raise ValueError("未设置 DEEPSEEK_API_KEY 环境变量")

        # 延迟导入 anthropic 避免强制依赖
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from anthropic import Anthropic
            self._client = Anthropic(
                base_url=self.base_url,
                api_key=self.api_key,
            )
        return self._client

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """发送对话请求，返回 AI 回复文本

        Args:
            system_prompt: 系统提示词（角色定义+历史经验）
            user_message: 用户消息（当前市场数据+持仓+决策需求）
            model: 模型名称（默认使用配置的 model）
            temperature: 温度（默认使用配置的 temperature）
            max_tokens: 最大输出 token

        Returns:
            AI 回复的原始文本
        """
        model = model or (self.advanced_model if self.force_pro else self.model)
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens or self.max_tokens

        model_role = "PRO" if "pro" in model.lower() else "FLASH"
        logger.info(
            f"[{model_role}] 调用 AI: model={model}, "
            f"prompt={len(system_prompt)+len(user_message)}chars, temp={temperature}"
        )

        try:
            # DeepSeek 的 Anthropic 兼容端点: 添加 thinking 禁用参数
            # V4 模型可能默认启用思考模式，导致返回 ThinkingBlock
            extra_params = {}
            if "deepseek" in self.base_url:
                extra_params["thinking"] = {"type": "disabled"}

            response = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message}
                ],
                **extra_params,
            )

            # 遍历 content blocks，只提取 TextBlock 的文本
            # DeepSeek 可能返回 ThinkingBlock + TextBlock
            texts = []
            for block in response.content:
                if hasattr(block, "text"):
                    texts.append(block.text)
                elif getattr(block, "type", "") == "text":
                    texts.append(getattr(block, "text", ""))
                else:
                    logger.debug(f"跳过非文本块: {type(block).__name__}")

            if not texts:
                logger.warning(f"AI 返回无文本内容: {[type(b).__name__ for b in response.content]}")
                return ""

            text = "\n".join(texts)
            logger.debug(f"AI 回复长度: {len(text)}")
            return text

        except Exception as e:
            logger.error(f"AI 调用失败: {e}")
            raise

    def chat_json(
        self,
        system_prompt: str,
        user_message: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retry: int = 2,
    ) -> dict:
        """发送对话请求，返回解析后的 JSON 对象

        Args:
            retry: JSON 解析失败时的重试次数
        Returns:
            解析后的 dict
        """
        last_error = None
        for attempt in range(retry + 1):
            try:
                text = self.chat(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return self._parse_json(text)
            except json.JSONDecodeError as e:
                last_error = e
                logger.warning(f"JSON 解析失败 (attempt {attempt+1}/{retry+1}): {e}")
                if attempt < retry:
                    time.sleep(2 ** attempt)  # 指数退避

        raise ValueError(f"JSON 解析失败，已重试 {retry} 次: {last_error}")

    def chat_advanced(
        self,
        system_prompt: str,
        user_message: str,
        json_mode: bool = True,
    ) -> str | dict:
        """使用 Pro 模型进行深度分析（策略总结、经验提炼、投资建议）"""
        if json_mode:
            return self.chat_json(
                system_prompt=system_prompt,
                user_message=user_message,
                model=self.advanced_model,
                temperature=self.pro_temperature,
            )
        return self.chat(
            system_prompt=system_prompt,
            user_message=user_message,
            model=self.advanced_model,
            temperature=self.pro_temperature,
        )

    @staticmethod
    def _parse_json(text: str) -> dict:
        """从 AI 回复中提取 JSON

        处理 AI 可能会在 JSON 外包裹 markdown 代码块的情况。
        """
        text = text.strip()

        # 尝试移除 markdown 代码块
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        return json.loads(text)
