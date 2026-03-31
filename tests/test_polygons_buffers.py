import os

import pandas as pd
from shapely.geometry import Point

from polygons_buffers import polygons_buffers as pb
from ganabosques_orm.enums.species import Species
from ganabosques_orm.enums.ugg import UGG
from ganabosques_orm.enums.source import Source


class DummyAdm1:
    def __init__(self, ext_id, ugg_size):
        self.ext_id = ext_id
        self.ugg_size = ugg_size


def _build_input_df():
    cols = {
        "LONGITUD": [-74.0, -74.0],
        "LATITUD": [5.0, 5.0],
        "adm1": ["11", "11"],
        Source.SIT_CODE.value: ["SIT-1", "SIT-1"],
    }
    for sp in [Species.BOVINOS.name, Species.BUFALINOS.name]:
        cols[f"{UGG.TERNEROS_MENORES_1_ANIO.name}_{sp}"] = [1, 1]
        cols[f"{UGG.HEMBRAS_MACHOS_1_2_ANIOS.name}_{sp}"] = [1, 1]
        cols[f"{UGG.HEMBRAS_MENORES_2_3_ANIOS.name}_{sp}"] = [1, 1]
        cols[f"{UGG.MACHOS_2_3_ANIOS.name}_{sp}"] = [1, 1]
        cols[f"{UGG.HEMBRAS_MAYORES_3_ANIOS.name}_{sp}"] = [1, 1]
        cols[f"{UGG.MACHOS_MAYORES_3_ANIOS.name}_{sp}"] = [1, 1]
    return pd.DataFrame(cols)


def test_buffer_generates_final_csv_and_geojson(monkeypatch, tmp_path):
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    in_dir.mkdir()

    monkeypatch.setattr(pb.os, "listdir", lambda *_args, **_kwargs: ["datos.csv"])
    monkeypatch.setattr(pb.pd, "read_csv", lambda *_args, **_kwargs: _build_input_df())

    class _Objects:
        @staticmethod
        def only(*_args, **_kwargs):
            return [DummyAdm1(ext_id="11", ugg_size=1.0)]

    monkeypatch.setattr(pb.Adm1, "objects", _Objects())
    monkeypatch.setattr(pb, "tqdm", lambda it, **_kwargs: it)

    monkeypatch.setattr(
        pb.gpd,
        "points_from_xy",
        lambda x, y: [Point(lon, lat) for lon, lat in zip(x, y)],
    )

    written_geojson = []

    def fake_to_file(self, path, driver=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}")
        written_geojson.append(path)

    monkeypatch.setattr(pb.gpd.GeoSeries, "to_file", fake_to_file)

    pb.buffer(path_input=str(in_dir), path_output=str(out_dir), source="SAGARI")

    final_csv = out_dir / "SAGARI_completa_final.csv"
    assert final_csv.exists()
    assert len(written_geojson) == 1
    assert written_geojson[0].endswith("SIT-1.geojson")


def test_buffer_returns_when_no_csv(monkeypatch, tmp_path, capsys):
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    in_dir.mkdir()

    monkeypatch.setattr(pb.os, "listdir", lambda *_args, **_kwargs: [])
    pb.buffer(path_input=str(in_dir), path_output=str(out_dir), source="SAGARI")
    assert "No se encontró ningún archivo CSV" in capsys.readouterr().out


