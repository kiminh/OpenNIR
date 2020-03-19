import os
import io
import shutil
import tempfile
import contextlib
import hashlib
import tarfile
import zlib
import requests
from tqdm import tqdm
import onir


__all__ = ['download', 'download_stream', 'download_iter', 'download_if_needed']


__logger = None
def _logger():
    global __logger
    if __logger is None:
        __logger = onir.log.easy()
    return __logger


@contextlib.contextmanager
def download_tmp(url, tarf=False, buffer_size=io.DEFAULT_BUFFER_SIZE, expected_sha256=None):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(tmpdir + '/download', 'w+b') as f:
            for data in download_iter(url, buffer_size, expected_sha256=expected_sha256):
                f.write(data)
            f.flush()
            f.seek(0)
            if tarf:
                with tarfile.open(fileobj=f) as tarf:
                    yield tarf
            else:
                yield f


def download(url, file_name, buffer_size=io.DEFAULT_BUFFER_SIZE, expected_sha256=None):
    with download_tmp(url, buffer_size=buffer_size, expected_sha256=expected_sha256) as f:
        shutil.move(f.name, file_name)


def download_iter(url, buffer_size=io.DEFAULT_BUFFER_SIZE, expected_sha256=None):
    response = requests.get(url, stream=True)
    dlen = response.headers.get('content-length')
    if dlen is not None:
        dlen = int(dlen)

    hasher = hashlib.sha256()

    fmt = '{desc}: {percentage:3.1f}%{r_bar}'
    with tqdm(desc=url, total=dlen, unit='B', unit_scale=True, bar_format=fmt, leave=False) as pbar:
        for data in response.iter_content(chunk_size=buffer_size):
            pbar.update(len(data))
            hasher.update(data)
            yield data
        h = hasher.hexdigest()
        pbar.bar_format = '{desc} [{elapsed}] [{n_fmt}] [{rate_fmt}]'
        if expected_sha256 is not None:
            if h != expected_sha256:
                raise IOError(f"Expected {url} to have SHA256 hash {expected_sha256} but got {h}")
            else:
                pbar.bar_format = f'{pbar.bar_format} [sha256 hash verified]'
        else:
            _logger().warn(f'no hash provided for {url}; consider adding '
                           f'expected_sha256="{h}" to ensure data integrity.')
        _logger().debug(f'downloaded {pbar}')


def download_stream(url, encoding=None, skip_gz=False, buffer_size=io.DEFAULT_BUFFER_SIZE, expected_sha256=None):
    """
    Adapted from <https://stackoverflow.com/questions/6657820#answer-20260030>
    """
    stream = _url2stream(url, expected_sha256=expected_sha256)
    if url.endswith('.gz') and not skip_gz:
        stream = io.BufferedReader(IterStream(stream_gzip_decompress(stream)), buffer_size=buffer_size)
    if encoding is not None:
        stream = io.TextIOWrapper(stream, encoding=encoding)
    return stream


def download_if_needed(url, file_name, expected_sha256=None):
    if not os.path.exists(file_name):
        download(url, file_name, expected_sha256=expected_sha256)


def _url2stream(url, buffer_size=io.DEFAULT_BUFFER_SIZE, expected_sha256=None):
    it = download_iter(url, buffer_size, expected_sha256=expected_sha256)
    return io.BufferedReader(IterStream(it), buffer_size=buffer_size)


class IterStream(io.RawIOBase):
    def __init__(self, it):
        super().__init__()
        self.leftover = None
        self.it = it
    def readable(self):
        return True
    def readinto(self, b):
        pos = 0
        try:
            while pos < len(b):
                l = len(b) - pos  # We're supposed to return at most this much
                chunk = self.leftover or next(self.it)
                output, self.leftover = chunk[:l], chunk[l:]
                b[pos:pos+len(output)] = output
                pos += len(output)
            return pos
        except StopIteration:
            return pos    # indicate EOF


def stream_gzip_decompress(stream):
    dec = zlib.decompressobj(32 + zlib.MAX_WBITS)  # offset 32 to skip the header
    for chunk in stream:
        rv = dec.decompress(chunk)
        if rv:
            yield rv
