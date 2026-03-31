import json
import runpy
import geopandas as gpd
import pandas as pd
import pytest

from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, mapping

from quality_control_coordinates import quality_control_geofarmer as qcg


def test_detect_crs_from_geojson():
    geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}},
        "features": [],
    }
    assert qcg._detect_crs_from_geojson(geojson) == 3857


def test_filter_allowed_fields_keeps_only_whitelist():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "farm_id": "GF-1",
                    "farm_name": "Finca",
                    "farm_code": "SIT-1",
                    "contact_name": "No debe quedar",
                    "otro": "tampoco",
                },
                "geometry": {"type": "Point", "coordinates": [-74.0, 5.0]},
            }
        ],
    }

    out = qcg._filter_allowed_fields(geojson)
    props = out["features"][0]["properties"]
    assert "farm_id" in props
    assert "contact_name" not in props
    assert "otro" not in props


def test_validate_geometry_reprojects_3857():
    # Cuadrado pequeño alrededor de Bogotá en EPSG:3857
    coords = [
        [-8239000, 556000],
        [-8239000, 558000],
        [-8237000, 558000],
        [-8237000, 556000],
        [-8239000, 556000],
    ]
    geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}},
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            }
        ],
    }

    geom, msg = qcg._validate_geometry(geojson)
    assert geom is not None
    assert msg is not None and "ADVERTENCIA" in msg


def test_resolve_centroid_uses_largest_polygon():
    big = Polygon([(-74.2, 5.0), (-74.2, 5.2), (-74.0, 5.2), (-74.0, 5.0), (-74.2, 5.0)])
    small = Polygon([(-73.6, 5.0), (-73.6, 5.02), (-73.58, 5.02), (-73.58, 5.0), (-73.6, 5.0)])
    geom = MultiPolygon([small, big])

    # Centroide entrante en polígono pequeño: debe recalcular a polígono grande
    lat, lon = qcg._resolve_centroid(geom, {"centroid": [-73.59, 5.01]})
    assert -74.2 <= lon <= -74.0
    assert 5.0 <= lat <= 5.2


def test_replace_geometry_in_geojson_normalizes_to_single_feature():
    g = Polygon([(-74.0, 5.0), (-74.0, 5.1), (-73.9, 5.1), (-73.9, 5.0), (-74.0, 5.0)])
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"farm_id": "A"}, "geometry": {"type": "Point", "coordinates": [-74, 5]}},
            {"type": "Feature", "properties": {"farm_id": "A"}, "geometry": {"type": "Point", "coordinates": [-74, 5]}},
        ],
    }

    out = qcg._replace_geometry_in_geojson(geojson, g)
    assert out["type"] == "FeatureCollection"
    assert len(out["features"]) == 1
    assert out["crs"]["properties"]["name"].endswith("4326")


def test_procesar_writes_validated_output(monkeypatch, tmp_path):
    in_dir = tmp_path / "input" / "Colacteos"
    out_dir = tmp_path / "output"
    in_dir.mkdir(parents=True)

    geojson_path = in_dir / "FARM_ID_GF-1.geojson"
    raw = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "farm_id": "GF-1",
                    "farm_name": "Finca",
                    "farm_code": "SIT-1",
                    "centroid": [-74.0, 5.0],
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]],
                },
            }
        ],
    }
    geojson_path.write_text(json.dumps(raw), encoding="utf-8")

    shp = tmp_path / "adm3.shp"
    shp.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(qcg, "_load_adm3_gdf", lambda *_args, **_kwargs: ([], "dummy_col"))
    monkeypatch.setattr(qcg, "_find_adm3_code", lambda *_args, **_kwargs: "1000")

    qcg.procesar(input_dir=str(tmp_path / "input"), output_dir=str(out_dir), adm3_shp=str(shp))

    saved = out_dir / "Colacteos" / "FARM_ID_GF-1.geojson"
    assert saved.exists()
    data = json.loads(saved.read_text(encoding="utf-8"))
    props = data["features"][0]["properties"]
    assert props["adm3_code"] == "1000"
    assert "latitude" in props and "longitude" in props and "farm_ha" in props


