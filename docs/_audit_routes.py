#!/usr/bin/env python3
"""审计 Flask 路由与 openapi.yaml 的差距。

用法：
    python docs/_audit_routes.py
输出：
    - 实际路由总数、已文档化路径数
    - 缺失的路径列表（按模块分组）
    - 多余的文档路径（文档中存在但实际路由中没有）
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# 让 core 目录可被导入
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

# 允许在没有 token 文件的情况下加载（仅用于路由自省，不启动服务）
os.environ.setdefault("XIJIAN_DEV", "1")
os.environ.setdefault("XIJIAN_DEV_TOKEN_FILE", "1")

import yaml  # type: ignore  # noqa: E402


def load_openapi_paths(yaml_path: Path) -> set[str]:
    """读取 openapi.yaml，返回 paths 字段下的所有路径键。"""
    with yaml_path.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    paths = doc.get("paths", {}) or {}
    return set(paths.keys())


def flask_rule_to_openapi(rule: str) -> str:
    """将 Flask 路由规则转换为 OpenAPI 路径。

    Flask: /xijian/worlds/<world_id>/reset/preview
    OpenAPI: /xijian/worlds/{world_id}/reset/preview
    """
    return re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", rule)


def iter_flask_routes():
    """枚举 Flask 应用注册的所有路由（不含静态路由）。"""
    from xijian_api.app import create_app

    app = create_app()
    seen: set[tuple[str, str]] = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        path = flask_rule_to_openapi(rule.rule)
        # 去掉 /v1 前缀（openapi.yaml 的 servers 已经包含 /v1）
        if path.startswith("/v1"):
            path = path[len("/v1"):]
        if not path:
            path = "/"
        for method in sorted(rule.methods or ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            key = (path, method)
            if key in seen:
                continue
            seen.add(key)
            yield path, method, rule.endpoint


def main() -> int:
    yaml_path = ROOT / "docs" / "openapi.yaml"
    if not yaml_path.is_file():
        print(f"[ERR] openapi.yaml 不存在: {yaml_path}", file=sys.stderr)
        return 1

    documented_paths = load_openapi_paths(yaml_path)
    actual_paths: set[str] = set()
    actual_methods: dict[str, set[str]] = {}
    for path, method, endpoint in iter_flask_routes():
        actual_paths.add(path)
        actual_methods.setdefault(path, set()).add(method)

    # 标准化文档路径（去掉可能的尾斜杠）
    documented_norm = {p.rstrip("/") for p in documented_paths}
    actual_norm = {p.rstrip("/") for p in actual_paths}

    missing = sorted(actual_norm - documented_norm)
    extra = sorted(documented_norm - actual_norm)

    print("=" * 70)
    print(f"实际路由数（路径）: {len(actual_norm)}")
    print(f"已文档化路径数    : {len(documented_norm)}")
    print(f"缺失路径数        : {len(missing)}")
    print(f"多余路径数        : {len(extra)}")
    print("=" * 70)

    if missing:
        print("\n--- 缺失路径（实际有，文档无） ---")
        # 按模块分组
        groups: dict[str, list[str]] = {}
        for p in missing:
            # 取第一段作为分组
            parts = p.strip("/").split("/")
            group = parts[0] if parts else "root"
            groups.setdefault(group, []).append(p)
        for g in sorted(groups.keys()):
            print(f"\n[{g}] ({len(groups[g])} 个)")
            for p in groups[g]:
                methods = sorted(actual_methods.get(p, set()) or actual_methods.get(p + "/", set()))
                print(f"  {methods}  {p}")

    if extra:
        print("\n--- 多余路径（文档有，实际无） ---")
        for p in extra:
            print(f"  {p}")

    # 写入 JSON 报告便于后续脚本消费
    import json

    report = {
        "missing": missing,
        "extra": extra,
        "actual_count": len(actual_norm),
        "documented_count": len(documented_norm),
        "methods_per_path": {p: sorted(ms) for p, ms in actual_methods.items()},
    }
    out = ROOT / "docs" / "_audit_report.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
