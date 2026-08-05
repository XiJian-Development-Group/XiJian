#!/usr/bin/env python3
"""Generate the ``paths`` section of openapi.yaml via AST introspection
of Flask route source code (refined version).
基于 Flask 路由源码 AST 自省生成 openapi.yaml 的 paths 段（精细化版）。

Strategy / 策略：
1. Parse each ``routes/*.py`` file, extracting all functions decorated
   with ``@bp.get`` / ``@bp.post`` / etc.
   解析每个 routes/*.py 文件，提取所有被 @bp.get/@bp.post/... 装饰的函数。
2. For each function, extract / 对每个函数提取：
   - Route path, HTTP method, operationId, docstring
     路由路径、HTTP 方法、operationId、docstring
   - Whether it is dev-only (function body contains ``XIJIAN_DEV`` / ``_dev_only()``)
     是否 dev-only（函数体出现 XIJIAN_DEV / _dev_only()）
   - Request body fields: scan ``body.get("xxx")`` / ``payload.get("xxx")`` calls
     请求体字段：扫描 body.get("xxx") / payload.get("xxx") 调用
   - Query params: scan ``request.args.get("xxx")`` calls
     query 参数：扫描 request.args.get("xxx") 调用
   - Response fields: scan top-level keys in ``jsonify({...})`` literals
     响应字段：扫描 jsonify({...}) 字面量的顶层 key
3. For stub calls (e.g. ``chars_stub.get(id)``), scan the stub module
   source to extract return fields.
   对 stub 调用（如 chars_stub.get(id)），扫描 stub 模块源码提取返回字段。
4. Map to tags by module, generate refined OpenAPI path descriptions.
   按模块映射到 tags，生成精细化 OpenAPI 路径描述。

Usage / 用法：
    python docs/_gen_openapi.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
os.environ.setdefault("XIJIAN_DEV", "1")
os.environ.setdefault("XIJIAN_DEV_TOKEN_FILE", "1")

import yaml  # noqa: E402

ROUTES_DIR = ROOT / "core" / "xijian_api" / "routes"
STUBS_DIR = ROOT / "core" / "xijian_api" / "stubs"
OPENAPI_PATH = ROOT / "docs" / "openapi.yaml"
AUDIT_REPORT = ROOT / "docs" / "_audit_report.json"

# ---------------------------------------------------------------------------
# Module → tag mapping / 模块 → tag 映射
# ---------------------------------------------------------------------------
MODULE_TAG: dict[str, str] = {
    "root": "root",
    "models": "models",
    "chat": "chat",
    "completions": "completions-legacy",
    "embeddings": "embeddings",
    "audio": "audio",
    "images": "images",
    "videos": "video",
    "files": "files",
    "batches": "batches",
    "fine_tuning": "fine-tuning",
    "assistants": "assistants",
    "xijian_characters": "xijian.character",
    "xijian_interactions": "xijian.interaction",
    "xijian_worlds": "xijian.world",
    "xijian_memory": "xijian.memory",
    "xijian_sessions": "xijian.session",
    "xijian_settings": "xijian.settings",
    "xijian_resources": "xijian.resource",
    "xijian_backups": "xijian.backup",
    "xijian_npcs": "xijian.npc",
    "xijian_economy": "xijian.economy",
    "xijian_events": "xijian.event",
    "xijian_overload": "xijian.overload",
    "xijian_safety": "xijian.safety",
    "xijian_scenes": "xijian.scene",
    "xijian_mcp": "xijian.mcp",
    "xijian_generation": "xijian.generation",
    "multimodal": "multimodal",
    "mcp_server": "mcp",
    "ws_routes": "websocket",
}

# Known dev-only sub-paths / 已知 dev-only 子路径
DEV_ONLY_PATHS: set[str] = {
    "/xijian/_test/emit",
    "/xijian/_test/overload/simulate",
    "/xijian/mcp/dev/crash",
    "/xijian/safety/dev/crash",
    "/xijian/characters/{character_id}/state/tick",
    "/xijian/characters/{character_id}/state/recover",
    "/xijian/characters/{character_id}/state/recovering",
    "/xijian/events/scheduler/tick",
    "/xijian/npcs/scheduling/tick",
    "/xijian/npcs/scheduling/tick/all",
}

_DECORATOR_METHODS = {
    "get": "GET", "post": "POST", "put": "PUT",
    "patch": "PATCH", "delete": "DELETE",
}


# ---------------------------------------------------------------------------
# Stub module static analysis: extract dict field names returned by functions
# Stub 模块静态分析：提取函数返回的 dict 字段名
# ---------------------------------------------------------------------------

class StubAnalyzer:
    """Scan stub module source, extracting top-level dict field names
    returned by each function.
    扫描 stub 模块源码，提取每个函数返回的 dict 顶层字段名。

    Enhancement strategy / 增强策略：
    1. Direct ``return {...}`` literals / 直接 return {...} 字面量
    2. Variable assignment ``var = {...}`` then ``return var`` / 变量赋值后 return var
    3. Call module helper functions (e.g. ``_default_record``) then ``return var``
       调用模块内 helper 函数后 return var
    4. All dict literal keys in the function body (fallback, may over-collect)
       函数体内所有 dict 字面量的 key（兜底，可能过收集）
    """

    def __init__(self) -> None:
        self._cache: dict[str, list[str]] = {}
        self._stub_modules: dict[str, ast.Module] = {}
        self._module_helpers: dict[str, dict[str, list[str]]] = {}
        self._load_stub_modules()
        self._index_helpers()

    def _load_stub_modules(self) -> None:
        if not STUBS_DIR.is_dir():
            return
        for py in STUBS_DIR.glob("*.py"):
            if py.name == "__init__.py":
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
                self._stub_modules[py.stem] = tree
            except SyntaxError:
                continue

    def _index_helpers(self) -> None:
        """Build an index of helper functions → return fields for each stub module.
        
        为每个 stub 模块建立 helper 函数 → 返回字段的索引。
        """
        for mod_name, tree in self._stub_modules.items():
            helpers: dict[str, list[str]] = {}
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                fields = self._extract_dict_fields_in_func(node)
                if fields:
                    helpers[node.name] = fields
            self._module_helpers[mod_name] = helpers

    def _extract_dict_fields_in_func(self, func: ast.FunctionDef) -> list[str]:
        """Extract all dict literal keys inside a function body (for helper indexing).
        
        提取函数体内所有 dict 字面量的 key（用于 helper 索引）。
        """
        fields: list[str] = []
        for node in ast.walk(func):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        if key.value not in fields:
                            fields.append(key.value)
        return fields

    def _extract_return_fields(self, func: ast.FunctionDef, mod_name: str) -> list[str]:
        """Extract dict field names returned by a function, tracking variable
        assignments and helper calls.
        提取函数返回的 dict 字段名，跟踪变量赋值和 helper 调用。
        """
        fields: list[str] = []
        # 1. Collect ``var = {...}`` assignments in the function.
        #    收集函数内所有 var = {...} 赋值。
        var_to_fields: dict[str, list[str]] = {}
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        keys: list[str] = []
                        for k in node.value.keys:
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                keys.append(k.value)
                        var_to_fields[target.id] = keys

        # 2. Collect ``var = helper_call(...)`` assignments.
        #    收集 var = helper_call(...) 赋值。
        helpers = self._module_helpers.get(mod_name, {})
        for node in ast.walk(func):
            if (isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)):
                helper_name = node.value.func.id
                if helper_name in helpers:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            existing = var_to_fields.get(target.id, [])
                            for f in helpers[helper_name]:
                                if f not in existing:
                                    existing.append(f)
                            var_to_fields[target.id] = existing

        # 3. Analyze ``return`` statements.
        #    分析 return 语句。
        for node in ast.walk(func):
            if not isinstance(node, ast.Return):
                continue
            val = node.value
            if val is None:
                continue
            if isinstance(val, ast.Dict):
                for k in val.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        if k.value not in fields:
                            fields.append(k.value)
            elif isinstance(val, ast.Name) and val.id in var_to_fields:
                for f in var_to_fields[val.id]:
                    if f not in fields:
                        fields.append(f)
            elif isinstance(val, ast.Call):
                # ``return helper(...)`` or ``return other_stub.func(...)``
                if isinstance(val.func, ast.Name) and val.func.id in helpers:
                    for f in helpers[val.func.id]:
                        if f not in fields:
                            fields.append(f)

        # 4. Fallback: if nothing extracted, take all dict literal keys.
        #    兜底：如果没提取到，取函数体内所有 dict 字面量的 key。
        if not fields:
            fields = self._extract_dict_fields_in_func(func)
        return fields

    def get_return_fields(self, stub_module: str, func_name: str) -> list[str]:
        """Return inferred return field names for ``stub_module.func_name``.
        
        返回 stub_module.func_name 的推断返回字段名列表。
        """
        cache_key = f"{stub_module}.{func_name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        tree = self._stub_modules.get(stub_module)
        if tree is None:
            self._cache[cache_key] = []
            return []

        # Find all functions with matching name (may be multiple; pick the one
        # with the most fields).
        # 查找所有同名函数（可能有多个，取第一个有字段的）。
        best: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                fields = self._extract_return_fields(node, stub_module)
                if len(fields) > len(best):
                    best = fields
        self._cache[cache_key] = best
        return best


# ---------------------------------------------------------------------------
# Route function AST analysis / 路由函数 AST 分析
# ---------------------------------------------------------------------------

class RouteAnalyzer:
    """Analyze a single route function, extracting body/query/response fields.
    
    分析单个路由函数，提取请求体/query/响应字段。
    """

    def __init__(self, stub_analyzer: StubAnalyzer) -> None:
        self.stub = stub_analyzer

    def analyze(self, func: ast.FunctionDef, source: str) -> dict[str, Any]:
        """Return / 返回:
        {"body_fields": [...], "query_params": [...], "response_fields": [...],
         "stub_calls": [...], "dev_only": bool}.
        """
        body_fields: list[dict[str, Any]] = []
        query_params: list[dict[str, Any]] = []
        response_fields: list[str] = []
        stub_calls: list[tuple[str, str]] = []

        for node in ast.walk(func):
            # ``body.get("xxx")`` / ``payload.get("xxx")`` /
            # ``request.args.get("xxx")`` / ``xxx_stub.func(...)``
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                receiver = self._get_receiver_name(node.func.value)
                # Check for stub calls first (any ``xxx_stub.func()``).
                # 先判断是否 stub 调用（任意 xxx_stub.func()）。
                if receiver and receiver.endswith("_stub") and node.func.attr:
                    stub_mod = receiver[:-5] if receiver.endswith("_stub") else receiver
                    stub_calls.append((stub_mod, node.func.attr))
                elif node.func.attr == "get" and node.args:
                    first_arg = node.args[0]
                    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                        field_name = first_arg.value
                        if receiver in ("body", "payload"):
                            has_default = len(node.args) > 1
                            body_fields.append({
                                "name": field_name,
                                "required": not has_default,
                                "type": self._infer_type_from_default(
                                    node.args[1] if has_default else None
                                ),
                            })
                        elif receiver == "request.args":
                            query_params.append({
                                "name": field_name,
                                "required": False,
                                "type": "string",
                            })

            # ``jsonify({...})`` literal
            # jsonify({...}) 字面量
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "jsonify"
                    and node.args):
                first = node.args[0]
                if isinstance(first, ast.Dict):
                    for key in first.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            if key.value not in response_fields:
                                response_fields.append(key.value)

            # ``paginate(...)`` call → standard pagination response fields
            # paginate(...) 调用 → 标准分页响应字段
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "paginate"):
                for f in ("object", "data", "has_more", "first_id", "last_id"):
                    if f not in response_fields:
                        response_fields.append(f)

        # Infer response fields from stub calls.
        # 从 stub 调用推断响应字段。
        for stub_mod, func_name in stub_calls:
            stub_fields = self.stub.get_return_fields(stub_mod, func_name)
            for f in stub_fields:
                if f not in response_fields:
                    response_fields.append(f)

        func_source = ast.get_source_segment(source, func, padded=False) or ""
        dev_only = ("XIJIAN_DEV" in func_source or "_dev_only()" in func_source)

        return {
            "body_fields": body_fields,
            "query_params": query_params,
            "response_fields": response_fields,
            "stub_calls": stub_calls,
            "dev_only": dev_only,
        }

    def _get_receiver_name(self, node: ast.expr) -> str:
        """Extract the receiver name from ``a.b.c`` forms (e.g. body, payload,
        request.args, xxx_stub).
        提取 a.b.c 形式的接收者名称（如 body / payload / request.args / xxx_stub）。
        """
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._get_receiver_name(node.value)
            if parent:
                return f"{parent}.{node.attr}"
            return node.attr
        return ""

    def _infer_type_from_default(self, default: ast.expr | None) -> str:
        """Infer the field type from its default value.
        
        从默认值推断字段类型。
        """
        if default is None:
            return "string"
        if isinstance(default, ast.Constant):
            if isinstance(default.value, bool):
                return "boolean"
            if isinstance(default.value, int):
                return "integer"
            if isinstance(default.value, float):
                return "number"
            if isinstance(default.value, str):
                return "string"
        if isinstance(default, ast.List) or isinstance(default, ast.Dict):
            return "object"
        return "string"


# ---------------------------------------------------------------------------
# Route extraction / 路由提取
# ---------------------------------------------------------------------------

def _extract_routes_from_module(
    module_path: Path,
    route_analyzer: RouteAnalyzer,
) -> list[dict[str, Any]]:
    """Parse a single route module and return a list of route info dicts.
    
    解析单个路由模块，返回路由信息列表。
    """
    source = module_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(module_path))
    except SyntaxError:
        return []

    module_name = module_path.stem
    tag = MODULE_TAG.get(module_name, module_name)
    routes: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            route_info = _parse_decorator(dec, node, tag, module_name, source, route_analyzer)
            if route_info:
                routes.extend(route_info)
    return routes


def _parse_decorator(
    dec: ast.expr,
    func: ast.FunctionDef,
    tag: str,
    module_name: str,
    source: str,
    route_analyzer: RouteAnalyzer,
) -> list[dict[str, Any]]:
    """Parse a single route decorator and extract route metadata.

    解析单个路由装饰器，提取路由元数据。
    """
    if not isinstance(dec, ast.Call):
        return []
    func_attr = dec.func
    if not isinstance(func_attr, ast.Attribute):
        return []

    attr_name = func_attr.attr
    path_arg: str | None = None
    methods: list[str] = []

    if attr_name in _DECORATOR_METHODS:
        methods = [_DECORATOR_METHODS[attr_name]]
    elif attr_name == "route":
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, ast.List):
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        methods.append(elt.value.upper())
    else:
        return []

    if not dec.args:
        return []
    first = dec.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        path_arg = first.value
    elif isinstance(first, ast.JoinedStr):
        path_arg = None

    if not path_arg:
        return []

    openapi_path = _flask_rule_to_openapi(path_arg)
    if openapi_path.startswith("/v1"):
        openapi_path = openapi_path[len("/v1"):]
    if not openapi_path:
        openapi_path = "/"

    docstring = ast.get_docstring(func) or ""
    summary = (docstring.splitlines() or [""])[0].strip() or _humanize_op(func.name)
    description = docstring if len(docstring.splitlines()) > 1 else None

    analysis = route_analyzer.analyze(func, source)
    is_dev_only = analysis["dev_only"]
    if openapi_path in DEV_ONLY_PATHS:
        is_dev_only = True

    out: list[dict[str, Any]] = []
    for method in methods:
        out.append({
            "path": openapi_path,
            "method": method,
            "operationId": func.name,
            "summary": summary,
            "description": description,
            "tag": tag,
            "module": module_name,
            "dev_only": is_dev_only,
            "body_fields": analysis["body_fields"],
            "query_params": analysis["query_params"],
            "response_fields": analysis["response_fields"],
        })
    return out


def _flask_rule_to_openapi(rule: str) -> str:
    return re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", rule)


def _humanize_op(name: str) -> str:
    out = name.replace("_", " ")
    if out:
        out = out[0].upper() + out[1:]
    return out


# ---------------------------------------------------------------------------
# OpenAPI path object construction / OpenAPI 路径对象构造
# ---------------------------------------------------------------------------

PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")


def _path_params(path: str) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in PATH_PARAM_RE.finditer(path):
        name = m.group(1)
        if name in seen:
            continue
        seen.add(name)
        params.append({
            "name": name, "in": "path", "required": True,
            "schema": {"type": "string"},
        })
    return params


def _build_schema_from_fields(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a JSON schema from extracted field lists.
    
    从提取的字段列表构建 JSON schema。
    """
    if not fields:
        return {"type": "object", "additionalProperties": True}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for f in fields:
        properties[f["name"]] = {"type": f["type"]}
        if f.get("required"):
            required.append(f["name"])
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }
    if required:
        schema["required"] = required
    return schema