def test_validate_geometry_empty_returns_error():
    geojson = {"type": "FeatureCollection", "features": []}
    geom, msg = qcg._validate_geometry(geojson)
    assert geom is None
    assert "Geometría vacía" in msg


def test_validate_geometry_invalid_crs_returns_error_message():
    # Coordenadas fuera de WGS84 pero sin pista suficiente para detectar CRS.
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[500, 500], [500, 501], [501, 501], [501, 500], [500, 500]]],
                },
            }
        ],
    }
    geom, msg = qcg._validate_geometry(geojson)
    assert geom is None
    assert "No se pudo determinar el CRS" in msg


def test_resolve_centroid_outside_polygon_recomputes():
    geom = Polygon([(-74.2, 5.0), (-74.2, 5.2), (-74.0, 5.2), (-74.0, 5.0), (-74.2, 5.0)])
    lat, lon = qcg._resolve_centroid(geom, {"centroid": [-80.0, 0.0]})
    assert -74.2 <= lon <= -74.0
    assert 5.0 <= lat <= 5.2


def test_procesar_captures_file_error_and_writes_error_csv(monkeypatch, tmp_path):
    in_dir = tmp_path / "input" / "Colacteos"
    out_dir = tmp_path / "output"
    in_dir.mkdir(parents=True)
    (in_dir / "FARM_ID_GF-ERR.geojson").write_text("{}", encoding="utf-8")

    shp = tmp_path / "adm3.shp"
    shp.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(qcg, "_load_adm3_gdf", lambda *_args, **_kwargs: ([], "dummy_col"))
    monkeypatch.setattr(qcg, "_load_geojson", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom parse")))

    qcg.procesar(input_dir=str(tmp_path / "input"), output_dir=str(out_dir), adm3_shp=str(shp))

    err_dir = out_dir / "_errores"
    err_files = list(err_dir.glob("errores_qc_Colacteos_*.csv"))
    assert len(err_files) == 1
    content = err_files[0].read_text(encoding="utf-8")
    assert "boom parse" in content


def test_guess_epsg_from_bounds_variants():
    assert qcg._guess_epsg_from_bounds((-9_000_000, -1_000_000, 9_000_000, 1_000_000)) == 3857
    assert qcg._guess_epsg_from_bounds((400_000, 500_000, 800_000, 1_500_000)) == 3116


def test_load_adm3_gdf_without_crs_raises(monkeypatch):
    gdf = gpd.GeoDataFrame(
        {"ext_id": ["1000"], "geometry": [Polygon([(-74, 5), (-74, 5.1), (-73.9, 5.1), (-73.9, 5), (-74, 5)])]},
        crs=None,
    )
    monkeypatch.setattr(qcg.gpd, "read_file", lambda *_args, **_kwargs: gdf)
    try:
        qcg._load_adm3_gdf("dummy.shp")
        assert False, "Debió lanzar ValueError"
    except ValueError as e:
        assert "sin CRS" in str(e)


def test_load_adm3_gdf_without_code_column_raises(monkeypatch):
    gdf = gpd.GeoDataFrame({"otro": [1], "geometry": [Polygon([(-74, 5), (-74, 5.1), (-73.9, 5.1), (-73.9, 5), (-74, 5)])]}, crs="EPSG:4326")
    monkeypatch.setattr(qcg.gpd, "read_file", lambda *_args, **_kwargs: gdf)
    try:
        qcg._load_adm3_gdf("dummy.shp")
        assert False, "Debió lanzar ValueError"
    except ValueError as e:
        assert "No se encontró columna" in str(e)


def test_find_adm3_code_fallback_to_intersects(monkeypatch):
    geom = Polygon([(-74, 5), (-74, 5.1), (-73.9, 5.1), (-73.9, 5), (-74, 5)])
    adm3 = gpd.GeoDataFrame(
        {"ext_id": ["1000"], "geometry": [Polygon([(-75, 4), (-75, 6), (-73, 6), (-73, 4), (-75, 4)])]},
        crs="EPSG:4326",
    )

    calls = {"n": 0}

    def fake_sjoin(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first join fails")
        return pd.DataFrame({"ext_id": ["1000"]})

    monkeypatch.setattr(qcg.gpd, "sjoin", fake_sjoin)
    assert qcg._find_adm3_code(geom, adm3, "ext_id") == "1000"


def test_area_hectares_multipolygon_positive():
    p1 = Polygon([(-74.0, 5.0), (-74.0, 5.01), (-73.99, 5.01), (-73.99, 5.0), (-74.0, 5.0)])
    p2 = Polygon([(-74.02, 5.0), (-74.02, 5.01), (-74.01, 5.01), (-74.01, 5.0), (-74.02, 5.0)])
    area = qcg._area_hectares(MultiPolygon([p1, p2]))
    assert area > 0


def test_quality_control_geofarmer_main_entrypoint_raises_by_missing_args():
    with pytest.raises(ValueError, match="Se requiere input_dir"):
        runpy.run_module("quality_control_coordinates.quality_control_geofarmer", run_name="__main__")


def test_detect_crs_from_geojson_malformed_returns_none():
    geojson = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG-UNKNOWN"}}}
    assert qcg._detect_crs_from_geojson(geojson) is None


def test_validate_geometry_reports_reprojection_failure(monkeypatch):
    coords = [[-8239000, 556000], [-8239000, 558000], [-8237000, 558000], [-8237000, 556000], [-8239000, 556000]]
    geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}},
        "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [coords]}}],
    }

    monkeypatch.setattr(qcg, "_transform_geom_to_4326", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom reproj")))
    geom, msg = qcg._validate_geometry(geojson)
    assert geom is None
    assert "Falló reproyección" in msg


def test_procesar_handles_input_without_subfolders(monkeypatch, tmp_path):
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    in_dir.mkdir(parents=True)

    geojson_path = in_dir / "FARM_ID_GF-ROOT.geojson"
    raw = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"farm_id": "GF-ROOT", "farm_name": "Finca", "farm_code": "SIT-ROOT", "centroid": [-74.0, 5.0]},
                "geometry": {"type": "Polygon", "coordinates": [[[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]]},
            }
        ],
    }
    geojson_path.write_text(json.dumps(raw), encoding="utf-8")

    shp = tmp_path / "adm3.shp"
    shp.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(qcg, "_load_adm3_gdf", lambda *_args, **_kwargs: ([], "dummy_col"))
    monkeypatch.setattr(qcg, "_find_adm3_code", lambda *_args, **_kwargs: "1000")

    qcg.procesar(input_dir=str(in_dir), output_dir=str(out_dir), adm3_shp=str(shp))
    saved = out_dir / "_sin_empresa" / "FARM_ID_GF-ROOT.geojson"
    assert saved.exists()