def test_buffer_fallback_to_raw_db_field_with_space(monkeypatch, tmp_path):
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    in_dir.mkdir()

    monkeypatch.setattr(pb.os, "listdir", lambda *_args, **_kwargs: ["datos.csv"])
    monkeypatch.setattr(pb.pd, "read_csv", lambda *_args, **_kwargs: _build_input_df())

    class _ObjectsFail:
        @staticmethod
        def only(*_args, **_kwargs):
            raise RuntimeError("force fallback")

    monkeypatch.setattr(pb.Adm1, "objects", _ObjectsFail())

    class _FakeCollection:
        @staticmethod
        def find(*_args, **_kwargs):
            return [{"ext_id": "11", " ugg_size": 1.5}]

    class _FakeDB:
        def __getitem__(self, _name):
            return _FakeCollection()

    monkeypatch.setattr(pb, "get_db", lambda: _FakeDB())
    monkeypatch.setattr(pb, "tqdm", lambda it, **_kwargs: it)
    monkeypatch.setattr(pb.gpd, "points_from_xy", lambda x, y: [Point(lon, lat) for lon, lat in zip(x, y)])
    monkeypatch.setattr(pb.gpd.GeoSeries, "to_file", lambda self, path, driver=None: open(path, "w", encoding="utf-8").write("{}"))

    pb.buffer(path_input=str(in_dir), path_output=str(out_dir), source="SAGARI")

    assert (out_dir / "SAGARI_completa_final.csv").exists()


def test_buffer_raises_when_required_column_missing(monkeypatch, tmp_path):
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    in_dir.mkdir()

    df = _build_input_df().drop(columns=["LONGITUD"])
    monkeypatch.setattr(pb.os, "listdir", lambda *_args, **_kwargs: ["datos.csv"])
    monkeypatch.setattr(pb.pd, "read_csv", lambda *_args, **_kwargs: df)

    try:
        pb.buffer(path_input=str(in_dir), path_output=str(out_dir), source="SAGARI")
        assert False, "Debió lanzar ValueError"
    except ValueError as e:
        assert "Columna requerida no encontrada" in str(e)


def test_buffer_raises_when_no_valid_ugg_equivalences(monkeypatch, tmp_path):
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    in_dir.mkdir()

    monkeypatch.setattr(pb.os, "listdir", lambda *_args, **_kwargs: ["datos.csv"])
    monkeypatch.setattr(pb.pd, "read_csv", lambda *_args, **_kwargs: _build_input_df())

    class _Objects:
        @staticmethod
        def only(*_args, **_kwargs):
            return []

    monkeypatch.setattr(pb.Adm1, "objects", _Objects())

    class _FakeCollection:
        @staticmethod
        def find(*_args, **_kwargs):
            return [{"ext_id": "11", "ugg_size": None, " ugg_size": None}]

    class _FakeDB:
        def __getitem__(self, _name):
            return _FakeCollection()

    monkeypatch.setattr(pb, "get_db", lambda: _FakeDB())

    try:
        pb.buffer(path_input=str(in_dir), path_output=str(out_dir), source="SAGARI")
        assert False, "Debió lanzar RuntimeError"
    except RuntimeError as e:
        assert "No se encontraron equivalencias UGG/ha" in str(e)


def test_buffer_skips_rows_with_invalid_coordinates_or_radius(monkeypatch, tmp_path):
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    in_dir.mkdir()

    df = _build_input_df()
    df.loc[0, "LONGITUD"] = None
    df.loc[1, "LATITUD"] = None

    monkeypatch.setattr(pb.os, "listdir", lambda *_args, **_kwargs: ["datos.csv"])
    monkeypatch.setattr(pb.pd, "read_csv", lambda *_args, **_kwargs: df)

    class _Objects:
        @staticmethod
        def only(*_args, **_kwargs):
            return [DummyAdm1(ext_id="11", ugg_size=1.0)]

    monkeypatch.setattr(pb.Adm1, "objects", _Objects())
    monkeypatch.setattr(pb, "tqdm", lambda it, **_kwargs: it)
    monkeypatch.setattr(pb.gpd, "points_from_xy", lambda x, y: [Point(lon, lat) for lon, lat in zip(x, y)])

    written = []
    monkeypatch.setattr(pb.gpd.GeoSeries, "to_file", lambda self, path, driver=None: written.append(path))
    pb.buffer(path_input=str(in_dir), path_output=str(out_dir), source="SAGARI")
    assert written == []


