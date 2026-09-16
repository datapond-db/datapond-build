import json
from unittest.mock import patch

from datapond_build import fetch


class Resp:
    def __init__(self, body, headers=None):
        self.body, self.headers = body, headers or {"content-length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        yield self.body


def test_force_download_replaces_existing_file(tmp_path):
    dest = tmp_path / "s.zip"
    dest.write_bytes(b"old")
    with patch.object(fetch.requests, "get", return_value=Resp(b"new", {"content-length": "3", "etag": '"e2"'})):
        assert fetch.download_file("https://x/s.zip", dest, skip_if_exists=False, retries=1, quiet=True)
    assert dest.read_bytes() == b"new"
    assert json.loads((tmp_path / "s.zip.meta.json").read_text())["etag"] == "e2"


def test_if_changed_skips_unchanged_and_refetches_changed(tmp_path):
    dest = tmp_path / "s.zip"
    dest.write_bytes(b"old")
    (tmp_path / "s.zip.meta.json").write_text(json.dumps({"etag": "e1", "size": 3}))
    with patch.object(fetch, "remote_identity", return_value={"etag": "e1"}), patch.object(fetch.requests, "get") as get:
        assert fetch.download_file("https://x/s.zip", dest, refresh="if-changed", retries=1, quiet=True)
        assert not get.called and dest.read_bytes() == b"old"
    with patch.object(fetch, "remote_identity", return_value={"etag": "e2"}), \
            patch.object(fetch.requests, "get", return_value=Resp(b"new", {"content-length": "3", "etag": '"e2"'})):
        assert fetch.download_file("https://x/s.zip", dest, refresh="if-changed", retries=1, quiet=True)
    assert dest.read_bytes() == b"new"


def test_failed_download_keeps_existing_file(tmp_path):
    dest = tmp_path / "s.zip"
    dest.write_bytes(b"old")
    with patch.object(fetch.requests, "get", return_value=Resp(b"ne", {"content-length": "3"})):
        assert not fetch.download_file("https://x/s.zip", dest, refresh="always", retries=1, quiet=True)
    assert dest.read_bytes() == b"old" and not (tmp_path / "s.zip.part").exists()