def test_procesar_records_error_when_adm3_not_found(monkeypatch, tmp_path):
    in_dir = tmp_path / "input" / "Colacteos"
    out_dir = tmp_path / "output"
    in_dir.mkdir(parents=True)

    geojson_path = in_dir / "FARM_ID_GF-NOADM3.geojson"
    raw = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"farm_id": "GF-NOADM3", "farm_name": "Finca", "farm_code": "SIT-X", "centroid": [-74.0, 5.0]},
                "geometry": {"type": "Polygon", "coordinates": [[[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]]},
            }
        ],
    }
    geojson_path.write_text(json.dumps(raw), encoding="utf-8")

    shp = tmp_path / "adm3.shp"
    shp.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(qcg, "_load_adm3_gdf", lambda *_args, **_kwargs: ([], "dummy_col"))
    monkeypatch.setattr(qcg, "_find_adm3_code", lambda *_args, **_kwargs: None)

    qcg.procesar(input_dir=str(tmp_path / "input"), output_dir=str(out_dir), adm3_shp=str(shp))

    err_files = list((out_dir / "_errores").glob("errores_qc_Colacteos_*.csv"))
    assert len(err_files) == 1
    assert "No se encontró ADM3" in err_files[0].read_text(encoding="utf-8")


def test_extract_polygon_parts_with_geometry_collection():
    poly = Polygon([(-74.0, 5.0), (-74.0, 5.1), (-73.9, 5.1), (-73.9, 5.0), (-74.0, 5.0)])
    gc = GeometryCollection([Point(-74.0, 5.0), poly])
    parts = qcg._extract_polygon_parts(gc)
    assert len(parts) == 1
    assert parts[0].geom_type == "Polygon"