def test_buffer_skips_row_when_polygon_construction_fails(monkeypatch, tmp_path):
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    in_dir.mkdir()

    monkeypatch.setattr(pb.os, "listdir", lambda *_args, **_kwargs: ["datos.csv"])
    monkeypatch.setattr(pb.pd, "read_csv", lambda *_args, **_kwargs: _build_input_df())

    class _Objects:
        @staticmethod
        def only(*_args, **_kwargs):
            return [DummyAdm1(ext_id="11", ugg_size=1.0)]

    monkeypatch.setattr(pb.Adm1, "objects", _Objects())
    monkeypatch.setattr(pb, "tqdm", lambda it, **_kwargs: it)
    monkeypatch.setattr(pb.gpd, "points_from_xy", lambda x, y: [Point(lon, lat) for lon, lat in zip(x, y)])
    monkeypatch.setattr(pb, "Polygon", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad polygon")))

    written = []
    monkeypatch.setattr(pb.gpd.GeoSeries, "to_file", lambda self, path, driver=None: written.append(path))

    pb.buffer(path_input=str(in_dir), path_output=str(out_dir), source="SAGARI")
    assert written == []


def test_buffer_covers_edge_rows_and_numeric_sit_filename(monkeypatch, tmp_path):
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    in_dir.mkdir()

    df = _build_input_df()
    df[Source.SIT_CODE.value] = [123.0, 123.0]

    monkeypatch.setattr(pb.os, "listdir", lambda *_args, **_kwargs: ["datos.csv"])
    monkeypatch.setattr(pb.pd, "read_csv", lambda *_args, **_kwargs: df)

    class _Objects:
        @staticmethod
        def only(*_args, **_kwargs):
            return [DummyAdm1(ext_id="11", ugg_size=1.0)]

    monkeypatch.setattr(pb.Adm1, "objects", _Objects())
    monkeypatch.setattr(pb, "tqdm", lambda it, **_kwargs: it)
    monkeypatch.setattr(pb.gpd, "points_from_xy", lambda x, y: [Point(lon, lat) for lon, lat in zip(x, y)])

    written_paths = []

    class _FakeGeoSeries:
        def __init__(self, _geoms, crs=None):
            self.crs = crs

        def to_file(self, path, driver=None):
            written_paths.append(path)

    monkeypatch.setattr(pb.gpd, "GeoSeries", _FakeGeoSeries)

    class _Poly:
        def __init__(self, valid):
            self.is_valid = valid

        def buffer(self, _distance):
            return _Poly(True)

    calls = {"n": 0}

    def fake_polygon(_coords):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Poly(False)
        return _Poly(True)

    monkeypatch.setattr(pb, "Polygon", fake_polygon)

    row_missing_radio = type("RowMissingRadio", (), {
        "LONGITUD": -74.0,
        "LATITUD": 5.0,
        Source.SIT_CODE.value: 123.0,
    })
    row_zero_radio = type("RowZeroRadio", (), {
        "LONGITUD": -74.0,
        "LATITUD": 5.0,
        "radio": 0,
        Source.SIT_CODE.value: 123.0,
    })
    row_nan_lon = type("RowNanLon", (), {
        "LONGITUD": float("nan"),
        "LATITUD": 5.0,
        "radio": 10,
        Source.SIT_CODE.value: 123.0,
    })
    row_valid = type("RowValid", (), {
        "LONGITUD": -74.0,
        "LATITUD": 5.0,
        "radio": 10,
        Source.SIT_CODE.value: 123.0,
    })

    def fake_itertuples(_self, index=False):
        yield row_missing_radio()
        yield row_zero_radio()
        yield row_nan_lon()
        yield row_valid()

    monkeypatch.setattr(pb.gpd.GeoDataFrame, "itertuples", fake_itertuples, raising=False)

    pb.buffer(path_input=str(in_dir), path_output=str(out_dir), source="SAGARI")

    assert len(written_paths) == 1
    assert written_paths[0].endswith("123.geojson")
