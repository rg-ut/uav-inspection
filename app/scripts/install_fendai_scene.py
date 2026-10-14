"""Install the optional published scene, verifying size and SHA-256 before use."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request

APP_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = APP_ROOT / "docs" / "fendai-scene.json"


def data_root(configured: str | None = None) -> Path:
    selected = configured or os.environ.get("UAV_DATA_DIR")
    if selected:
        return Path(selected).expanduser().resolve()
    marker = APP_ROOT / "deployment-layout.json"
    if marker.is_file():
        layout = json.loads(marker.read_text(encoding="utf-8"))
        return (APP_ROOT / layout["data_directory"]).resolve()
    return APP_ROOT


def matches(path: Path, manifest: dict) -> bool:
    if not path.is_file() or path.stat().st_size != manifest["bytes"]:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == manifest["sha256"]


def install(root: Path, manifest: dict, source: Path | None = None, overwrite: bool = False) -> Path:
    target = root / "uav-inspection-backend" / "fendaidata" / "scenes" / "fendai-flower.ksplat"
    if target.exists():
        if matches(target, manifest):
            return target
        if not overwrite:
            raise ValueError("已有同名文件与发布版本不同；请先备份，确认后使用 --overwrite。")
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        if source is not None:
            stream = source.open("rb")
        else:
            request = urllib.request.Request(manifest["download_url"], headers={"User-Agent": "uav-scene-installer"})
            stream = urllib.request.urlopen(request, timeout=60)
        digest = hashlib.sha256()
        size = 0
        with stream, tempfile.NamedTemporaryFile(dir=target.parent, prefix=".fendai-", suffix=".tmp", delete=False) as output:
            pending = Path(output.name)
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > manifest["bytes"]:
                    raise ValueError("模型大小超过发布记录，未安装。")
                digest.update(chunk)
                output.write(chunk)
        if size != manifest["bytes"] or digest.hexdigest() != manifest["sha256"]:
            raise ValueError("模型大小或 SHA-256 校验失败，未安装。")
        if overwrite:
            os.replace(pending, target)
        else:
            # Hard-link creation fails if another upload appeared during download.
            os.link(pending, target)
        return target
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", help="与服务的 UAV_DATA_DIR 相同的数据根目录")
    parser.add_argument("--source", type=Path, help="已下载的本地 KSplat；省略时从 GitHub Release 下载")
    parser.add_argument("--overwrite", action="store_true", help="校验后替换已有不同版本的模型")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    try:
        target = install(data_root(args.data_dir), manifest, args.source, args.overwrite)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"安装失败：{exc}\n")
    print(f"模型已校验并安装：{target}")


if __name__ == "__main__":
    main()