def test_largest_polygon_none_when_non_polygon_geometry():
    assert qcg._largest_polygon(Point(-74.0, 5.0)) is None


def test_resolve_centroid_with_invalid_centroid_payload_falls_back():
    geom = Polygon([(-74.2, 5.0), (-74.2, 5.2), (-74.0, 5.2), (-74.0, 5.0), (-74.2, 5.0)])
    lat, lon = qcg._resolve_centroid(geom, {"centroid": ["bad", "value"]})
    assert -74.2 <= lon <= -74.0
    assert 5.0 <= lat <= 5.2


def test_area_hectares_zero_for_empty_geometry():
    empty_poly = Polygon()
    assert qcg._area_hectares(empty_poly) == 0.0


def test_replace_geometry_with_feature_input_keeps_properties():
    geom = Polygon([(-74.0, 5.0), (-74.0, 5.1), (-73.9, 5.1), (-73.9, 5.0), (-74.0, 5.0)])
    feature = {
        "type": "Feature",
        "properties": {"farm_id": "GF-1", "farm_code": "SIT-1"},
        "geometry": {"type": "Point", "coordinates": [-74.0, 5.0]},
    }
    out = qcg._replace_geometry_in_geojson(feature, geom)
    assert out["type"] == "FeatureCollection"
    assert out["features"][0]["properties"]["farm_id"] == "GF-1"


def test_set_enriched_properties_with_feature_input():
    feature = {
        "type": "Feature",
        "properties": {"farm_id": "GF-2"},
        "geometry": {"type": "Point", "coordinates": [-74.0, 5.0]},
    }
    out = qcg._set_enriched_properties(feature, "1000", 5.0, -74.0, 12.34567)
    assert out["properties"]["adm3_code"] == "1000"
    assert out["properties"]["farm_ha"] == 12.3457


def test_find_adm3_code_returns_none_when_all_joins_fail(monkeypatch):
    geom = Polygon([(-74, 5), (-74, 5.1), (-73.9, 5.1), (-73.9, 5), (-74, 5)])
    adm3 = gpd.GeoDataFrame(
        {"ext_id": ["1000"], "geometry": [Polygon([(-75, 4), (-75, 6), (-73, 6), (-73, 4), (-75, 4)])]},
        crs="EPSG:4326",
    )

    monkeypatch.setattr(qcg.gpd, "sjoin", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("join fail")))
    assert qcg._find_adm3_code(geom, adm3, "ext_id") is None


