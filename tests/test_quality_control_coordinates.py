import io
import zipfile

import geopandas as gpd
import pandas as pd
import quality_control_coordinates.quality_control_coordinates as qcc
from shapely.geometry import Polygon


class DummyResp:
    def __init__(self, ok=True, status_code=200, content=b""):
        self.ok = ok
        self.status_code = status_code
        self.content = content


def _minimal_csv(path):
    pd.DataFrame(
        {
            "LONGITUD": [-74.0, -73.9],
            "LATITUD": [5.0, 5.1],
            "otro": [1, 2],
        }
    ).to_csv(path, index=False)


def _zip_with_dummy_shp():
    buff = io.BytesIO()
    with zipfile.ZipFile(buff, "w") as zf:
        zf.writestr("veredas.shp", b"dummy")
    return buff.getvalue()


def test_quality_control_returns_when_no_csv(tmp_path, capsys):
    in_dir = tmp_path / "in"
    in_dir.mkdir()

    qcc.quality_control_coordinates(
        input_path=str(in_dir),
        path_output=str(tmp_path / "out"),
        workspace="ws",
        url_geoserver="http://geo",
        store="store",
        user="u",
        password="p",
    )
    out = capsys.readouterr().out
    assert "No se encontró ningún archivo CSV" in out


def test_quality_control_bad_zip_writes_preview(monkeypatch, tmp_path):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _minimal_csv(in_dir / "datos.csv")

    monkeypatch.setattr(qcc.requests, "get", lambda *_args, **_kwargs: DummyResp(ok=True, content=b"not a zip"))

    qcc.quality_control_coordinates(
        input_path=str(in_dir),
        path_output=str(out_dir),
        workspace="ws",
        url_geoserver="http://geo",
        store="store",
        user="u",
        password="p",
    )

    assert (out_dir / "wfs_error_preview.txt").exists()


def test_quality_control_happy_path(monkeypatch, tmp_path):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _minimal_csv(in_dir / "datos.csv")

    monkeypatch.setattr(qcc.requests, "get", lambda *_args, **_kwargs: DummyResp(ok=True, content=_zip_with_dummy_shp()))

    veredas = gpd.GeoDataFrame(
        {
            "cod_ver": ["1001"],
            "cod_mpio": ["100"],
            "cod_dpto": ["10"],
            "geometry": [Polygon([(-75, 4), (-75, 6), (-73, 6), (-73, 4), (-75, 4)])],
        },
        crs="EPSG:4326",
    )
    monkeypatch.setattr(qcc.gpd, "read_file", lambda *_args, **_kwargs: veredas)

    def fake_sjoin(left, right, how="left", predicate="intersects"):
        joined = left.copy()
        joined["cod_ver"] = "1001"
        joined["cod_mpio"] = "100"
        joined["cod_dpto"] = "10"
        joined["index_right"] = 0
        return joined

    monkeypatch.setattr(qcc.gpd, "sjoin", fake_sjoin)

    qcc.quality_control_coordinates(
        input_path=str(in_dir),
        path_output=str(out_dir),
        workspace="ws",
        url_geoserver="http://geo",
        store="store",
        user="u",
        password="p",
    )

    out_csv = out_dir / "datos.csv"
    assert out_csv.exists()
    df = pd.read_csv(out_csv)
    assert {"adm1", "adm2", "adm3"}.issubset(set(df.columns))


def test_quality_control_http_error(monkeypatch, tmp_path, capsys):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _minimal_csv(in_dir / "datos.csv")

    monkeypatch.setattr(qcc.requests, "get", lambda *_args, **_kwargs: DummyResp(ok=False, status_code=500))
    qcc.quality_control_coordinates(str(in_dir), str(out_dir), "ws", "http://geo", "store", "u", "p")
    assert "Error al descargar shapefile" in capsys.readouterr().out


def test_quality_control_csv_without_long_lat(monkeypatch, tmp_path, capsys):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    pd.DataFrame({"X": [1], "Y": [2]}).to_csv(in_dir / "datos.csv", index=False)

    monkeypatch.setattr(qcc.requests, "get", lambda *_args, **_kwargs: DummyResp(ok=True, content=_zip_with_dummy_shp()))
    qcc.quality_control_coordinates(str(in_dir), str(out_dir), "ws", "http://geo", "store", "u", "p")
    assert "no contiene columnas LONGITUD y LATITUD" in capsys.readouterr().out


def test_quality_control_no_shp_extracted(monkeypatch, tmp_path, capsys):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _minimal_csv(in_dir / "datos.csv")

    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("solo.dbf", b"dbf")

    monkeypatch.setattr(qcc.requests, "get", lambda *_args, **_kwargs: DummyResp(ok=True, content=b.getvalue()))
    qcc.quality_control_coordinates(str(in_dir), str(out_dir), "ws", "http://geo", "store", "u", "p")
    assert "No se encontró ningún .shp" in capsys.readouterr().out


