import json
import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from insight.db import Base, Location, database
from insight.geo import Geocoder, normalize_location

OVERSEAS = {
    "status": 0,
    "address": "GB|None|None|None|None|0|0|0",
    "content": {
        "address": "",
        "address_detail": {
            "adcode": "0",
            "city": "",
            "city_code": 51442,
            "district": "",
            "nation": "England",
            "nation_code": "GBR",
            "province": "",
            "street": "",
            "street_number": "",
        },
        "point": {"x": "-0.1213709999998828", "y": "51.51401129288363"},
    },
}


class LocationNormalizationTests(unittest.TestCase):
    def normalize(self, detail, address=""):
        return normalize_location(
            {"status": 0, "address": address, "content": {"address_detail": detail}}
        )

    def test_user_overseas_response(self):
        self.assertEqual(
            normalize_location(OVERSEAS), {"province": "境外 / England", "city": ""}
        )

    def test_country_code_and_address_fallback(self):
        self.assertEqual(
            self.normalize({"province": "", "nation_code": "USA"})["province"],
            "境外 / USA",
        )
        self.assertEqual(
            self.normalize({"province": None}, "GB|None|None")["province"], "境外 / GB"
        )

    def test_foreign_region_not_treated_as_chinese_province(self):
        self.assertEqual(
            self.normalize(
                {
                    "province": "California",
                    "city": "Los Angeles",
                    "nation_code": "US",
                    "nation": "United States",
                }
            ),
            {"province": "境外 / United States", "city": "Los Angeles"},
        )

    def test_domestic_province_preserved(self):
        for detail in [
            {"province": "广东省", "city": "深圳市"},
            {"province": "广东省", "nation_code": "CHN"},
            {"province": "广东省", "nation": "中国"},
        ]:
            with self.subTest(detail=detail):
                self.assertEqual(self.normalize(detail)["province"], "广东省")

    def test_null_and_placeholder_fields_become_unknown(self):
        for value in [None, "", "None", " null ", "-", [], {}]:
            with self.subTest(value=value):
                self.assertEqual(
                    self.normalize({"province": value, "city": value, "nation": value}),
                    {"province": "未知", "city": ""},
                )

    def test_region_codes_and_column_limits(self):
        self.assertEqual(
            self.normalize({"nation_code": "HKG"})["province"], "香港特别行政区"
        )
        result = self.normalize({"province": "x" * 200, "city": "y" * 200})
        self.assertEqual(len(result["province"]), 100)
        self.assertEqual(len(result["city"]), 100)

    def test_invalid_structure_remains_retryable(self):
        for data in [
            {"status": 0},
            {"status": 0, "content": None},
            {"status": 0, "content": {"address_detail": []}},
        ]:
            with self.subTest(data=data), self.assertRaises(TypeError):
                normalize_location(data)

    def test_overseas_result_persisted_and_cached(self):
        with TemporaryDirectory() as directory:
            engine, sessions = database("sqlite:///" + str(Path(directory) / "db"))
            try:
                Base.metadata.create_all(engine)
                with sessions.begin() as session:
                    session.add(Location(ip="195.96.139.7", state="retry", attempts=3))
                cache, http = Mock(), Mock()
                cache.get.return_value = None
                http.get.return_value.status_code = 200
                http.get.return_value.json.return_value = OVERSEAS
                with patch.dict(os.environ, {"BAIDU_MAP_AK": "test"}):
                    worker = Geocoder(sessions, cache=cache, http=http)
                    self.assertEqual(worker.run_batch(), 1)
                    self.assertEqual(worker.run_batch(), 0)
                    with sessions() as session:
                        row = session.get(Location, "195.96.139.7")
                        self.assertEqual(row.state, "resolved")
                        self.assertEqual(row.province, "境外 / England")
                        self.assertEqual(row.attempts, 0)
                        self.assertGreater(
                            row.retry_at,
                            datetime.now(UTC).replace(tzinfo=None),
                        )
                    cached = cache.setex.call_args.args[2]
                    self.assertEqual(json.loads(cached)["province"], "境外 / England")
                    cache.get.return_value = cached
                    self.assertEqual(
                        worker.locate("195.96.139.7")["province"], "境外 / England"
                    )
                    http.get.assert_called_once()
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
