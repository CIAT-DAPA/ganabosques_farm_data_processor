import json
from pathlib import Path

from ganabosques_orm.collections.adm3 import Adm3
from ganabosques_orm.collections.farm import Farm
from ganabosques_orm.collections.farmpolygons import FarmPolygons
from ganabosques_orm.auxiliaries.extidfarm import ExtIdFarm
from ganabosques_orm.enums.farmsource import FarmSource
from ganabosques_orm.enums.source import Source
from ganabosques_orm.enums.valuechain import ValueChain

from save_farm.save_farm_geofarmer import run, upsert_one


def _make_stats():
    return {
        "farm_inserts": 0,
        "farm_updates": 0,
        "farm_no_changes": 0,
        "poly_inserts": 0,
        "poly_updates": 0,
        "poly_deactivated": 0,
        "poly_no_changes": 0,
        "warnings": 0,
    }


def _write_geojson(path: Path, farm_id: str, farm_code: str | None, coords):
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "farm_id": farm_id,
                    "farm_code": farm_code,
                    "adm3_code": "1000",
                    "latitude": 5.0,
                    "longitude": -74.0,
                    "farm_ha": 10.5,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [coords],
                },
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _active_polygons_for_farm(farm):
    return FarmPolygons.objects(farm_id=farm, log__enable=True)


