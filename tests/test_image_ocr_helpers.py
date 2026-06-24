import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import metrology_config_app_v2_3_pie_delete_process_guard as app


class ImageOcrHelperTests(unittest.TestCase):
    def test_invalid_image_config_json_reports_clear_error(self):
        with self.assertRaisesRegex(ValueError, "Invalid Image OCR config JSON"):
            app.parse_image_parse_config("{not-json", ["Rx"])

    def test_missing_roi_reports_clear_error(self):
        config = {"metrics": {"Rx": {"regex": r"Rx\s*=\s*(\d+)"}}}
        with self.assertRaisesRegex(ValueError, "missing roi"):
            app.parse_image_parse_config(json.dumps(config), ["Rx"])

    def test_missing_regex_reports_clear_error(self):
        config = {"metrics": {"Rx": {"roi": [0.1, 0.1, 0.2, 0.2]}}}
        with self.assertRaisesRegex(ValueError, "missing regex"):
            app.parse_image_parse_config(json.dumps(config), ["Rx"])

    def test_regex_value_extraction(self):
        value = app.extract_regex_value("Rx = -12.34 deg", r"Rx\s*=\s*([-+]?\d+(?:\.\d+)?)", "Rx")
        self.assertEqual(value, "-12.34")

    def test_directory_source_chooses_latest_supported_image(self):
        old_wait = app.FILE_STABLE_WAIT_SECONDS
        app.FILE_STABLE_WAIT_SECONDS = 0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                older = root / "older.png"
                newer = root / "newer.jpg"
                ignored = root / "ignored.txt"
                older.write_bytes(b"old")
                newer.write_bytes(b"new")
                ignored.write_bytes(b"text")
                os.utime(older, (1000, 1000))
                os.utime(newer, (2000, 2000))
                selected, data, _stat = app.find_stable_image_file(str(root), {"file_pattern": "*"})
                self.assertEqual(Path(selected).name, "newer.jpg")
                self.assertEqual(data, b"new")
        finally:
            app.FILE_STABLE_WAIT_SECONDS = old_wait


class ImageOcrIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which(os.environ.get("MDCP_TESSERACT_CMD") or "tesseract"), "Tesseract is not installed")
    def test_synthetic_png_ocr_smoke(self):
        try:
            from PIL import Image, ImageDraw, ImageFont
            import pytesseract  # noqa: F401
            import cv2  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as ex:
            self.skipTest(f"OCR dependency missing: {ex}")

        old_wait = app.FILE_STABLE_WAIT_SECONDS
        app.FILE_STABLE_WAIT_SECONDS = 0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                image_path = Path(tmp) / "result_STEP01.png"
                img = Image.new("RGB", (900, 360), "white")
                draw = ImageDraw.Draw(img)
                try:
                    font = ImageFont.truetype("arial.ttf", 72)
                except Exception:
                    font = ImageFont.load_default()
                draw.text((30, 30), "Rx=1.23", fill="black", font=font)
                draw.text((30, 140), "Ry=4.56", fill="black", font=font)
                draw.text((30, 250), "Z=7.89", fill="black", font=font)
                img.save(image_path)

                config = {
                    "process_from_filename_regex": r"result_(?P<process_step>[^.]+)",
                    "ocr": {"lang": "eng", "psm": 7, "scale": 2.0, "threshold": True},
                    "metrics": {
                        "Rx": {"roi": [0.0, 0.00, 0.6, 0.28], "regex": r"Rx\s*[:=]?\s*([0-9.]+)"},
                        "Ry": {"roi": [0.0, 0.30, 0.6, 0.28], "regex": r"Ry\s*[:=]?\s*([0-9.]+)"},
                        "Z": {"roi": [0.0, 0.61, 0.6, 0.28], "regex": r"Z\s*[:=]?\s*([0-9.]+)"}
                    }
                }
                fields, rows, label = app.read_image_rows(
                    str(image_path), json.dumps(config), "PROD_A", "production_code",
                    "", "process_step", ["Rx", "Ry", "Z"]
                )
                self.assertIn("Rx", fields)
                self.assertEqual(rows[0]["production_code"], "PROD_A")
                self.assertEqual(rows[0]["process_step"], "STEP01")
                self.assertAlmostEqual(float(rows[0]["Rx"]), 1.23, places=2)
                self.assertAlmostEqual(float(rows[0]["Ry"]), 4.56, places=2)
                self.assertAlmostEqual(float(rows[0]["Z"]), 7.89, places=2)
                self.assertTrue(label.startswith("image:"))
        finally:
            app.FILE_STABLE_WAIT_SECONDS = old_wait


if __name__ == "__main__":
    unittest.main()
