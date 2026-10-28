"""One small JPEG per photo, video or scanned page, for the vision model to describe.

Every function returns None when the file cannot be decoded (corrupt, unsupported codec, or the
optional imaging libraries are missing), so indexing never fails because of one bad file.
"""
import io
import logging

log = logging.getLogger(__name__)
# Long side in pixels: enough for readable screenshots and scans, small enough to stay fast.
IMAGE_SIDE = 1024
SCAN_SIDE = 1280
VIDEO_FRAMES = 4


def _jpeg(image, side):
    image = image.convert('RGB')
    image.thumbnail((side, side))
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=85)
    return buffer.getvalue()


def image_frame(path, side=IMAGE_SIDE):
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as image:
            image.draft('RGB', (side, side))  # JPEGs decode straight at a reduced size.
            return _jpeg(ImageOps.exif_transpose(image), side)
    except Exception:  # Any decoder error just means "cannot describe this file".
        log.debug('Could not decode image for description: %s', path, exc_info=True)
        return None


def video_frames(path, side=IMAGE_SIDE, count=VIDEO_FRAMES):
    """Frames spread across the video, tiled in a 2x2 grid so one request covers the whole clip."""
    try:
        import av
        from PIL import Image
        frames = []
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            duration = container.duration / av.time_base if container.duration else 0
            for index in range(count):
                if duration > 0:
                    container.seek(int(duration * (index + .5) / count * av.time_base))
                frame = next(container.decode(stream), None)
                if frame is not None:
                    frames.append(frame.to_image())
        if not frames:
            return None
        tile = side // 2
        grid = Image.new('RGB', (side, side), 'black')
        for index, frame in enumerate(frames[:4]):
            frame.thumbnail((tile, tile))
            grid.paste(frame, ((index % 2) * tile + (tile - frame.width) // 2,
                               (index // 2) * tile + (tile - frame.height) // 2))
        return _jpeg(grid, side)
    except Exception:
        log.debug('Could not decode video for description: %s', path, exc_info=True)
        return None


def scanned_page(path, side=SCAN_SIDE):
    """The first page of a PDF without a text layer, rendered like a scan."""
    try:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(str(path))
        try:
            page = document[0]
            width, height = page.get_size()
            return _jpeg(page.render(scale=side / max(width, height, 1)).to_pil(), side)
        finally:
            document.close()
    except Exception:
        log.debug('Could not render PDF page for description: %s', path, exc_info=True)
        return None


FRAMES = {'image': image_frame, 'video': video_frames, 'scan': scanned_page}
