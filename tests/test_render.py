import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import main


class RenderTests(unittest.TestCase):
    def test_pdf_is_rendered_to_png(self):
        import pypdfium2 as pdfium

        def convert(command, timeout):
            source = Path(command[-1])
            document = pdfium.PdfDocument.new()
            try:
                page = document.new_page(320, 180)
                page.close()
                document.save(str(source.with_suffix(".pdf")))
            finally:
                document.close()
            return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

        with patch.object(main.shutil, "which", return_value="soffice"), patch.object(main, "run", side_effect=convert):
            report = main.check_render_capability()
        self.assertTrue(report["success"])
        self.assertTrue(report["png_rendered"])
        self.assertEqual(report["page_count"], 1)
        self.assertGreater(report["png_bytes"], 0)

    def test_sample_directories_are_isolated_and_cleaned(self):
        sources = []

        def convert(source, soffice, size):
            self.assertTrue(source.is_file())
            self.assertFalse(source.with_suffix(".pdf").exists())
            sources.append(source)
            source.with_suffix(".pdf").write_bytes(b"old output")
            return {"tested": True}

        with patch.object(main.shutil, "which", return_value="soffice"), patch.object(main, "convert_and_inspect", side_effect=convert):
            main.check_render_capability()
            main.check_render_capability()
        self.assertNotEqual(sources[0].parent, sources[1].parent)
        self.assertTrue(all(not source.parent.exists() for source in sources))

    def test_upload_omits_content_and_sample_specific_checks(self):
        class Request:
            async def stream(self):
                yield b"presentation"

        report = {"tested": True, "pdf_text": {
            "text_preview": "private content", "superscript_preserved": False,
            "cjk_rendered": False, "cjk_chars_in_pdf": 2,
        }}
        with patch.object(main.shutil, "which", return_value="soffice"), patch.object(main, "convert_and_inspect", return_value=report):
            result = asyncio.run(main.probe_pptx(Request()))
        self.assertEqual(result["pdf_text"], {"cjk_chars_in_pdf": 2})

    def test_invalid_pptx_is_rejected_without_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pptx"
            source.write_bytes(b"not a presentation")
            with patch.object(main, "run") as execute:
                report = main.convert_and_inspect(source, "soffice", source.stat().st_size)
            self.assertEqual(report["stage"], "输入校验")
            execute.assert_not_called()

    def test_conversion_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pptx"
            main.build_sample_pptx(source)
            with patch.object(main, "run", return_value={
                "ok": True, "returncode": 0, "stdout": "", "stderr": "source file could not be loaded"
            }) as execute:
                report = main.convert_and_inspect(source, "soffice", source.stat().st_size)
            self.assertFalse(report["success"])
            self.assertTrue(report["input_valid_pptx"])
            self.assertEqual(execute.call_args.args[0][1].split("=", 1)[0], "-env:UserInstallation")

    def test_upload_rejects_oversized_input(self):
        class Request:
            async def stream(self):
                yield b"x" * (20 * 1024 * 1024 + 1)

        with patch.object(main.shutil, "which", return_value="soffice"):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(main.probe_pptx(Request()))
        self.assertEqual(caught.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()