def test_procesar_counts_crs_invalido_branch(monkeypatch, tmp_path):
    in_dir = tmp_path / "input" / "Colacteos"
    out_dir = tmp_path / "output"
    in_dir.mkdir(parents=True)
    (in_dir / "FARM_ID_GF-CRS.geojson").write_text("{}", encoding="utf-8")
    shp = tmp_path / "adm3.shp"
    shp.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(qcg, "_load_adm3_gdf", lambda *_args, **_kwargs: ([], "dummy_col"))
    monkeypatch.setattr(
        qcg,
        "_load_geojson",
        lambda *_args, **_kwargs: {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"farm_id": "GF-CRS", "farm_code": "SIT-CRS"}, "geometry": None}],
        },
    )
    monkeypatch.setattr(qcg, "_validate_geometry", lambda *_args, **_kwargs: (None, "CRS inválido en coordenadas"))

    qcg.procesar(input_dir=str(tmp_path / "input"), output_dir=str(out_dir), adm3_shp=str(shp))

    err_files = list((out_dir / "_errores").glob("errores_qc_Colacteos_*.csv"))
    assert len(err_files) == 1
    content = err_files[0].read_text(encoding="utf-8")
    assert "CRS inválido" in content


def test_extract_geofarmer_code_none_when_filename_does_not_match():
    assert qcg.extract_geofarmer_code("finca.geojson") is None


def test_extract_first_properties_for_feature_and_unknown_type():
    feature = {"type": "Feature", "properties": {"farm_id": "GF-1"}}
    assert qcg._extract_first_properties(feature)["farm_id"] == "GF-1"
    assert qcg._extract_first_properties({"type": "Unknown"}) == {}


def test_filter_allowed_fields_for_single_feature():
    feature = {
        "type": "Feature",
        "properties": {
            "farm_id": "GF-1",
            "farm_code": "SIT-1",
            "no_permitido": "x",
        },
        "geometry": {"type": "Point", "coordinates": [-74.0, 5.0]},
    }
    out = qcg._filter_allowed_fields(feature)
    assert "farm_id" in out["properties"]
    assert "no_permitido" not in out["properties"]


def test_guess_epsg_from_bounds_hits_colombia_fallback_return():
    # Activa el fallback de la rama colombiana (retorno en línea de fallback).
    assert qcg._guess_epsg_from_bounds((400_000, 50_000, 1_200_000, 50_000)) == 3116


def test_validate_geometry_feature_path_and_make_valid_fallback(monkeypatch):
    poly = Polygon([(-74.0, 5.0), (-74.0, 5.1), (-73.9, 5.1), (-73.9, 5.0), (-74.0, 5.0)])
    geojson = {
        "type": "Feature",
        "properties": {},
        "geometry": mapping(poly),
    }
    monkeypatch.setattr(qcg, "make_valid", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("force")))

    geom, msg = qcg._validate_geometry(geojson)
    assert geom is not None
    assert msg is None


def test_validate_geometry_returns_error_when_reprojection_results_empty(monkeypatch):
    coords = [
        [-8239000, 556000],
        [-8239000, 558000],
        [-8237000, 558000],
        [-8237000, 556000],
        [-8239000, 556000],
    ]
    geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}},
        "features": [
            {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [coords]}}
        ],
    }
    monkeypatch.setattr(qcg, "_transform_geom_to_4326", lambda *_args, **_kwargs: Polygon())

    geom, msg = qcg._validate_geometry(geojson)
    assert geom is None
    assert "resultó en geometría vacía" in msg


def test_load_adm3_gdf_reprojects_and_picks_non_first_candidate(monkeypatch):
    class _FakeCRS:
        def __init__(self, epsg):
            self._epsg = epsg

        def to_epsg(self):
            return self._epsg

    class _FakeStr:
        def __init__(self, values):
            self._values = values

        def strip(self):
            return [str(v).strip() for v in self._values]

    class _FakeSeries:
        def __init__(self, values):
            self._values = values

        def astype(self, _dtype):
            return self

        @property
        def str(self):
            return _FakeStr(self._values)

    class _FakeGDF:
        def __init__(self):
            self.crs = _FakeCRS(3116)
            self.columns = ["ADM3", "geometry"]
            self.data = {"ADM3": [" 05304003 "]}

        def to_crs(self, epsg=None):
            self.crs = _FakeCRS(epsg)
            return self

        def __getitem__(self, key):
            return _FakeSeries(self.data[key])

        def __setitem__(self, key, value):
            self.data[key] = value

    fake_gdf = _FakeGDF()
    monkeypatch.setattr(qcg.gpd, "read_file", lambda *_args, **_kwargs: fake_gdf)

    loaded, code_col = qcg._load_adm3_gdf("dummy.shp")
    assert loaded is fake_gdf
    assert code_col == "ADM3"
    assert loaded.data["ADM3"] == ["05304003"]


