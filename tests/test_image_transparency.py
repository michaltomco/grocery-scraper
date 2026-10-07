"""Regression coverage for transparent thumbnails using the project test runner."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image
import build_site


class ImageTransparencyTests(unittest.TestCase):
    def test_normalize_composites_transparency_on_white(self):
        for size in [(40, 30), (220, 220)]:
            with self.subTest(size=size), TemporaryDirectory() as directory:
                path = Path(directory) / 'transparent.png'
                Image.new('RGBA', size, (0, 0, 0, 0)).save(path)
                self.assertTrue(build_site.normalize_image_file(path))
                with Image.open(path) as result:
                    self.assertEqual(result.mode, 'RGB')
                    self.assertEqual(result.size, (220, 220))
                    self.assertGreaterEqual(min(result.getpixel((110, 110))), 250)

    def test_cache_composites_transparency_on_white(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.png'
            image = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
            image.paste((0, 160, 0, 255), (30, 30, 70, 70))
            image.save(source)
            with patch.object(build_site, 'IMG_DIR', root / 'cache'):
                result = build_site.cache_image('avocado', source.as_uri())
            self.assertEqual(result, 'img/avocado.png')
            with Image.open(root / 'cache' / 'avocado.png') as output:
                self.assertGreaterEqual(min(output.getpixel((65, 65))), 250)
                self.assertGreater(output.getpixel((110, 110))[1], 130)


if __name__ == '__main__':
    unittest.main()