def test_quality_control_missing_cod_columns(monkeypatch, tmp_path, capsys):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _minimal_csv(in_dir / "datos.csv")

    monkeypatch.setattr(qcc.requests, "get", lambda *_args, **_kwargs: DummyResp(ok=True, content=_zip_with_dummy_shp()))
    veredas = gpd.GeoDataFrame({"otro": [1], "geometry": [Polygon([(-75, 4), (-75, 6), (-73, 6), (-73, 4), (-75, 4)])]}, crs="EPSG:4326")
    monkeypatch.setattr(qcc.gpd, "read_file", lambda *_args, **_kwargs: veredas)

    qcc.quality_control_coordinates(str(in_dir), str(out_dir), "ws", "http://geo", "store", "u", "p")
    assert "no contiene cod_ver/cod_mpio/cod_dpto" in capsys.readouterr().out


def test_quality_control_sets_crs_when_missing(monkeypatch, tmp_path, capsys):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _minimal_csv(in_dir / "datos.csv")

    monkeypatch.setattr(qcc.requests, "get", lambda *_args, **_kwargs: DummyResp(ok=True, content=_zip_with_dummy_shp()))

    # Sin CRS definido para cubrir la rama que asume EPSG:4326.
    veredas = gpd.GeoDataFrame(
        {
            "cod_ver": ["1001"],
            "cod_mpio": ["100"],
            "cod_dpto": ["10"],
            "geometry": [Polygon([(-75, 4), (-75, 6), (-73, 6), (-73, 4), (-75, 4)])],
        }
    )
    monkeypatch.setattr(qcc.gpd, "read_file", lambda *_args, **_kwargs: veredas)

    def fake_sjoin(left, right, how="left", predicate="intersects"):
        joined = left.copy()
        joined["cod_ver"] = "1001"
        joined["cod_mpio"] = "100"
        joined["cod_dpto"] = "10"
        return joined

    monkeypatch.setattr(qcc.gpd, "sjoin", fake_sjoin)

    qcc.quality_control_coordinates(str(in_dir), str(out_dir), "ws", "http://geo", "store", "u", "p")

    out = capsys.readouterr().out
    assert "no trae CRS definido" in out
    assert (out_dir / "datos.csv").exists()


def test_quality_control_reprojects_non_4326_layer(monkeypatch, tmp_path, capsys):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _minimal_csv(in_dir / "datos.csv")

    monkeypatch.setattr(qcc.requests, "get", lambda *_args, **_kwargs: DummyResp(ok=True, content=_zip_with_dummy_shp()))

    veredas = gpd.GeoDataFrame(
        {
            "cod_ver": ["1001"],
            "cod_mpio": ["100"],
            "cod_dpto": ["10"],
            "geometry": [Polygon([(-8350000, 500000), (-8350000, 700000), (-8150000, 700000), (-8150000, 500000), (-8350000, 500000)])],
        },
        crs="EPSG:3857",
    )
    monkeypatch.setattr(qcc.gpd, "read_file", lambda *_args, **_kwargs: veredas)

    def fake_sjoin(left, right, how="left", predicate="intersects"):
        joined = left.copy()
        joined["cod_ver"] = "1001"
        joined["cod_mpio"] = "100"
        joined["cod_dpto"] = "10"
        return joined

    monkeypatch.setattr(qcc.gpd, "sjoin", fake_sjoin)

    qcc.quality_control_coordinates(str(in_dir), str(out_dir), "ws", "http://geo", "store", "u", "p")

    out = capsys.readouterr().out
    assert "fue reproyectada a EPSG:4326" in out
    assert (out_dir / "datos.csv").exists()


def test_quality_control_returns_when_reprojection_fails(monkeypatch, tmp_path, capsys):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _minimal_csv(in_dir / "datos.csv")

    monkeypatch.setattr(qcc.requests, "get", lambda *_args, **_kwargs: DummyResp(ok=True, content=_zip_with_dummy_shp()))

    class _Crs:
        @staticmethod
        def to_string():
            return "EPSG:3116"

    class _Layer:
        crs = _Crs()

        def to_crs(self, *_args, **_kwargs):
            raise RuntimeError("no reprojection")

    monkeypatch.setattr(qcc.gpd, "read_file", lambda *_args, **_kwargs: _Layer())

    qcc.quality_control_coordinates(str(in_dir), str(out_dir), "ws", "http://geo", "store", "u", "p")

    out = capsys.readouterr().out
    assert "Error al reproyectar la capa a EPSG:4326" in out
    assert not (out_dir / "datos.csv").exists()
