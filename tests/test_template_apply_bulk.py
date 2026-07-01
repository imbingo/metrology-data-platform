import tempfile
import unittest
from pathlib import Path

import metrology_config_app_v2_3_pie_delete_process_guard as app


class TemplateApplyBulkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_file = app.DB_FILE
        app.DB_FILE = str(Path(self.tmp.name) / "template_apply_demo.db")
        app.init_db()
        self.conn = app.get_conn()
        self.cur = self.conn.cursor()
        self.cur.execute(
            "INSERT INTO production_config (production_code, production_name, updated_at) VALUES (?, ?, ?)",
            ("DEMO_PROD_BULK", "Demo bulk template production", app.now_str()),
        )
        self.production_id = self.cur.lastrowid
        self.template_a = self._insert_template("Demo-Rxy", ["Rx", "Ry"])
        self.template_b = self._insert_template("Demo-Z", ["Z"])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        app.DB_FILE = self.old_db_file
        self.tmp.cleanup()

    def _insert_template(self, name, metrics):
        self.cur.execute(
            """
            INSERT INTO template_config (
                template_name, template_version, data_source_type, header_row_index, delimiter, encoding,
                production_code_column, process_step_column, sample_fields_json, description, created_at, updated_at
            ) VALUES (?, 'v1.0', 'csv', 1, ',', 'auto', 'production_code', 'process_step', '[]', '', ?, ?)
            """,
            (name, app.now_str(), app.now_str()),
        )
        template_id = self.cur.lastrowid
        for idx, metric in enumerate(metrics):
            self.cur.execute(
                """
                INSERT INTO template_metric_config (
                    template_id, metric_name, source_column, data_type, unit, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, 'number', 'um', ?, ?, ?)
                """,
                (template_id, metric, metric, idx, app.now_str(), app.now_str()),
            )
        return template_id

    def test_page_offers_bulk_select_controls(self):
        html = app.page_template_apply_bulk({"username": "admin", "role": "admin"}, self.production_id)
        self.assertIn("全选模板", html)
        self.assertIn("全不选", html)
        self.assertIn('name="template_ids"', html)
        self.assertIn("Demo-Rxy", html)
        self.assertIn("Demo-Z", html)

    def test_bulk_apply_selected_templates_creates_one_item_per_template(self):
        form = {
            "production_id": [str(self.production_id)],
            "template_ids": [str(self.template_a), str(self.template_b)],
            "item_name": ["DemoItem"],
            "process_step": [""],
            "execution_time_text": ["after process"],
            "equipment_name": ["TOOL-DEMO"],
            "data_source_path": [r"\\demo-server\metrology\result.csv"],
            "scan_frequency_seconds": ["30"],
        }
        applied = app.handle_template_apply_bulk({"username": "admin", "role": "admin"}, form, "127.0.0.1")
        self.assertEqual(len(applied), 2)

        rows = self.conn.execute(
            "SELECT * FROM measurement_item_config WHERE production_id=? ORDER BY id",
            (self.production_id,),
        ).fetchall()
        self.assertEqual([r["item_name"] for r in rows], ["DemoItem - Demo-Rxy", "DemoItem - Demo-Z"])
        self.assertEqual([r["data_source_path"] for r in rows], [r"\\demo-server\metrology\result.csv"] * 2)
        self.assertEqual([r["scan_frequency_seconds"] for r in rows], [30, 30])

        metric_counts = [
            self.conn.execute("SELECT COUNT(*) AS c FROM metric_config WHERE item_id=?", (r["id"],)).fetchone()["c"]
            for r in rows
        ]
        self.assertEqual(metric_counts, [2, 1])

        log_count = self.conn.execute("SELECT COUNT(*) AS c FROM template_apply_log").fetchone()["c"]
        self.assertEqual(log_count, 2)

    def test_legacy_single_template_id_still_works(self):
        form = {
            "production_id": [str(self.production_id)],
            "template_id": [str(self.template_a)],
            "item_name": [""],
            "process_step": [""],
            "execution_time_text": [""],
            "equipment_name": [""],
            "data_source_path": [r"\\demo-server\metrology\result.csv"],
            "scan_frequency_seconds": ["60"],
        }
        applied = app.handle_template_apply_bulk({"username": "admin", "role": "admin"}, form, "127.0.0.1")
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["item_name"], "Demo-Rxy")


if __name__ == "__main__":
    unittest.main()