def test_find_adm3_code_first_join_success(monkeypatch):
    geom = Polygon([(-74, 5), (-74, 5.1), (-73.9, 5.1), (-73.9, 5), (-74, 5)])

    class _FakeAdm3:
        def __getitem__(self, _key):
            return self

    monkeypatch.setattr(qcg.gpd, "GeoDataFrame", lambda *args, **kwargs: object())
    monkeypatch.setattr(qcg.gpd, "sjoin", lambda *_args, **_kwargs: pd.DataFrame({"ext_id": ["05304003"]}))

    assert qcg._find_adm3_code(geom, _FakeAdm3(), "ext_id") == "05304003"


def test_find_adm3_code_returns_none_when_candidates_empty(monkeypatch):
    geom = Polygon([(-74, 5), (-74, 5.1), (-73.9, 5.1), (-73.9, 5), (-74, 5)])

    class _FakeCandidates:
        empty = True

        def __getitem__(self, _key):
            return self

    class _FakeAdm3:
        def __getitem__(self, _key):
            return self

        def intersects(self, _geom):
            return _FakeCandidates()

    calls = {"n": 0}

    def _fake_sjoin(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("force first join failure")
        return pd.DataFrame({"ext_id": []})

    monkeypatch.setattr(qcg.gpd, "GeoDataFrame", lambda *args, **kwargs: object())
    monkeypatch.setattr(qcg.gpd, "sjoin", _fake_sjoin)

    assert qcg._find_adm3_code(geom, _FakeAdm3(), "ext_id") is None


def test_find_adm3_code_returns_none_after_second_join_without_value(monkeypatch):
    geom = Polygon([(-74, 5), (-74, 5.1), (-73.9, 5.1), (-73.9, 5), (-74, 5)])

    class _FakeCandidates:
        empty = False

        def __getitem__(self, _key):
            return self

    class _FakeAdm3:
        def __getitem__(self, _key):
            return self

        def intersects(self, _geom):
            return _FakeCandidates()

    calls = {"n": 0}

    def _fake_sjoin(*_args, **_kwargs):
        calls["n"] += 1
        return pd.DataFrame({"ext_id": [None]})

    monkeypatch.setattr(qcg.gpd, "GeoDataFrame", lambda *args, **kwargs: object())
    monkeypatch.setattr(qcg.gpd, "sjoin", _fake_sjoin)

    assert qcg._find_adm3_code(geom, _FakeAdm3(), "ext_id") is None


def test_find_adm3_code_returns_none_when_geom_empty():
    assert qcg._find_adm3_code(Polygon(), object(), "ext_id") is None


def test_resolve_centroid_fallback_when_no_polygon_parts():
    lat, lon = qcg._resolve_centroid(Point(-74.0, 5.0), {})
    assert lat == 5.0
    assert lon == -74.0


def test_area_hectares_subtracts_interior_holes():
    exterior = [(-74.0, 5.0), (-74.0, 5.1), (-73.9, 5.1), (-73.9, 5.0), (-74.0, 5.0)]
    interior = [(-73.98, 5.02), (-73.98, 5.08), (-73.92, 5.08), (-73.92, 5.02), (-73.98, 5.02)]
    poly_with_hole = Polygon(exterior, [interior])
    area_hole = qcg._area_hectares(poly_with_hole)
    area_full = qcg._area_hectares(Polygon(exterior))
    assert area_hole > 0
    assert area_hole < area_full


def test_procesar_raises_when_output_or_shp_args_missing(tmp_path):
    in_dir = tmp_path / "input"
    in_dir.mkdir()
    with pytest.raises(ValueError, match="output_dir"):
        qcg.procesar(input_dir=str(in_dir), output_dir=None, adm3_shp="x.shp")
    with pytest.raises(ValueError, match="adm3_shp"):
        qcg.procesar(input_dir=str(in_dir), output_dir=str(tmp_path / "out"), adm3_shp=None)


def test_procesar_raises_when_paths_do_not_exist(tmp_path):
    missing_input = tmp_path / "no_existe"
    with pytest.raises(FileNotFoundError, match="carpeta de entrada"):
        qcg.procesar(input_dir=str(missing_input), output_dir=str(tmp_path / "out"), adm3_shp="x.shp")

    in_dir = tmp_path / "input"
    in_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="shapefile ADM3"):
        qcg.procesar(input_dir=str(in_dir), output_dir=str(tmp_path / "out"), adm3_shp=str(tmp_path / "missing.shp"))


