"""Fetch pinned frontend assets with retries, mirrors and offline import."""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

STATIC = Path(__file__).resolve().parents[1] / "insight" / "static"
ASSETS = {
    "echarts.min.js": [
        "https://registry.npmmirror.com/echarts/5.6.0/files/dist/echarts.min.js",
        "https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js",
        "https://unpkg.com/echarts@5.6.0/dist/echarts.min.js",
    ],
    "china.json": ["https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json"],
    "ECHARTS-LICENSE.txt": [
        "https://registry.npmmirror.com/echarts/5.6.0/files/LICENSE",
        "https://cdn.jsdelivr.net/npm/echarts@5.6.0/LICENSE",
        "https://unpkg.com/echarts@5.6.0/LICENSE",
    ],
}


def validate(filename, content):
    text = content.decode("utf-8-sig")
    if filename == "china.json":
        data = json.loads(text)
        if (
            not isinstance(data, dict)
            or data.get("type") != "FeatureCollection"
            or not isinstance(data.get("features"), list)
        ):
            raise ValueError("地图不是有效的 GeoJSON FeatureCollection")
        # DataV may include unnamed boundary features alongside provinces.
        # Require usable province geometry, not a name on every feature.
        provinces = [
            feature
            for feature in data["features"]
            if isinstance(feature, dict)
            and isinstance(feature.get("properties"), dict)
            and isinstance(feature["properties"].get("name"), str)
            and feature["properties"]["name"].strip()
            and isinstance(feature.get("geometry"), dict)
            and feature["geometry"].get("type") in ("Polygon", "MultiPolygon")
            and isinstance(feature["geometry"].get("coordinates"), list)
            and feature["geometry"]["coordinates"]
        ]
        if not provinces:
            raise ValueError("地图中没有包含名称与有效坐标的省级多边形")
    elif filename == "echarts.min.js":
        if (
            len(content) < 100000
            or "echarts" not in text.lower()
            or text.lstrip().startswith("<")
        ):
            raise ValueError("响应不是有效的 ECharts 脚本（可能是错误页面）")
    elif "Apache License" not in text or "Version 2.0" not in text:
        raise ValueError("响应不是 ECharts 的 Apache 2.0 许可证")


def save(destination, content):
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def fetch(filename, urls, timeout, retries):
    for url in urls:
        for attempt in range(1, retries + 1):
            print(f"下载 {filename} [{attempt}/{retries}] {url}", flush=True)
            try:
                request = Request(
                    url,
                    headers={
                        "User-Agent": "nginx-insight/0.1 asset-downloader",
                        "Accept": "*/*",
                    },
                )
                with urlopen(request, timeout=timeout) as response:
                    content = response.read()
                validate(filename, content)
                return content
            except (URLError, OSError, ValueError) as exc:
                print(f"  失败: {exc}", file=sys.stderr, flush=True)
                if attempt < retries:
                    time.sleep(min(attempt, 3))
    raise RuntimeError(f"{filename} 的所有下载源均失败")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir", type=Path, help="从本地目录导入三个资源文件，不访问网络"
    )
    parser.add_argument("--force", action="store_true", help="重新下载已有有效资源")
    parser.add_argument(
        "--timeout", type=float, default=20, help="每次网络操作超时秒数，默认 20"
    )
    parser.add_argument(
        "--retries", type=int, default=2, help="每个源的尝试次数，默认 2"
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.retries < 1:
        parser.error("timeout 必须大于 0，retries 必须至少为 1")
    STATIC.mkdir(parents=True, exist_ok=True)
    failures = []
    for filename, urls in ASSETS.items():
        destination = STATIC / filename
        if destination.exists() and not args.force and not args.source_dir:
            try:
                validate(filename, destination.read_bytes())
                print(f"已存在，跳过 {filename}", flush=True)
                continue
            except (ValueError, OSError):
                pass
        try:
            if args.source_dir:
                content = (args.source_dir / filename).read_bytes()
                validate(filename, content)
            else:
                content = fetch(filename, urls, args.timeout, args.retries)
            save(destination, content)
            print(f"已保存 {filename} ({len(content)} bytes)", flush=True)
        except (OSError, ValueError, RuntimeError) as exc:
            failures.append(filename)
            print(f"资源失败: {exc}", file=sys.stderr, flush=True)
    if failures:
        print(
            "未完成: " + ", ".join(failures) + "。可重跑；已成功的资源会跳过。"
            "如服务器无法联网，请在可联网机器运行本脚本，再通过 --source-dir 导入资源。"
            "网络代理可使用 HTTPS_PROXY 环境变量配置。",
            file=sys.stderr,
            flush=True,
        )
        return 1
    print("全部前端资源已就绪。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
