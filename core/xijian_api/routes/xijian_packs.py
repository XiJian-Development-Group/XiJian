"""``/v1/xijian/packs/*`` routes — resource pack management endpoints.
``/v1/xijian/packs/*`` 路由 — 资源包管理端点。

Endpoints / 端点:
* ``GET    /v1/xijian/packs``              → 列出所有已安装包
* ``GET    /v1/xijian/packs/<package_id>`` → 获取单个包详情
* ``POST   /v1/xijian/packs/install``      → 安装包（multipart file 或 JSON path）
* ``DELETE /v1/xijian/packs/<package_id>`` → 卸载包
* ``POST   /v1/xijian/packs/rescan``       → 重新扫描包目录并重建索引
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import packs as packs_stub

bp = Blueprint("xijian_packs", __name__)


@bp.get("/v1/xijian/packs")
def list_packs():
    """List all installed resource packs.

    列出所有已安装的资源包。

    Returns:
        JSON array of pack records.
    """
    packs = packs_stub.list_packs()
    return jsonify(packs)


@bp.get("/v1/xijian/packs/<package_id>")
def get_pack(package_id: str):
    """Get details for a single installed pack.

    获取单个已安装包的详情。

    Args:
        package_id: The package identifier.

    Returns:
        Pack record JSON.

    Raises:
        ApiError(404): Pack not found (code="pack_not_found").
    """
    pack = packs_stub.get_pack(package_id)
    if pack is None:
        raise ApiError(
            404,
            f"resource pack {package_id!r} not found",
            "not_found_error",
            code="pack_not_found",
        )
    return jsonify(pack)


@bp.post("/v1/xijian/packs/install")
def install_pack():
    """Install a resource pack from an archive.

    安装资源包（从归档文件）。

    Two input modes / 两种输入模式:
    1. **multipart/form-data** with field ``file`` — upload an archive directly.
    2. **application/json** with ``{"path": "..."}`` — server-side archive path.

    The archive must be a .7z or .zip file containing a valid pack structure.
    归档必须是包含有效包结构的 .7z 或 .zip 文件。

    Returns:
        201 Created with the installed pack record.

    Raises:
        ApiError(400): No file/path provided, or invalid extension.
        ApiError(413): File too large (handled by Flask's MAX_CONTENT_LENGTH).
        ApiError(400/422): Pack validation failed (PackValidationError → 400).
    """
    # multipart file upload
    if "file" in request.files:
        file = request.files["file"]
        if not file or file.filename == "":
            raise ApiError(
                400,
                "no file uploaded",
                "invalid_request_error",
                code="no_file",
                param="file",
            )
        # Validate extension
        if not file.filename.lower().endswith((".7z", ".zip")):
            raise ApiError(
                400,
                "only .7z and .zip archives are supported",
                "invalid_request_error",
                code="invalid_extension",
                param="file",
            )
        # Save to a temp file and install
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
            file.save(tmp.name)
            try:
                record = packs_stub.install_archive(tmp.name)
            except packs_stub.PackValidationError as exc:
                raise ApiError(
                    400,
                    str(exc),
                    "invalid_request_error",
                    code="pack_validation_error",
                )
            except Exception as exc:  # noqa: BLE001
                raise ApiError(
                    500,
                    f"installation failed: {exc}",
                    "server_error",
                    code="install_failed",
                )
            finally:
                try:
                    Path(tmp.name).unlink(missing_ok=True)
                except Exception:
                    pass
        return jsonify(record), 201

    # JSON with server-side path
    payload = request.get_json(silent=True) or {}
    if "path" not in payload:
        raise ApiError(
            400,
            "either multipart 'file' or JSON 'path' is required",
            "invalid_request_error",
            code="missing_file_or_path",
            param="file",
        )
    archive_path = payload["path"]
    if not isinstance(archive_path, str) or not archive_path:
        raise ApiError(
            400,
            "'path' must be a non-empty string",
            "invalid_request_error",
            code="invalid_path",
            param="path",
        )
    p = Path(archive_path)
    if not p.is_file():
        raise ApiError(
            400,
            f"archive not found at path: {archive_path}",
            "invalid_request_error",
            code="archive_not_found",
            param="path",
        )
    if p.suffix.lower() not in (".7z", ".zip"):
        raise ApiError(
            400,
            "only .7z and .zip archives are supported",
            "invalid_request_error",
            code="invalid_extension",
            param="path",
        )
    try:
        record = packs_stub.install_archive(archive_path)
    except packs_stub.PackValidationError as exc:
        raise ApiError(
            400,
            str(exc),
            "invalid_request_error",
            code="pack_validation_error",
        )
    except Exception as exc:  # noqa: BLE001
        raise ApiError(
            500,
            f"installation failed: {exc}",
            "server_error",
            code="install_failed",
        )
    return jsonify(record), 201


@bp.delete("/v1/xijian/packs/<package_id>")
def uninstall_pack(package_id: str):
    """Uninstall a resource pack.

    卸载资源包。

    Args:
        package_id: The package identifier.

    Returns:
        The removed pack record.

    Raises:
        ApiError(404): Pack not found (code="pack_not_found").
    """
    try:
        record = packs_stub.uninstall_pack(package_id)
    except packs_stub.PackValidationError as exc:
        raise ApiError(
            404,
            str(exc),
            "not_found_error",
            code="pack_not_found",
        )
    return jsonify(record)


@bp.post("/v1/xijian/packs/rescan")
def rescan_packs():
    """Re-scan the packs directory and rebuild the index.

    重新扫描包目录并重建索引。

    This will load any newly-added pack directories into runtime.
    这会将新增的包目录加载到运行时。

    Returns:
        JSON with ``installed`` count and ``errors`` list.
    """
    result = packs_stub.scan_packs()
    return jsonify({
        "installed": len(result.get("installed", [])),
        "errors": result.get("errors", []),
    })


__all__ = ["bp"]