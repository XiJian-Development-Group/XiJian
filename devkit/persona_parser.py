"""Persona document parser for extracting key features."""

import re


def extract_persona_features(persona_doc: str) -> dict:
    """Extract key personality features from persona document (C2.4).

    Parses the persona markdown and extracts:
    - name, age, gender, occupation
    - core personality traits
    - speech patterns / catchphrases
    - relationships
    - quirks/habits
    - backstory

    Returns a structured dict for use in consistency checking (C2.7).
    """
    if not persona_doc or not persona_doc.strip():
        return {}

    features = {
        "name": "",
        "age": "",
        "gender": "",
        "occupation": "",
        "core_traits": [],
        "speech_patterns": [],
        "relationships": [],
        "quirks": [],
        "backstory": "",
        "catchphrases": [],
    }

    lines = persona_doc.splitlines()
    current_section = ""
    section_content = []

    def parse_section(section: str, content_lines: list[str]) -> None:
        if not section:
            return
        section_lower = section.lower()
        content_text = "\n".join(content_lines)

        if any(k in section_lower for k in ("基本信息", "基本资料", "身份", "basic")):
            for line in content_text.splitlines():
                if "：" in line or ":" in line:
                    sep = "：" if "：" in line else ":"
                    parts = line.split(sep, 1)
                    if len(parts) == 2:
                        key, val = parts[0].strip(), parts[1].strip()
                        if any(k in key for k in ("姓名", "名字", "name")):
                            features["name"] = val
                        elif any(k in key for k in ("年龄", "age")):
                            features["age"] = val
                        elif any(k in key for k in ("性别", "gender")):
                            features["gender"] = val
                        elif any(k in key for k in ("职业", "身份", "occupation", "role")):
                            features["occupation"] = val
        elif any(k in section_lower for k in ("性格", "personality", "特质", "traits")):
            traits = re.findall(r"[-*•]\s*(.+)", content_text)
            if not traits:
                traits = [s.strip() for s in re.split(r"[。；;]", content_text) if s.strip()]
            features["core_traits"] = traits[:10]
        elif any(k in section_lower for k in ("语言", "语气", "口头禅", "speech", "catchphrase", "语言风格")):
            patterns = re.findall(r"[-*•]\s*(.+)", content_text)
            if not patterns:
                patterns = [s.strip() for s in re.split(r"[。；;]", content_text) if s.strip()]
            features["speech_patterns"] = patterns[:10]
            features["catchphrases"] = [p for p in patterns if len(p) < 30][:5]
        elif any(k in section_lower for k in ("关系", "人际", "relationship")):
            rels = re.findall(r"[-*•]\s*(.+)", content_text)
            if not rels:
                rels = [s.strip() for s in re.split(r"[。；;]", content_text) if s.strip()]
            features["relationships"] = rels[:10]
        elif any(k in section_lower for k in ("癖好", "习惯", "怪癖", "quirk", "habit")):
            quirks = re.findall(r"[-*•]\s*(.+)", content_text)
            if not quirks:
                quirks = [s.strip() for s in re.split(r"[。；;]", content_text) if s.strip()]
            features["quirks"] = quirks[:10]
        elif any(k in section_lower for k in ("背景", "成长", "经历", "backstory", "history")):
            features["backstory"] = content_text[:500]

    for line in lines:
        if line.lstrip().startswith("#"):
            parse_section(current_section, section_content)
            current_section = line.lstrip("#").strip()
            section_content = []
        else:
            section_content.append(line)

    parse_section(current_section, section_content)

    return features


def get_persona_templates() -> dict[str, str]:
    """Return built-in persona-doc markdown templates (C2.4)."""
    return {
        "通用角色": (
            "## 基本信息\n\n"
            "- 姓名：\n- 年龄：\n- 性别：\n- 职业：\n\n"
            "## 性格描述\n\n"
            "（核心性格特征、矛盾点）\n\n"
            "## 背景故事\n\n"
            "（出生、成长经历、关键转折事件）\n\n"
            "## 语言风格\n\n"
            "（说话方式、口头禅、语气特点）\n\n"
            "## 人际关系\n\n"
            "- （人物名）：（关系、看法）\n- （人物名）：（关系、看法）\n\n"
            "## 癖好与习惯\n\n"
            "（小动作、偏好、忌讳）"
        ),
        "主角型": (
            "## 基本信息\n\n"
            "- 姓名：\n- 年龄：\n- 身份／定位：\n- 标签：\n\n"
            "## 核心动机\n\n"
            "（驱动角色行动的根本原因）\n\n"
            "## 性格光谱\n\n"
            "- 外向 ← → 内向：\n- 理性 ← → 感性：\n- 善良 ← → 冷酷：\n\n"
            "## 成长弧线\n\n"
            "（初始状态 → 关键事件 → 转变后状态）\n\n"
            "## 标志性台词\n\n"
            "（2-3 句最能代表角色的台词）\n\n"
            "## 禁忌 / 弱点\n\n"
            "（角色最不想面对的事物）"
        ),
        "配角型": (
            "## 基本信息\n\n"
            "- 姓名：\n- 年龄：\n- 与主角的关系：\n\n"
            "## 性格快照\n\n"
            "（2-3 句话概括性格）\n\n"
            "## 功能定位\n\n"
            "（在剧情中扮演的角色：助攻、阻碍、情报源等）\n\n"
            "## 秘密\n\n"
            "（角色隐藏的事）\n\n"
            "## 可变性\n\n"
            "（角色能否被说服、收买、改变立场）"
        ),
    }
