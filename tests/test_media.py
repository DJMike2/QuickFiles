"""Vision inputs: one JPEG per photo, video or scanned page; undecodable files never raise."""
from pathlib import Path
import tempfile
import unittest

import av
from PIL import Image

from app.ai import media
from app.core.search import visual_kind

JPEG = b'\xff\xd8\xff'


class MediaTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_photos_are_downscaled_jpegs(self):
        Image.new('RGB', (3000, 2000), 'red').save(self.root/'photo.png')
        frame = media.image_frame(self.root/'photo.png')
        self.assertTrue(frame.startswith(JPEG))
        with Image.open(media.io.BytesIO(frame)) as image:
            self.assertEqual(max(image.size), media.IMAGE_SIDE)

    def test_video_frames_are_tiled_into_one_image(self):
        path = self.root/'clip.mp4'
        with av.open(str(path), 'w') as container:
            stream = container.add_stream('mpeg4', rate=10)
            stream.width, stream.height, stream.pix_fmt = 64, 48, 'yuv420p'
            for shade in range(0, 250, 10):
                for packet in stream.encode(av.VideoFrame.from_image(Image.new('RGB', (64, 48), (shade, 0, 0)))):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        self.assertTrue(media.video_frames(path).startswith(JPEG))

    def test_scanned_pages_render_without_a_text_layer(self):
        Image.new('RGB', (850, 1100), 'white').save(self.root/'scan.pdf')
        self.assertTrue(media.scanned_page(self.root/'scan.pdf').startswith(JPEG))

    def test_undecodable_files_return_none(self):
        for name in ('broken.jpg', 'broken.mp4', 'broken.pdf'):
            (self.root/name).write_bytes(b'not really media')
        self.assertIsNone(media.image_frame(self.root/'broken.jpg'))
        self.assertIsNone(media.video_frames(self.root/'broken.mp4'))
        self.assertIsNone(media.scanned_page(self.root/'broken.pdf'))

    def test_only_media_and_pdfs_without_text_need_a_description(self):
        self.assertEqual(visual_kind('.jpg'), 'image')
        self.assertEqual(visual_kind('.mkv'), 'video')
        self.assertEqual(visual_kind('.pdf', ' '), 'scan')
        self.assertIsNone(visual_kind('.pdf', 'A typed lease agreement with a real text layer'))
        self.assertIsNone(visual_kind('.txt'))


if __name__ == '__main__':
    unittest.main()