def test_procesar_uses_filename_farm_id_and_records_missing_code_and_crs_warning(monkeypatch, tmp_path):
    in_dir = tmp_path / "input" / "Colacteos"
    out_dir = tmp_path / "output"
    in_dir.mkdir(parents=True)

    geojson_path = in_dir / "FARM_ID_GF-FILENAME.geojson"
    geojson_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"farm_name": "Finca sin codigo", "farm_code": ""},
                        "geometry": {"type": "Polygon", "coordinates": [[[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    shp = tmp_path / "adm3.shp"
    shp.write_text("dummy", encoding="utf-8")

    poly = Polygon([(-74.0, 5.0), (-74.0, 5.1), (-73.9, 5.1), (-73.9, 5.0), (-74.0, 5.0)])

    monkeypatch.setattr(qcg, "_load_adm3_gdf", lambda *_args, **_kwargs: ([], "dummy_col"))
    monkeypatch.setattr(qcg, "_validate_geometry", lambda *_args, **_kwargs: (poly, "ADVERTENCIA: reproyectado"))
    monkeypatch.setattr(qcg, "_find_adm3_code", lambda *_args, **_kwargs: "1000")

    qcg.procesar(input_dir=str(tmp_path / "input"), output_dir=str(out_dir), adm3_shp=str(shp))

    saved = out_dir / "Colacteos" / "FARM_ID_GF-FILENAME.geojson"
    assert saved.exists()
    err_files = list((out_dir / "_errores").glob("errores_qc_Colacteos_*.csv"))
    assert len(err_files) == 1
    content = err_files[0].read_text(encoding="utf-8")
    assert "Sin código externo" in content
    assert "ADVERTENCIA: reproyectado" in content


def test_procesar_records_codigo_ceros_and_sin_geometria(monkeypatch, tmp_path):
    in_dir = tmp_path / "input" / "Colacteos"
    out_dir = tmp_path / "output"
    in_dir.mkdir(parents=True)

    geojson_path = in_dir / "FARM_ID_GF-ZEROS.geojson"
    geojson_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"farm_id": "GF-ZEROS", "farm_name": "Finca", "farm_code": "0000"},
                        "geometry": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    shp = tmp_path / "adm3.shp"
    shp.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(qcg, "_load_adm3_gdf", lambda *_args, **_kwargs: ([], "dummy_col"))
    monkeypatch.setattr(qcg, "_validate_geometry", lambda *_args, **_kwargs: (None, "Geometría vacía o inválida"))

    qcg.procesar(input_dir=str(tmp_path / "input"), output_dir=str(out_dir), adm3_shp=str(shp))

    err_files = list((out_dir / "_errores").glob("errores_qc_Colacteos_*.csv"))
    assert len(err_files) == 1
    content = err_files[0].read_text(encoding="utf-8")
    assert "solo ceros" in content
    assert "Geometría vacía o inválida" in content
