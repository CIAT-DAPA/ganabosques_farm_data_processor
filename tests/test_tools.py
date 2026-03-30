import io
import zipfile

from tools.log_print import log_print
from tools import shapefile_utils as su


class DummyLogger:
    def __init__(self):
        self.calls = []

    def debug(self, msg):
        self.calls.append(("debug", msg))

    def info(self, msg):
        self.calls.append(("info", msg))

    def warning(self, msg):
        self.calls.append(("warning", msg))

    def error(self, msg):
        self.calls.append(("error", msg))

    def critical(self, msg):
        self.calls.append(("critical", msg))


def test_log_print_dispatches_levels(capsys):
    lg = DummyLogger()

    log_print(lg, "a", "debug")
    log_print(lg, "b", "warning")
    log_print(lg, "c", "error")
    log_print(lg, "d", "critical")
    log_print(lg, "e", "info")

    assert [c[0] for c in lg.calls] == ["debug", "warning", "error", "critical", "info"]
    out = capsys.readouterr().out
    assert "a" in out and "e" in out


class DummyResp:
    def __init__(self, ok=True, status_code=200, content=b""):
        self.ok = ok
        self.status_code = status_code
        self.content = content


def test_ensure_adm3_shapefile_returns_existing(tmp_path):
    shp = tmp_path / "adm3.shp"
    shp.write_text("x", encoding="utf-8")

    out = su.ensure_adm3_shapefile(
        shapefiles_dir=str(tmp_path),
        workspace="ws",
        url_geoserver="http://x",
        store="st",
        user="u",
        password="p",
    )
    assert out.endswith("adm3.shp")


def test_ensure_adm3_shapefile_download_success(monkeypatch, tmp_path):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("layer.shp", b"shp-data")
        z.writestr("layer.dbf", b"dbf-data")

    monkeypatch.setattr(su.requests, "get", lambda *args, **kwargs: DummyResp(ok=True, content=b.getvalue()))

    out = su.ensure_adm3_shapefile(
        shapefiles_dir=str(tmp_path),
        workspace="ws",
        url_geoserver="http://x",
        store="st",
        user="u",
        password="p",
    )
    assert out is not None and out.endswith(".shp")


def test_ensure_adm3_shapefile_http_error(monkeypatch, tmp_path):
    monkeypatch.setattr(su.requests, "get", lambda *args, **kwargs: DummyResp(ok=False, status_code=500, content=b""))

    out = su.ensure_adm3_shapefile(
        shapefiles_dir=str(tmp_path),
        workspace="ws",
        url_geoserver="http://x",
        store="st",
        user="u",
        password="p",
    )
    assert out is None


def test_ensure_adm3_shapefile_bad_zip(monkeypatch, tmp_path):
    monkeypatch.setattr(su.requests, "get", lambda *args, **kwargs: DummyResp(ok=True, content=b"notzip"))

    out = su.ensure_adm3_shapefile(
        shapefiles_dir=str(tmp_path),
        workspace="ws",
        url_geoserver="http://x",
        store="st",
        user="u",
        password="p",
    )
    assert out is None
    assert (tmp_path / "wfs_error_preview.txt").exists()


def test_ensure_adm3_shapefile_zip_without_shp(monkeypatch, tmp_path):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("layer.dbf", b"dbf-data")

    monkeypatch.setattr(su.requests, "get", lambda *args, **kwargs: DummyResp(ok=True, content=b.getvalue()))

    out = su.ensure_adm3_shapefile(
        shapefiles_dir=str(tmp_path),
        workspace="ws",
        url_geoserver="http://x",
        store="st",
        user="u",
        password="p",
    )
    assert out is None


def test_ensure_adm3_shapefile_handles_request_exception(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise RuntimeError("network")

    monkeypatch.setattr(su.requests, "get", boom)
    out = su.ensure_adm3_shapefile(
        shapefiles_dir=str(tmp_path),
        workspace="ws",
        url_geoserver="http://x",
        store="st",
        user="u",
        password="p",
    )
    assert out is None
