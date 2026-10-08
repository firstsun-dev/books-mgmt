import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gdrive_sync as gs


class SanitizePathComponentTests(unittest.TestCase):
    def test_slash_becomes_fullwidth_solidus(self):
        self.assertEqual(gs.sanitize_path_component("80/20法則"), "80／20法則")

    def test_backslash_becomes_fullwidth_backslash(self):
        self.assertEqual(gs.sanitize_path_component("a\\b"), "a＼b")

    def test_control_chars_and_null_byte_stripped(self):
        self.assertEqual(gs.sanitize_path_component("abc\x00def\x01"), "abcdef")

    def test_chinese_and_punctuation_preserved(self):
        name = "良好習慣：原子習慣的實踐"
        self.assertEqual(gs.sanitize_path_component(name), name)

    def test_dot_and_dotdot_rejected_as_traversal(self):
        self.assertEqual(gs.sanitize_path_component("."), "_untitled_")
        self.assertEqual(gs.sanitize_path_component(".."), "_untitled_")

    def test_empty_and_whitespace_only_rejected(self):
        self.assertEqual(gs.sanitize_path_component(""), "_untitled_")
        self.assertEqual(gs.sanitize_path_component("   "), "_untitled_")

    def test_idempotent(self):
        name = "80/20法則\\測試\x00"
        once = gs.sanitize_path_component(name)
        twice = gs.sanitize_path_component(once)
        self.assertEqual(once, twice)

    def test_does_not_reintroduce_ascii_slash_via_nfkc(self):
        # Guards against a regression where NFKC normalization (applied after
        # the slash substitution) would fold U+FF0F back into ASCII '/'.
        sanitized = gs.sanitize_path_component("80/20法則")
        self.assertNotIn("/", sanitized)


class BuildFileNameTests(unittest.TestCase):
    def test_identical_series_and_chapter_simplifies(self):
        self.assertEqual(gs.build_file_name("80／20法則", "80／20法則"), "80／20法則.txt")

    def test_differing_series_and_chapter(self):
        self.assertEqual(
            gs.build_file_name("原子習慣", "第一章"),
            "原子習慣 - 第一章.txt",
        )


class ResolveRemotePathTests(unittest.TestCase):
    def setUp(self):
        gs._remote_path_owners.clear()

    def test_matches_manually_corrected_drive_file(self):
        # Production bug: Kavita title "80/20法則" previously produced
        # "良好習慣/80/20法則.txt" (an unwanted "80" folder). The corrected
        # Drive file is "良好習慣/80／20法則.txt" (fullwidth solidus) - confirm
        # the sanitizer reproduces that exact name so it is recognized/skipped.
        remote_path = gs.resolve_remote_path("良好習慣", "80/20法則.txt", chapter_id=1)
        self.assertEqual(remote_path, "良好習慣/80／20法則.txt")
        self.assertEqual(remote_path.count("/"), 1)

    def test_collection_name_with_slash_is_sanitized(self):
        remote_path = gs.resolve_remote_path("A/B", "file.txt", chapter_id=1)
        self.assertEqual(remote_path, "A／B/file.txt")
        self.assertEqual(remote_path.count("/"), 1)

    def test_same_chapter_id_is_idempotent(self):
        first = gs.resolve_remote_path("col", "same.txt", chapter_id=42)
        second = gs.resolve_remote_path("col", "same.txt", chapter_id=42)
        self.assertEqual(first, second)

    def test_different_chapters_colliding_on_name_are_disambiguated(self):
        first = gs.resolve_remote_path("col", "same.txt", chapter_id=1)
        second = gs.resolve_remote_path("col", "same.txt", chapter_id=2)
        self.assertNotEqual(first, second)
        self.assertEqual(first, "col/same.txt")
        self.assertEqual(second, "col/same (2).txt")


