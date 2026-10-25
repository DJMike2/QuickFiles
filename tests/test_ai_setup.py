"""Engine archive compatibility and verified-download failure handling."""
from io import BytesIO
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from app.ai.binary_manager import BinaryManager, ENGINE_RELEASE
from app.ai.model_downloader import download_file


class SetupTests(unittest.TestCase):
    def test_pinned_cpu_packages_install_on_windows_macos_and_linux(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = [('win32', 'x86_64', 'win-cpu-x64.zip'),
                     ('darwin', 'arm64', 'macos-arm64.tar.gz'),
                     ('linux', 'x86_64', 'ubuntu-x64.tar.gz')]
            for system, machine, suffix in cases:
                with self.subTest(system=system):
                    name = f'llama-{ENGINE_RELEASE}-bin-{suffix}'
                    package = root/name
                    server_name = 'llama-server.exe' if system == 'win32' else 'llama-server'
                    if suffix.endswith('.zip'):
                        with zipfile.ZipFile(package, 'w') as archive:
                            archive.writestr('nested/'+server_name, b'engine')
                    else:
                        with tarfile.open(package, 'w:gz') as archive:
                            for file, content in [(server_name, b'engine'), ('libdemo.so.1', b'library')]:
                                item = tarfile.TarInfo('nested/'+file)
                                item.size = len(content)
                                archive.addfile(item, BytesIO(content))
                            link = tarfile.TarInfo('nested/libdemo.so')
                            link.type = tarfile.SYMTYPE
                            link.linkname = 'libdemo.so.1'
                            archive.addfile(link)
                    assets = {'assets': [{'name': name, 'browser_download_url': 'https://example.invalid/engine'}]}

                    def downloaded(url, destination, *args):
                        shutil.copyfile(package, destination)
                        return destination

                    hardware = SimpleNamespace(backend='cpu', os_warning='')
                    with patch('app.ai.binary_manager.sys.platform', system), \
                         patch('app.ai.binary_manager.platform.machine', return_value=machine), \
                         patch('app.ai.binary_manager.urllib.request.urlopen',
                               return_value=BytesIO(json.dumps(assets).encode())), \
                         patch('app.ai.binary_manager.download_file', downloaded):
                        manager = BinaryManager(root/system, hardware)
                        executable = manager.download()
                        self.assertEqual(executable.read_bytes(), b'engine')
                        if not suffix.endswith('.zip'):
                            self.assertEqual((executable.parent/'libdemo.so').read_bytes(), b'library')
                        self.assertEqual((manager.bin_dir/'release.txt').read_text().strip(), ENGINE_RELEASE)

    def test_failed_hash_does_not_replace_existing_model_or_leave_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)/'model.gguf'
            destination.write_bytes(b'old model')
            response = BytesIO(b'bad download')
            response.headers = {'Content-Length': '12'}
            with patch('app.ai.model_downloader.urllib.request.urlopen', return_value=response):
                with self.assertRaisesRegex(ValueError, 'SHA256'):
                    download_file('https://example.invalid/model', destination,
                                  expected_size=12, expected_sha256='0'*64)
            self.assertEqual(destination.read_bytes(), b'old model')
            self.assertFalse(destination.with_name('model.gguf.part').exists())


if __name__ == '__main__':
    unittest.main()
