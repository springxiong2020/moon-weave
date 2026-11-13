import importlib.util
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import URLError

spec = importlib.util.spec_from_file_location(
    "fetch_assets", Path(__file__).resolve().parents[1] / "scripts/fetch_assets.py"
)
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)
JS = b"/* echarts */" + b" " * 100001
MAP = json.dumps(
    {
        "type": "FeatureCollection",
        "features": [
            {
                "properties": {"name": "广东省"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[113, 22], [114, 22], [113, 23], [113, 22]]],
                },
            }
        ],
    }
).encode()
LICENSE = b"Apache License Version 2.0"


class FetchTests(unittest.TestCase):
    def test_map_with_unnamed_boundary_features(self):
        data = json.loads(MAP)
        data["features"].extend(
            [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [[[110, 20], [111, 21]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": None,
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [[[[110, 20], [111, 20], [110, 21], [110, 20]]]],
                    },
                },
                {"type": "Feature", "properties": {}, "geometry": None},
            ]
        )
        assets.validate("china.json", json.dumps(data).encode())

    def test_map_requires_usable_named_polygon(self):
        for data in [
            [],
            {"type": "FeatureCollection", "features": []},
            {
                "type": "FeatureCollection",
                "features": [{"properties": {}, "geometry": None}],
            },
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "properties": {"name": "广东省"},
                        "geometry": {"type": "Polygon", "coordinates": []},
                    }
                ],
            },
        ]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                assets.validate("china.json", json.dumps(data).encode())

    def test_mirror_fallback(self):
        response = io.BytesIO(JS)
        with patch.object(
            assets, "urlopen", side_effect=[URLError("reset"), response]
        ) as download:
            self.assertEqual(
                assets.fetch(
                    "echarts.min.js", ["https://first", "https://second"], 1, 1
                ),
                JS,
            )
        self.assertEqual(download.call_count, 2)

    def test_retry_then_success(self):
        with (
            patch.object(
                assets,
                "urlopen",
                side_effect=[ConnectionResetError("reset"), io.BytesIO(JS)],
            ),
            patch.object(assets.time, "sleep"),
        ):
            self.assertEqual(
                assets.fetch("echarts.min.js", ["https://first"], 1, 2), JS
            )

    def test_reject_error_pages(self):
        for name in assets.ASSETS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                assets.validate(name, b"<html>blocked</html>")

    def test_offline_import_and_skip(self):
        with TemporaryDirectory() as root:
            root = Path(root)
            source, target = root / "source", root / "target"
            source.mkdir()
            for name, content in zip(assets.ASSETS, [JS, MAP, LICENSE]):
                (source / name).write_bytes(content)
            with (
                patch.object(assets, "STATIC", target),
                patch.object(assets, "urlopen") as download,
            ):
                self.assertEqual(assets.main(["--source-dir", str(source)]), 0)
                self.assertEqual(assets.main([]), 0)
                download.assert_not_called()
                self.assertEqual((target / "china.json").read_bytes(), MAP)

    def test_failure_exit_and_keep_existing(self):
        with TemporaryDirectory() as root:
            target = Path(root)
            (target / "echarts.min.js").write_bytes(JS)
            with (
                patch.object(assets, "STATIC", target),
                patch.object(assets, "fetch", side_effect=RuntimeError("reset")),
            ):
                self.assertEqual(assets.main([]), 1)
            self.assertEqual((target / "echarts.min.js").read_bytes(), JS)


if __name__ == "__main__":
    unittest.main()
