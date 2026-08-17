"""AI 文案生成：OpenAI 兼容接口（智谱 GLM / DeepSeek / OpenAI 等均可配置）。"""
from __future__ import annotations

import json
import re

import httpx

from . import config

DEFAULT_MODEL = "glm-4-flash"

STYLE_PROMPTS = {
    "natural": "用自然口语重新组织文案，保留原意与关键信息，避免与原文雷同。",
    "viral": "改写成吸睛的爆款文案：开头 3 秒抓人的钩子、适当的悬念和情绪词、结尾引导互动，但不要过度标题党。",
    "concise": "精简改写：去掉冗余，突出一个核心看点，控制在两句话以内。",
    "story": "用讲故事的方式改写：设置场景、制造共鸣，适合口播类视频。",
}

CREDIT_TEMPLATES = {
    "bilibili": "哔哩哔哩",
    "douyin": "抖音",
    "kuaishou": "快手",
    "xiaohongshu": "小红书",
    "generic": "网络",
}


def ai_ready() -> bool:
    return bool((config.get("ai_api_key") or "").strip())


def chat(system: str, user: str, temperature: float = 0.8) -> str:
    key = (config.get("ai_api_key") or "").strip()
    if not key:
        raise RuntimeError("请先在「设置 → AI 文案」中填写 API Key")
    base = (config.get("ai_base_url") or "https://open.bigmodel.cn/api/paas/v4").strip().rstrip("/")
    model = (config.get("ai_model") or DEFAULT_MODEL).strip()
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
    }
    with httpx.Client(timeout=httpx.Timeout(30, read=90)) as c:
        r = c.post(f"{base}/chat/completions", json=body,
                   headers={"Authorization": f"Bearer {key}",
                            "Content-Type": "application/json"})
    if r.status_code != 200:
        raise RuntimeError(f"AI 接口返回 {r.status_code}：{r.text[:150]}")
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"AI 响应格式异常：{str(data)[:150]}") from e


def _extract_json(text: str) -> dict:
    """从模型输出中提取 JSON（容忍 markdown 代码块包裹）。"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("AI 未返回有效结果，请重试")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"AI 结果解析失败：{e}") from e


def rewrite_copy(video: dict, style: str = "natural", credit: bool = True) -> dict:
    """为已下载视频生成搬运文案，返回 {title, description, tags}。"""
    platform_name = CREDIT_TEMPLATES.get(video.get("platform"), "网络")
    author = video.get("author") or "原作者"
    orig_title = video.get("title") or ""
    orig_desc = (video.get("description") or "").strip() or orig_title

    style_hint = STYLE_PROMPTS.get(style, STYLE_PROMPTS["natural"])
    credit_hint = ("文案末尾用一行注明出处，格式如「转自{platform}·{author}」，自然融入，不突兀。"
                   if credit else "不要包含任何出处或原作者信息。")

    system = ("你是短视频文案专家。根据原始视频信息改写一套发布文案，"
              "输出严格的 JSON：{\"title\": \"新标题\", \"description\": \"新文案\", "
              "\"tags\": [\"标签1\", \"标签2\", ...]}。"
              "title 不超过 30 字；description 不超过 200 字、可用 emoji 与换行；"
              "tags 给 5-8 个适合发布平台的流量标签。只输出 JSON，不要多余内容。")
    user = (f"原始平台：{platform_name}\n原作者：{author}\n"
            f"原标题：{orig_title}\n原文案：{orig_desc}\n"
            f"改写要求：{style_hint}\n署名要求：{credit_hint}")

    result = _extract_json(chat(system, user))
    title = str(result.get("title") or orig_title)[:60].strip()
    desc = str(result.get("description") or "").strip()
    tags = [str(t).strip().lstrip("#") for t in (result.get("tags") or [])
            if str(t).strip()][:10]
    if credit and author and author not in desc:
        desc = f"{desc}\n\n转自{platform_name} · {author}"
    return {"title": title, "description": desc, "tags": tags}