def _build_response_schema(response_fields: list[str]) -> dict[str, Any]:
    """Build a JSON schema from response field name lists.
    
    从响应字段名列表构建 JSON schema。
    """
    if not response_fields:
        return {"type": "object", "additionalProperties": True}
    # Known boolean fields / 已知的 boolean 字段
    BOOL_FIELDS = {
        "loaded", "is_active", "ok", "deleted", "success", "authed", "alive",
        "has_more", "enabled", "disabled", "is_loaded", "active", "paused",
        "running", "stopped", "completed", "failed", "cancelled", "nsfw_allowed",
        "can_dialogue", "protection_enabled", "confirm",
    }
    # Known integer fields / 已知的 integer 字段
    INT_FIELDS = {
        "created", "updated", "expires_at", "timestamp", "ts", "created_at",
        "updated_at", "last_active_at", "count", "total", "limit", "offset",
        "page", "pages", "size", "ttl_seconds", "retry_count", "attempts",
    }
    # Known number fields / 已知的 number 字段
    NUM_FIELDS = {
        "amount", "balance", "price", "value", "quantity", "weight",
        "score", "confidence", "probability", "rate", "ratio",
    }
    # Known array fields / 已知的 array 字段
    ARRAY_FIELDS = {
        "data", "items", "entries", "events", "snapshots", "rules",
        "currencies", "wallets", "transactions", "characters", "worlds",
        "npcs", "instances", "interactions", "pois", "messages",
        "steps", "runs", "jobs", "batches", "files", "models",
        "capabilities", "tools", "methods", "params", "errors", "warnings",
        "audit_log", "log", "history", "children", "descendants", "chain",
        "tiers", "slots", "queue", "pending", "results", "choices",
        "content", "tags", "keywords", "aliases",
    }
    # Known object fields / 已知的 object 字段
    OBJECT_FIELDS = {
        "error", "xijian", "metadata", "context", "state", "config",
        "environment", "compute", "preview", "from_owner", "to_owner",
        "owner", "recipient", "sender", "data_source", "stub",
    }
    properties: dict[str, Any] = {}
    for f in response_fields:
        if f in BOOL_FIELDS:
            ftype = "boolean"
        elif f in INT_FIELDS:
            ftype = "integer"
        elif f in NUM_FIELDS:
            ftype = "number"
        elif f in ARRAY_FIELDS:
            ftype = "array"
        elif f in OBJECT_FIELDS:
            ftype = "object"
        else:
            ftype = "string"
        properties[f] = {"type": ftype}
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }


def _build_operation(route: dict[str, Any]) -> dict[str, Any]:
    """Build an OpenAPI operation object from a route info dict.

    从路由信息字典构建 OpenAPI operation 对象。
    """
    method = route["method"].lower()
    op: dict[str, Any] = {
        "tags": [route["tag"]],
        "operationId": route["operationId"],
        "summary": route["summary"],
    }
    desc = route.get("description")
    if desc:
        op["description"] = desc

    # Parameters: path + query / 参数：path + query
    params = _path_params(route["path"])
    for qp in route.get("query_params", []):
        params.append({
            "name": qp["name"], "in": "query", "required": qp.get("required", False),
            "schema": {"type": qp.get("type", "string")},
        })
    if params:
        op["parameters"] = params

    # Request body / 请求体
    body_fields = route.get("body_fields", [])
    if method in ("post", "put", "patch"):
        # Declare requestBody even if no fields extracted (optional).
        # 即使没提取到字段，也声明一个 requestBody（可选）。
        schema = _build_schema_from_fields(body_fields)
        op["requestBody"] = {
            "required": any(f.get("required") for f in body_fields),
            "content": {"application/json": {"schema": schema}},
        }

    # Response / 响应
    response_schema = _build_response_schema(route.get("response_fields", []))
    responses: dict[str, Any] = {
        "200": {
            "description": "Success / 成功",
            "content": {"application/json": {"schema": response_schema}},
        },
    }
    if params:
        responses["404"] = {"$ref": "#/components/responses/NotFound"}
    if method == "post" and not params:
        responses["201"] = {
            "description": "Created / 创建成功",
            "content": {"application/json": {"schema": response_schema}},
        }
    if route["dev_only"]:
        responses["403"] = {
            "description": "Requires XIJIAN_DEV=1 / 需要 XIJIAN_DEV=1 环境变量",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OAIError"}}},
        }
    if body_fields:
        responses["400"] = {"$ref": "#/components/responses/BadRequest"}
    op["responses"] = responses

    if route["dev_only"]:
        op["x-dev-only"] = True
    return op