def test_no_farm_code_does_not_merge_different_geofarmer_ids(tmp_path):
    Adm3(ext_id="1000").save()

    f1 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-1.geojson"
    f2 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-2.geojson"

    coords = [[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]
    _write_geojson(f1, farm_id="GF-1", farm_code=None, coords=coords)
    _write_geojson(f2, farm_id="GF-2", farm_code=None, coords=coords)

    errores, report_rows, stats = [], [], _make_stats()

    ok1, _ = upsert_one(str(f1), errores, report_rows, stats, value_chain=None)
    ok2, _ = upsert_one(str(f2), errores, report_rows, stats, value_chain=None)

    assert ok1 and ok2
    assert Farm.objects.count() == 2

    gf_ids = []
    for farm in Farm.objects:
        for ext in farm.ext_id:
            if ext.source == Source.GEOFARMER_ID:
                gf_ids.append(ext.ext_code)
    assert sorted(gf_ids) == ["GF-1", "GF-2"]


def test_external_code_collision_creates_new_farm_and_warning(tmp_path):
    Adm3(ext_id="1000").save()

    f1 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-AAA.geojson"
    f2 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-BBB.geojson"

    coords = [[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]
    _write_geojson(f1, farm_id="GF-AAA", farm_code="SIT-001", coords=coords)
    _write_geojson(f2, farm_id="GF-BBB", farm_code="SIT-001", coords=coords)

    errores, report_rows, stats = [], [], _make_stats()

    ok1, _ = upsert_one(str(f1), errores, report_rows, stats, value_chain=None)
    ok2, _ = upsert_one(str(f2), errores, report_rows, stats, value_chain=None)

    assert ok1 and ok2
    assert Farm.objects.count() == 2
    assert any(r.get("tipo") == "advertencia" and "Colisión" in r.get("detalle", "") for r in report_rows)


def test_same_geometry_keeps_active_polygon_without_new_version(tmp_path):
    Adm3(ext_id="1000").save()

    f1 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-100.geojson"

    coords = [[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]
    _write_geojson(f1, farm_id="GF-100", farm_code="SIT-100", coords=coords)

    errores, report_rows, stats = [], [], _make_stats()

    ok1, _ = upsert_one(str(f1), errores, report_rows, stats, value_chain=None)
    ok2, _ = upsert_one(str(f1), errores, report_rows, stats, value_chain=None)

    assert ok1 and ok2
    assert Farm.objects.count() == 1
    assert FarmPolygons.objects.count() == 1
    farm = Farm.objects.first()
    assert _active_polygons_for_farm(farm).count() == 1
    assert stats["poly_no_changes"] == 1


def test_different_geometry_versions_polygon_and_disables_previous(tmp_path):
    Adm3(ext_id="1000").save()

    f1 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-200.geojson"

    coords_v1 = [[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]
    coords_v2 = [[-74.0, 5.0], [-74.0, 5.2], [-73.8, 5.2], [-73.8, 5.0], [-74.0, 5.0]]

    _write_geojson(f1, farm_id="GF-200", farm_code="SIT-200", coords=coords_v1)

    errores, report_rows, stats = [], [], _make_stats()

    ok1, _ = upsert_one(str(f1), errores, report_rows, stats, value_chain=None)
    assert ok1

    _write_geojson(f1, farm_id="GF-200", farm_code="SIT-200", coords=coords_v2)
    ok2, _ = upsert_one(str(f1), errores, report_rows, stats, value_chain=None)
    assert ok2

    farm = Farm.objects.first()
    assert Farm.objects.count() == 1
    assert FarmPolygons.objects.count() == 2
    assert _active_polygons_for_farm(farm).count() == 1
    assert stats["poly_deactivated"] == 1
    assert stats["poly_updates"] == 1


def test_geofarmer_match_prioritized_over_external_collision_keeps_processing(tmp_path):
    Adm3(ext_id="1000").save()

    # Farm A: tendrá el GEOFARMER_ID objetivo
    farm_a = Farm(
        adm3_id=Adm3.objects.first(),
        farm_source=FarmSource.GEOFARMER,
        value_chain=ValueChain.LIVESTOCK,
        ext_id=[ExtIdFarm(source=Source.GEOFARMER_ID, ext_code="GF-X")],
    )
    farm_a.save()

    # Farm B: ocupa el código externo que llegará en el archivo
    farm_b = Farm(
        adm3_id=Adm3.objects.first(),
        farm_source=FarmSource.GEOFARMER,
        value_chain=ValueChain.LIVESTOCK,
        ext_id=[ExtIdFarm(source=Source.SIT_CODE, ext_code="SIT-COLLIDE")],
    )
    farm_b.save()

    f1 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-X.geojson"
    coords = [[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]
    _write_geojson(f1, farm_id="GF-X", farm_code="SIT-COLLIDE", coords=coords)

    errores, report_rows, stats = [], [], _make_stats()
    ok, msg = upsert_one(str(f1), errores, report_rows, stats, value_chain=ValueChain.LIVESTOCK)

    assert ok
    assert "Farm" in msg
    assert Farm.objects.count() == 2
    assert len(errores) == 0
    # El código externo conflictivo no debe anexarse al farm de GF-X.
    farm_gf = Farm.objects(ext_id__match={"source": Source.GEOFARMER_ID, "ext_code": "GF-X"}).first()
    ext_pairs = {(e.source, e.ext_code) for e in farm_gf.ext_id}
    assert (Source.SIT_CODE, "SIT-COLLIDE") not in ext_pairs


def test_upsert_one_resolves_adm3_with_leading_zeros(tmp_path):
    Adm3(ext_id="05304003").save()
    f1 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-LEAD0.geojson"
    coords = [[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]

    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "farm_id": "GF-LEAD0",
                    "farm_code": "SIT-LEAD0",
                    "adm3_code": "05304003",
                    "latitude": 5.0,
                    "longitude": -74.0,
                    "farm_ha": 10.5,
                },
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            }
        ],
    }
    f1.parent.mkdir(parents=True, exist_ok=True)
    f1.write_text(json.dumps(data), encoding="utf-8")

    errores, report_rows, stats = [], [], _make_stats()
    ok, _ = upsert_one(str(f1), errores, report_rows, stats, value_chain=ValueChain.LIVESTOCK)
    assert ok
    assert Farm.objects.count() == 1


def test_upsert_one_uses_existing_farm_by_external_when_no_geofarmer_ids(tmp_path):
    Adm3(ext_id="1000").save()

    existing = Farm(
        adm3_id=Adm3.objects.first(),
        farm_source=FarmSource.GEOFARMER,
        value_chain=ValueChain.LIVESTOCK,
        ext_id=[ExtIdFarm(source=Source.SIT_CODE, ext_code="SIT-EXT")],
    )
    existing.save()

    f1 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-EXT.geojson"
    coords = [[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]
    _write_geojson(f1, farm_id="GF-EXT", farm_code="SIT-EXT", coords=coords)

    errores, report_rows, stats = [], [], _make_stats()
    ok, _ = upsert_one(str(f1), errores, report_rows, stats, value_chain=ValueChain.LIVESTOCK)
    assert ok
    assert Farm.objects.count() == 1

    updated = Farm.objects.first()
    ext_pairs = {(e.source, e.ext_code) for e in updated.ext_id}
    assert (Source.GEOFARMER_ID, "GF-EXT") in ext_pairs


def test_upsert_one_updates_existing_farm_metadata_fields(tmp_path):
    Adm3(ext_id="1000").save()
    Adm3(ext_id="2000").save()

    farm = Farm(
        adm3_id=Adm3.objects(ext_id="1000").first(),
        farm_source=FarmSource.SAGARI,
        value_chain=ValueChain.CACAO,
        ext_id=[ExtIdFarm(source=Source.GEOFARMER_ID, ext_code="GF-META")],
    )
    farm.save()

    f1 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-META.geojson"
    coords = [[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]

    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "farm_id": "GF-META",
                    "farm_code": "SIT-META",
                    "adm3_code": "2000",
                    "latitude": 5.0,
                    "longitude": -74.0,
                    "farm_ha": 10.5,
                },
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            }
        ],
    }
    f1.parent.mkdir(parents=True, exist_ok=True)
    f1.write_text(json.dumps(data), encoding="utf-8")

    errores, report_rows, stats = [], [], _make_stats()
    ok, _ = upsert_one(str(f1), errores, report_rows, stats, value_chain=ValueChain.LIVESTOCK)
    assert ok

    refreshed = Farm.objects.first()
    assert refreshed.farm_source == FarmSource.GEOFARMER
    assert refreshed.value_chain == ValueChain.LIVESTOCK
    assert str(refreshed.adm3_id.ext_id) == "2000"


