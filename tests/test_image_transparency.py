from PIL import Image
import build_site
import pytest


@pytest.mark.parametrize('size', [(40, 30), (220, 220)])
def test_normalize_composites_transparency_on_white(tmp_path, size):
    path = tmp_path / 'transparent.png'
    Image.new('RGBA', size, (0, 0, 0, 0)).save(path)
    assert build_site.normalize_image_file(path)
    with Image.open(path) as result:
        assert result.mode == 'RGB'
        assert result.size == (220, 220)
        assert min(result.getpixel((110, 110))) >= 250


def test_cache_composites_transparency_on_white(tmp_path, monkeypatch):
    source = tmp_path / 'source.png'
    image = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
    image.paste((0, 160, 0, 255), (30, 30, 70, 70))
    image.save(source)
    monkeypatch.setattr(build_site, 'IMG_DIR', tmp_path / 'cache')
    result = build_site.cache_image('avocado', source.as_uri())
    assert result == 'img/avocado.png'
    with Image.open(tmp_path / 'cache' / 'avocado.png') as output:
        assert min(output.getpixel((65, 65))) >= 250
        assert output.getpixel((110, 110))[1] > 130