class ValidateDownloadedFileTests(unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(gs.BookProcessingError):
            gs.validate_downloaded_file(Path("/nonexistent/path.pdf"), 4)

    def test_empty_file_raises(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "empty.pdf"
            p.touch()
            with self.assertRaises(gs.BookProcessingError):
                gs.validate_downloaded_file(p, 4)

    def test_wrong_magic_for_declared_pdf_raises(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.pdf"
            p.write_bytes(b"start of something that is not a pdf at all")
            with self.assertRaises(gs.BookProcessingError):
                gs.validate_downloaded_file(p, 4)

    def test_wrong_magic_for_declared_epub_raises(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.epub"
            p.write_bytes(b"%PDF-1.4 this is actually a pdf not a zip/epub")
            with self.assertRaises(gs.BookProcessingError):
                gs.validate_downloaded_file(p, 3)

    def test_correct_magic_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            pdf_path = Path(d) / "real.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n...")
            gs.validate_downloaded_file(pdf_path, 4)  # should not raise

            epub_path = Path(d) / "real.epub"
            epub_path.write_bytes(b"PK\x03\x04...")
            gs.validate_downloaded_file(epub_path, 3)  # should not raise


class ValidateTextOutputTests(unittest.TestCase):
    def test_too_short_text_raises(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.txt"
            p.write_text("   \n  ", encoding="utf-8")
            with self.assertRaises(gs.BookProcessingError):
                gs.validate_text_output(p)

    def test_adequate_text_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.txt"
            p.write_text("這是一段足夠長的範例文字內容，用於通過驗證測試。" * 2, encoding="utf-8")
            gs.validate_text_output(p)  # should not raise


class CheckGdriveFileExistsTests(unittest.TestCase):
    def setUp(self):
        gs.gdrive_files_cache.clear()

    def tearDown(self):
        gs.gdrive_files_cache.clear()

    def test_cache_hit_for_corrected_drive_file_skips_without_network(self):
        gs.gdrive_files_cache.add("books-clean-text/良好習慣/80／20法則.txt")
        self.assertTrue(
            gs.check_gdrive_file_exists("books-clean-text/良好習慣/80／20法則.txt")
        )

    def test_cache_miss_for_old_unsafe_path(self):
        gs.gdrive_files_cache.add("books-clean-text/良好習慣/80／20法則.txt")
        # The old buggy path (with a literal '/' folder) should NOT be
        # considered the same file as the corrected one.
        self.assertFalse(
            "books-clean-text/良好習慣/80/20法則.txt" in gs.gdrive_files_cache
        )


class PdfToTxtErrorClassificationTests(unittest.TestCase):
    def test_corrupt_pdf_structure_raises_book_processing_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            pdf_path = Path(d) / "corrupt.pdf"
            # Starts with a valid PDF magic header but has no real xref/root -
            # mirrors the "Cannot find Root object in pdf" production failures.
            pdf_path.write_bytes(b"%PDF-1.4\nnot a real pdf body, no xref or root object here")
            txt_path = Path(d) / "out.txt"
            with self.assertRaises(gs.BookProcessingError):
                gs.pdf_to_txt(pdf_path, txt_path)


class EpubToTxtErrorClassificationTests(unittest.TestCase):
    def test_corrupt_zip_raises_book_processing_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            epub_path = Path(d) / "corrupt.epub"
            epub_path.write_bytes(b"PK\x03\x04garbage not a real zip body")
            txt_path = Path(d) / "out.txt"
            with self.assertRaises(gs.BookProcessingError):
                gs.epub_to_txt(epub_path, txt_path)

    def test_valid_epub_extracts_text(self):
        import tempfile
        from ebooklib import epub as epub_module

        with tempfile.TemporaryDirectory() as d:
            epub_path = Path(d) / "valid.epub"
            book = epub_module.EpubBook()
            book.set_identifier("id1")
            book.set_title("Test")
            book.set_language("en")
            chap = epub_module.EpubHtml(title="Chap1", file_name="chap1.xhtml", lang="en")
            chap.content = "<h1>Hello</h1><p>World content here.</p>"
            book.add_item(chap)
            book.toc = (chap,)
            book.add_item(epub_module.EpubNcx())
            book.add_item(epub_module.EpubNav())
            book.spine = ["nav", chap]
            epub_module.write_epub(str(epub_path), book)

            txt_path = Path(d) / "out.txt"
            gs.epub_to_txt(epub_path, txt_path)
            text = txt_path.read_text(encoding="utf-8")
            self.assertIn("Hello", text)
            self.assertIn("World content here", text)


if __name__ == "__main__":
    unittest.main()