def test_upsert_one_invalid_geojson_reports_error(tmp_path):
    f1 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_BAD.geojson"
    f1.parent.mkdir(parents=True, exist_ok=True)
    f1.write_text("{invalid json", encoding="utf-8")

    errores, report_rows, stats = [], [], _make_stats()
    ok, msg = upsert_one(str(f1), errores, report_rows, stats, value_chain=ValueChain.LIVESTOCK)

    assert not ok
    assert "ERROR" in msg
    assert len(errores) == 1
    assert errores[0]["tipo"] == "error"


def test_run_writes_report_csv_with_mixed_results(tmp_path):
    Adm3(ext_id="1000").save()
    qc_dir = tmp_path / "02_quality_control" / "Colacteos"
    qc_dir.mkdir(parents=True)

    good = qc_dir / "FARM_ID_GF-OK.geojson"
    bad = qc_dir / "FARM_ID_GF-BAD.geojson"

    coords = [[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]
    _write_geojson(good, farm_id="GF-OK", farm_code="SIT-OK", coords=coords)
    bad.write_text("{not-json", encoding="utf-8")

    report_dir = tmp_path / "reportes"
    run(str(tmp_path / "02_quality_control"), errors_out_dir=str(report_dir), value_chain=ValueChain.LIVESTOCK)

    files = list(report_dir.glob("reporte_geofarmer_save_*.csv"))
    assert len(files) == 1
    csv_text = files[0].read_text(encoding="utf-8")
    assert "GF-OK" in csv_text
    assert "error" in csv_text.lower()


def test_upsert_one_missing_adm3_code_reports_error(tmp_path):
    f1 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-NOADM3.geojson"
    coords = [[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "farm_id": "GF-NOADM3",
                    "farm_code": "SIT-X",
                    "latitude": 5.0,
                    "longitude": -74.0,
                    "farm_ha": 10.5,
                },
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            }
        ],
    }
    f1.parent.mkdir(parents=True, exist_ok=True)
    f1.write_text(json.dumps(data), encoding="utf-8")

    errores, report_rows, stats = [], [], _make_stats()
    ok, msg = upsert_one(str(f1), errores, report_rows, stats, value_chain=ValueChain.LIVESTOCK)
    assert not ok
    assert "adm3_code vacío" in msg


def test_upsert_one_multiple_geofarmer_ids_reports_error(tmp_path):
    Adm3(ext_id="1000").save()
    f1 = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_GF-MULTI.geojson"
    coords = [[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"farm_id": "GF-1", "farm_code": "SIT-X", "adm3_code": "1000", "latitude": 5.0, "longitude": -74.0, "farm_ha": 10.5},
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            },
            {
                "type": "Feature",
                "properties": {"farm_id": "GF-2", "farm_code": "SIT-X", "adm3_code": "1000", "latitude": 5.0, "longitude": -74.0, "farm_ha": 10.5},
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            },
        ],
    }
    f1.parent.mkdir(parents=True, exist_ok=True)
    f1.write_text(json.dumps(data), encoding="utf-8")

    errores, report_rows, stats = [], [], _make_stats()
    ok, msg = upsert_one(str(f1), errores, report_rows, stats, value_chain=ValueChain.LIVESTOCK)
    assert not ok
    assert "múltiples GEOFARMER_ID" in msg


def test_run_filters_files_by_value_chain(monkeypatch, tmp_path):
    base = tmp_path / "02_quality_control"
    c1 = base / "Colacteos"
    c2 = base / "Riqueza"
    c1.mkdir(parents=True)
    c2.mkdir(parents=True)
    f1 = c1 / "FARM_ID_A.geojson"
    f2 = c2 / "FARM_ID_B.geojson"
    f1.write_text("{}", encoding="utf-8")
    f2.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "save_farm.save_farm_geofarmer.infer_value_chain_from_filepath",
        lambda fp: ValueChain.LIVESTOCK if "Colacteos" in fp else ValueChain.CACAO,
    )

    called = []

    def fake_upsert(fp, errores, report_rows, stats, value_chain=None):
        called.append(fp)
        return True, "ok"

    monkeypatch.setattr("save_farm.save_farm_geofarmer.upsert_one", fake_upsert)
    monkeypatch.setattr("save_farm.save_farm_geofarmer.tqdm", lambda it, **_kwargs: it)

    run(str(base), errors_out_dir=str(tmp_path / "rep"), value_chain=ValueChain.LIVESTOCK)

    assert len(called) == 1
    assert "Colacteos" in called[0]