def _build_paths(routes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    paths: dict[str, dict[str, Any]] = {}
    for route in routes:
        path = route["path"]
        method = route["method"].lower()
        op = _build_operation(route)
        paths.setdefault(path, {})[method] = op
    return paths


# ---------------------------------------------------------------------------
# Static WebSocket entry / WebSocket 静态条目
# ---------------------------------------------------------------------------


def _build_ws_static_entry(module_path: Path) -> list[dict[str, Any]]:
    """Add a static OpenAPI entry for the flask-sock WebSocket endpoint.

    为 flask-sock WebSocket 端点补充静态 OpenAPI 条目。

    ``@sock.route("/v1/ws")`` is not a ``bp.*`` decorator, so the AST
    walker cannot parse it — without this entry ``/v1/ws`` would be
    missing from the contract.  The handshake is a GET + ``Upgrade``
    (RFC 6455); we document it under a ``WS`` operation key and explain
    the upgrade in the description.

    ``@sock.route("/v1/ws")`` 不是 ``bp.*`` 装饰器，AST 遍历无法解析——
    没有该条目时 ``/v1/ws`` 会从契约中缺失。握手本质是 GET + ``Upgrade``
    (RFC 6455)；这里以 ``WS`` 操作键记录，并在描述中说明升级过程。
    """
    if module_path.name != "ws_routes.py":
        return []
    source = module_path.read_text(encoding="utf-8")
    if '@sock.route("/v1/ws"' not in source:
        return []
    return [
        {
            "path": "/ws",
            "method": "WS",
            "operationId": "ws_endpoint",
            "summary": "WebSocket 双向通道 /v1/ws (角色主动消息、AI 控制信号)",
            "description": (
                "WebSocket 端点，经 GET + Upgrade (RFC 6455) 握手建立。"
                "认证方式：`Sec-WebSocket-Protocol: xijian.v1, bearer.<token>`"
                "（或首帧 `{\"type\":\"auth\",\"token\":\"...\"}` 信封）。"
                "帧协议见 docs/api.md §5。仅在 werkzeug 服务器驱动下可用"
                "（waitress 不支持 WSGI socket 注入，握手会 500）。"
            ),
            "tag": "websocket",
            "module": "ws_routes",
            "dev_only": False,
            "body_fields": [],
            "query_params": [],
            "response_fields": [],
        }
    ]


# ---------------------------------------------------------------------------
# Merge and write / 合并与写入
# ---------------------------------------------------------------------------

def _load_existing_components(yaml_path: Path) -> dict[str, Any]:
    with yaml_path.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    return doc.get("components", {}) or {}


def _load_existing_head(yaml_path: Path) -> dict[str, Any]:
    with yaml_path.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    return {
        "openapi": doc.get("openapi", "3.0.3"),
        "info": doc.get("info", {}),
        "servers": doc.get("servers", []),
        "security": doc.get("security", []),
        "tags": doc.get("tags", []),
    }


def _normalize_existing_paths(paths: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Remove stale paths from existing ``paths`` that don't match actual
    routes (based on audit report).
    从现有 paths 中剔除与实际不匹配的 stale 路径（基于审计报告）。
    """
    if not AUDIT_REPORT.is_file():
        return paths, []
    with AUDIT_REPORT.open("r", encoding="utf-8") as f:
        report = json.load(f)
    extras = set(report.get("extra", []))
    cleaned: dict[str, Any] = {}
    removed: list[str] = []
    for p, v in paths.items():
        if p.rstrip("/") in extras:
            removed.append(p)
            continue
        cleaned[p] = v
    return cleaned, removed


def _op_has_detail(op: dict[str, Any]) -> bool:
    """Check if an operation contains refined fields (non-generic schema).
    
    判断一个 operation 是否包含精细化字段（非通用 schema）。
    """
    rb = op.get("requestBody")
    if isinstance(rb, dict):
        content = rb.get("content", {})
        for media in content.values():
            schema = media.get("schema", {}) if isinstance(media, dict) else {}
            if isinstance(schema, dict) and schema.get("properties"):
                return True
    # Check 200 response has properties.
    # 检查 200 响应有 properties。
    responses = op.get("responses", {})
    r200 = responses.get("200") if isinstance(responses, dict) else None
    if isinstance(r200, dict):
        content = r200.get("content", {})
        for media in content.values():
            schema = media.get("schema", {}) if isinstance(media, dict) else {}
            if isinstance(schema, dict) and schema.get("properties"):
                return True
    return False


def _merge_paths(existing: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    """Merge strategy / 合并策略：
    - Generated with refined fields (non-empty properties) → overwrite existing
      generated 中含精细化字段（properties 非空）的 operation → 覆盖 existing
    - Generated without refinement → keep existing if present, else use generated
      generated 中无精细化的 operation → 保留 existing（若存在），否则用 generated
    - Existing but not in generated → keep
      existing 中有但 generated 没有的路径/方法 → 保留

    Both iteration levels are sorted so the emitted YAML is
    byte-for-byte reproducible across runs (set iteration order is
    hash-randomised and used to differ between processes).
    两层迭代均排序，使输出的 YAML 跨运行逐字节可复现
    （set 迭代顺序受哈希随机化影响，此前会随进程不同而变化）。
    """
    merged: dict[str, Any] = {}
    all_paths = sorted(set(existing.keys()) | set(generated.keys()))
    for p in all_paths:
        ex_ops = existing.get(p, {})
        gen_ops = generated.get(p, {})
        merged_ops: dict[str, Any] = {}
        all_methods = sorted(set(ex_ops.keys()) | set(gen_ops.keys()))
        for m in all_methods:
            ex_op = ex_ops.get(m)
            gen_op = gen_ops.get(m)
            if gen_op is None:
                if ex_op is not None:
                    merged_ops[m] = ex_op
                continue
            if ex_op is None:
                merged_ops[m] = gen_op
                continue
            # Both exist: if generated is refined, use it; otherwise keep existing.
            # 两者都有：若 generated 精细化，用 generated；否则保留 existing。
            if _op_has_detail(gen_op):
                merged_ops[m] = gen_op
            else:
                merged_ops[m] = ex_op
        merged[p] = merged_ops
    return merged


def _sort_paths(paths: dict[str, Any]) -> dict[str, Any]:
    return dict(sorted(paths.items(), key=lambda kv: kv[0]))


def _ensure_tags(head: dict[str, Any], needed_tags: set[str]) -> dict[str, Any]:
    existing = {t.get("name") for t in head.get("tags", []) if isinstance(t, dict)}
    new_tags = list(head.get("tags", []))
    for t in sorted(needed_tags):
        if t not in existing:
            new_tags.append({"name": t, "description": t})
    head["tags"] = new_tags
    return head


def _yaml_representer() -> Any:
    class _IndentedDumper(yaml.SafeDumper):
        pass

    def _str_representer(dumper: yaml.Dumper, data: str) -> Any:
        if "\n" in data:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    _IndentedDumper.add_representer(str, _str_representer)
    return _IndentedDumper


def main() -> int:
    if not ROUTES_DIR.is_dir():
        print(f"[ERR] Routes directory not found / routes 目录不存在: {ROUTES_DIR}", file=sys.stderr)
        return 1

    stub_analyzer = StubAnalyzer()
    route_analyzer = RouteAnalyzer(stub_analyzer)

    # 1. Collect all routes / 收集所有路由
    all_routes: list[dict[str, Any]] = []
    for py in sorted(ROUTES_DIR.glob("*.py")):
        if py.name == "__init__.py":
            continue
        all_routes.extend(_extract_routes_from_module(py, route_analyzer))
    # flask-sock decorators (@sock.route) are invisible to the AST
    # walker; add the static /v1/ws entry when the module declares it.
    # flask-sock 装饰器 (@sock.route) 对 AST 遍历不可见；
    # 当模块声明了该端点时静态补充 /v1/ws 条目。
    all_routes.extend(_build_ws_static_entry(ROUTES_DIR / "ws_routes.py"))

    print(f"Extracted {len(all_routes)} routes from {len(list(ROUTES_DIR.glob('*.py')))} modules")
    print(f"从 {len(list(ROUTES_DIR.glob('*.py')))} 个模块提取到 {len(all_routes)} 个路由")
    # Field extraction stats / 统计字段提取情况
    with_body = sum(1 for r in all_routes if r.get("body_fields"))
    with_query = sum(1 for r in all_routes if r.get("query_params"))
    with_resp = sum(1 for r in all_routes if r.get("response_fields"))
    print(f"  With body fields / 含请求体字段: {with_body} / {len(all_routes)}")
    print(f"  With query params / 含 query 参数: {with_query} / {len(all_routes)}")
    print(f"  With response fields / 含响应字段: {with_resp} / {len(all_routes)}")

    # 2. Load existing document / 加载现有文档
    head = _load_existing_head(OPENAPI_PATH)
    components = _load_existing_components(OPENAPI_PATH)

    with OPENAPI_PATH.open("r", encoding="utf-8") as f:
        existing_doc = yaml.safe_load(f)
    existing_paths = existing_doc.get("paths", {}) or {}
    existing_paths, removed = _normalize_existing_paths(existing_paths)
    if removed:
        print(f"Removed {len(removed)} stale paths / 移除 {len(removed)} 个 stale 路径: {removed}")

    # 3. Generate new paths / 生成新 paths
    generated_paths = _build_paths(all_routes)
    needed_tags = {r["tag"] for r in all_routes}
    head = _ensure_tags(head, needed_tags)

    # 4. Merge / 合并
    merged_paths = _merge_paths(existing_paths, generated_paths)
    merged_paths = _sort_paths(merged_paths)
    print(f"Merged paths count / 合并后 paths 数: {len(merged_paths)}")

    # 5. Assemble final document / 组装最终文档
    final_doc: dict[str, Any] = {
        "openapi": head["openapi"],
        "info": head["info"],
        "servers": head["servers"],
        "security": head["security"],
        "tags": head["tags"],
        "paths": merged_paths,
        "components": components,
    }

    # 6. Write / 写入
    dumper = _yaml_representer()
    yaml_text = yaml.dump(final_doc, Dumper=dumper, allow_unicode=True, sort_keys=False, width=100)
    OPENAPI_PATH.write_text(yaml_text, encoding="utf-8")
    print(f"Written to / 已写入 {OPENAPI_PATH} ({len(yaml_text)} bytes / 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
