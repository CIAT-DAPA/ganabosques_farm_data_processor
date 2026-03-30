from save_farm import save_farm_geofarmer as sfg
from ganabosques_orm.enums.source import Source
from ganabosques_orm.enums.valuechain import ValueChain
import pytest
import argparse
import runpy
import mongoengine


def test_resolve_value_chain_and_external_source_branches():
    assert sfg._resolve_value_chain(None) is None
    assert sfg._resolve_value_chain("livestock") == ValueChain.LIVESTOCK
    assert sfg._resolve_value_chain("invalid") is None

    assert sfg._resolve_external_source("SIT_CODE", None) == Source.SIT_CODE
    assert sfg._resolve_external_source("PRODUCER_ID", None) == Source.PRODUCER_ID
    assert sfg._resolve_external_source(None, ValueChain.CACAO) == Source.PRODUCER_ID
    assert sfg._resolve_external_source(None, ValueChain.LIVESTOCK) == Source.SIT_CODE
    # Source inválido debe caer al fallback por value_chain.
    assert sfg._resolve_external_source("NO_EXISTE", ValueChain.CACAO) == Source.PRODUCER_ID


def test_extract_geofarmer_ids_and_single_id_errors():
    geojson_multi = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"farm_id": "A"}, "geometry": None},
            {"type": "Feature", "properties": {"FARM_ID": "B"}, "geometry": None},
        ],
    }
    ids = sfg.extract_geofarmer_ids(geojson_multi)
    assert ids == {"A", "B"}

    try:
        sfg.extract_single_geofarmer_id({"type": "FeatureCollection", "features": []})
        assert False, "Debió lanzar ValueError"
    except ValueError as e:
        assert "No se encontró" in str(e)

    try:
        sfg.extract_single_geofarmer_id(geojson_multi)
        assert False, "Debió lanzar ValueError"
    except ValueError as e:
        assert "múltiples" in str(e)


def test_normalize_external_code_and_same_geometry_false_cases():
    assert sfg.normalize_external_code(None) == ""
    assert sfg.normalize_external_code(" null ") == ""
    assert sfg.normalize_external_code("SIT-1") == "SIT-1"

    # Sin geometría en alguno de los lados debe retornar False
    assert sfg.same_geometry('{"type":"FeatureCollection","features":[]}', {"type": "FeatureCollection", "features": []}) is False


def test_channel_metadata_and_infer_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sfg,
        "_channels_config",
        lambda: {"Colacteos": {"VALUE_CHAIN": "livestock", "EXTERNAL_SOURCE": "SIT_CODE"}},
    )

    assert sfg._channel_metadata_from_name("colacteos")["VALUE_CHAIN"] == "livestock"
    assert sfg._channel_metadata_from_name("nope") == {}

    fp = tmp_path / "02_quality_control" / "Colacteos" / "FARM_ID_X.geojson"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text("{}", encoding="utf-8")

    assert sfg.infer_value_chain_from_filepath(str(fp)) == ValueChain.LIVESTOCK
    assert sfg.infer_external_source_from_filepath(str(fp), ValueChain.LIVESTOCK) == Source.SIT_CODE


def test_extract_first_properties_and_geometry_signature_variants():
    feature_obj = {
        "type": "Feature",
        "properties": {"farm_id": "A"},
        "geometry": {"type": "Polygon", "coordinates": [[[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]]},
    }
    assert sfg.extract_first_properties(feature_obj)["farm_id"] == "A"

    geom_only = {"type": "Polygon", "coordinates": [[[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]]}
    assert sfg.geometry_signature_from_geojson(geom_only) is not None


def test_run_validates_polygons_dir_and_handles_empty(monkeypatch, tmp_path):
    with pytest.raises(FileNotFoundError):
        sfg.run(str(tmp_path / "missing"))

    empty = tmp_path / "empty"
    empty.mkdir()

    logs = []
    monkeypatch.setattr(sfg, "log_print", lambda *_args, **_kwargs: logs.append(_args[1] if len(_args) > 1 else ""))
    sfg.run(str(empty), errors_out_dir=str(tmp_path / "out"), value_chain=ValueChain.LIVESTOCK)
    assert any("No se encontraron GeoJSONs" in m for m in logs)


def test_save_farm_geofarmer_main_entrypoint(monkeypatch, tmp_path):
    monkeypatch.setattr(mongoengine, "connect", lambda **_kwargs: None)
    polygons_dir = tmp_path / "qc"
    polygons_dir.mkdir()

    ns = argparse.Namespace(polygons_dir=str(polygons_dir), errors_dir=str(tmp_path / "err"))
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: ns)

    # Debe ejecutar entrypoint sin lanzar excepciones.
    runpy.run_module("save_farm.save_farm_geofarmer", run_name="__main__")
