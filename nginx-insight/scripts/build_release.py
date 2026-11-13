"""Build a production source archive without credentials or development files."""

import argparse
import re
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_release(output):
    metadata = ROOT / "pyproject.toml"
    match = re.search(
        r'^version\s*=\s*"([0-9A-Za-z.+-]+)"\s*$', metadata.read_text(), re.MULTILINE
    )
    if not match:
        raise ValueError("无法从 pyproject.toml 读取版本号")
    version = match[1]
    files = [metadata, ROOT / ".env.example"]
    files.extend(sorted((ROOT / "insight").glob("*.py")))
    for directory, suffixes in (
        ("templates", {".html"}),
        (
            "static",
            {".js", ".css", ".json", ".svg", ".png", ".ico", ".woff", ".woff2"},
        ),
    ):
        files.extend(
            sorted(
                path
                for path in (ROOT / "insight" / directory).rglob("*")
                if path.is_file() and path.suffix in suffixes
            )
        )
    required = [
        ROOT / "insight" / "__init__.py",
        ROOT / "insight" / "templates" / "index.html",
        *(
            ROOT / "insight" / "static" / name
            for name in (
                "app.js",
                "style.css",
                "echarts.min.js",
                "china.json",
            )
        ),
    ]
    for path in files + required:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"发布文件缺失或为符号链接：{path}")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"nginx-insight-v{version}.tar.gz"
    # Exclusive creation prevents silently replacing an earlier release.
    try:
        with archive.open("xb") as destination:
            try:
                with tarfile.open(fileobj=destination, mode="w:gz") as package:
                    for path in files:
                        package.add(
                            path,
                            arcname=str(Path("nginx-insight") / path.relative_to(ROOT)),
                        )
            except BaseException:
                archive.unlink()
                raise
    except FileExistsError:
        raise ValueError(
            f"发布包已存在，请更新版本号或选择其他输出目录：{archive}"
        ) from None
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "dist", help="输出目录，默认项目 dist/"
    )
    args = parser.parse_args()
    try:
        archive = build_release(args.output.resolve())
    except (OSError, ValueError) as exc:
        parser.exit(1, f"打包失败：{exc}\n")
    print(f"发布包：{archive}")


if __name__ == "__main__":
    main()
