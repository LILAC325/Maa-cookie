#!/usr/bin/env python3
"""同步 MaaFramework 上游 JSON Schema 到 tools/schema/，并重新生成 schema-manifest.json。

用法：
    python tools/sync_schema.py

上游 ref 固定为 REF（与 requirements.txt 里的 maafw 版本保持一致），
升级 maafw 时改这里一处，然后重跑本脚本即可。

注意：custom.action.schema.json / custom.recognition.schema.json 是本项目
自维护的，不由本脚本同步。
"""

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

REF = "v5.14.1"
BASE_URL = f"https://raw.githubusercontent.com/MaaXYZ/MaaFramework/{REF}/tools"
TARGET_DIR = Path(__file__).parent / "schema"

# 上游文件 -> 内容断言，防止下到错误页面（404 HTML 等）后静默覆盖
UPSTREAM = {
    "interface.schema.json": lambda j: j["properties"]["interface_version"]["const"] == 2,
    "interface_import.schema.json": lambda j: isinstance(j["properties"]["task"], dict),
    "interface_config.schema.json": lambda j: "controller" in j["required"],
    "pipeline.schema.json": lambda j: isinstance(j["$defs"]["Node"], dict),
}


def fetch(name: str, retries: int = 4) -> dict:
    """raw.githubusercontent 端点偶发连接被重置，需要重试。"""
    url = f"{BASE_URL}/{name}"
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise SystemExit(f"下载内容不是合法 JSON：{name}（{e}）")
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 - 网络抖动种类多，统一重试
            last_err = e
            if attempt < retries:
                wait = 2 * attempt
                print(f"  {name} 第 {attempt} 次失败（{e}），{wait}s 后重试")
                time.sleep(wait)
    raise SystemExit(f"下载失败：{name}（{last_err}）")


def main() -> int:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    files = []

    for name, check in UPSTREAM.items():
        data = fetch(name)
        try:
            ok = check(data)
        except (KeyError, TypeError) as e:
            raise SystemExit(f"上游结构校验失败：{name}（{e}）")
        if not ok:
            raise SystemExit(f"上游结构校验失败：{name}")

        content = json.dumps(data, indent=4) + "\n"
        # newline="\n" 必须显式指定：Windows 上默认会把 \n 翻译成 \r\n，
        # 导致磁盘字节与下面按 content 计算的 sha256 不一致。
        (TARGET_DIR / name).write_text(content, encoding="utf-8", newline="\n")
        files.append(
            {
                "path": f"tools/schema/{name}",
                "upstreamPath": f"tools/{name}",
                "url": f"{BASE_URL}/{name}",
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
        print(f"synced {name}")

    manifest = {
        "schemaVersion": 1,
        "source": {"repository": "MaaXYZ/MaaFramework", "ref": REF},
        "files": files,
    }
    (TARGET_DIR / "schema-manifest.json").write_text(
        json.dumps(manifest, indent=4) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Synced {len(files)} schema files into {TARGET_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())