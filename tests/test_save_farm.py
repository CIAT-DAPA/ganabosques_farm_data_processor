import os
import json
import importlib
import pandas as pd
import tempfile
from datetime import datetime
import glob
import pytest

from ganabosques_orm.collections.adm3 import Adm3
from ganabosques_orm.collections.farm import Farm
from ganabosques_orm.collections.farmpolygons import FarmPolygons
from ganabosques_orm.auxiliaries.bufferpolygon import BufferPolygon
from ganabosques_orm.enums.source import Source
from ganabosques_orm.enums.ugg import UGG
from ganabosques_orm.enums.species import Species
from ganabosques_orm.enums.valuechain import ValueChain
from ganabosques_orm.auxiliaries.log import Log


from save_farm.save_farm import process_farm_file, generar_ugg_map, buffers_iguales, save_farm, farm_source_csv

def create_csv_and_geojson(temp_dir, adm3_code="12345", sit_code="SIT999", ugg_col="TERNEROS_MENORES_1_ANIO_BOVINOS", ugg_val="3"):
    csv_path = os.path.join(temp_dir, "SAGARI_test.csv")
    geojson_folder = os.path.join(temp_dir, "buffers")
    os.makedirs(geojson_folder, exist_ok=True)

    df = pd.DataFrame([{
        "adm3": adm3_code,
        "SIT_CODE": sit_code,
        "LATITUD": "5.0",
        "LONGITUD": "-74.0",
        "hectareas": "50",
        "radio": "120",
        ugg_col: ugg_val
    }])
    df.to_csv(csv_path, index=False)

    geojson = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
    }
    with open(os.path.join(geojson_folder, f"{sit_code}.geojson"), "w") as f:
        json.dump(geojson, f)

    return csv_path, geojson_folder

def test_create_farm_and_polygon():
    # Precondiciones
    Adm3(ext_id="12345").save()
    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path, geojson_folder = create_csv_and_geojson(temp_dir)
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        process_farm_file(csv_path, geojson_folder, output_dir)

        farm = Farm.objects.first()
        polygon = FarmPolygons.objects.first()

        assert farm is not None
        assert polygon is not None
        assert farm.ext_id[0].source == Source.SIT_CODE
        assert polygon.farm_id == farm
        assert polygon.buffer_inputs[0].ugg == UGG.TERNEROS_MENORES_1_ANIO
        assert polygon.buffer_inputs[0].species == Species.BOVINOS
        assert polygon.buffer_inputs[0].amount == 3

def test_adm3_inexistente():
    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path, geojson_folder = create_csv_and_geojson(temp_dir, adm3_code="NO_EXISTE")
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        process_farm_file(csv_path, geojson_folder, output_dir)

        # Buscar el archivo de errores generado
        error_files = glob.glob(os.path.join(output_dir, "*_errores_*.csv"))
        assert len(error_files) == 1, "No se generó archivo de errores"
        
        df_errores = pd.read_csv(error_files[0])
        assert "No se encontró Adm3 con ID NO_EXISTE" in df_errores["error"].values

def test_not_create_duplicate_polygon():
    Adm3(ext_id="12345").save()
    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path, geojson_folder = create_csv_and_geojson(temp_dir)
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        # Primera ejecución: crea
        process_farm_file(csv_path, geojson_folder, output_dir)
        assert Farm.objects.count() == 1
        assert FarmPolygons.objects.count() == 1

        # Segunda ejecución: no debe duplicar
        process_farm_file(csv_path, geojson_folder, output_dir)
        assert Farm.objects.count() == 1
        assert FarmPolygons.objects.count() == 1

def test_not_create_duplicate_farm():
    Adm3(ext_id="12345").save()
    # Creamos manualmente un farm existente con el código SIT_CODE
    existing_farm = Farm(
        adm3_id=Adm3.objects.first(),
        ext_id=[{"source": "SIT_CODE", "ext_code": "SIT999"}],
        farm_source="SAGARI"
    )
    existing_farm.save()

    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path, geojson_folder = create_csv_and_geojson(temp_dir, sit_code="SIT999")
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        process_farm_file(csv_path, geojson_folder, output_dir)

        # Asegurarse de que solo hay un Farm y un Polygon
        assert Farm.objects.count() == 1
        assert FarmPolygons.objects.count() == 1

def test_updates_farm_with_new_external_code():
    Adm3(ext_id="12345").save()
    # Farm inicial solo con SIT_CODE
    farm = Farm(
        adm3_id=Adm3.objects.first(),
        ext_id=[{"source": "SIT_CODE", "ext_code": "SIT999"}],
        farm_source="SAGARI",
        log = Log(enable=True, created=datetime.now(), updated=datetime.now())
    )
    farm.save()

    with tempfile.TemporaryDirectory() as temp_dir:
        # Crear CSV con mismo SIT_CODE
        csv_path, geojson_folder = create_csv_and_geojson(temp_dir, sit_code="SIT999")
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        # Simulamos que se agrega un nuevo código externo (e.g., GEOFARMER_ID)
        df = pd.read_csv(csv_path)
        df["GEOFARMER_ID"] = "GF123"
        df.to_csv(csv_path, index=False)

        process_farm_file(csv_path, geojson_folder, output_dir)

        updated_farm = Farm.objects.first()
        ext_sources = [e["source"] for e in updated_farm.ext_id]

        assert Source.SIT_CODE in ext_sources
        assert Source.GEOFARMER_ID in ext_sources
        assert Farm.objects.count() == 1


def test_process_farm_file_updates_existing_polygon_when_data_changes():
    Adm3(ext_id="12345").save()
    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path, geojson_folder = create_csv_and_geojson(temp_dir)
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        process_farm_file(csv_path, geojson_folder, output_dir)
        original_polygon = FarmPolygons.objects.first()
        original_updated = original_polygon.log.updated

        # Cambiamos datos para forzar rama de actualización de polígono existente.
        df = pd.read_csv(csv_path)
        df["LATITUD"] = "6.5"
        df.to_csv(csv_path, index=False)

        process_farm_file(csv_path, geojson_folder, output_dir)

        assert Farm.objects.count() == 1
        assert FarmPolygons.objects.count() == 1

        refreshed_polygon = FarmPolygons.objects.first()
        assert refreshed_polygon.latitude == 6.5
        assert refreshed_polygon.log.updated >= original_updated

def test_generate_ugg_map():
    columns = ["TERNEROS_MENORES_1_ANIO_BOVINOS"]
    result = generar_ugg_map(columns)
    assert isinstance(result, dict)
    assert "TERNEROS_MENORES_1_ANIO_BOVINOS" in result

def test_buffers_equal_true():
    buff1 = [BufferPolygon(ugg=UGG.HEMBRAS_MACHOS_1_2_ANIOS, species=Species.BOVINOS, amount=5)]
    buff2 = [BufferPolygon(ugg=UGG.HEMBRAS_MACHOS_1_2_ANIOS, species=Species.BOVINOS, amount=5)]
    assert buffers_iguales(buff1, buff2)

def test_buffers_equal_false():
    buff1 = [BufferPolygon(ugg=UGG.HEMBRAS_MACHOS_1_2_ANIOS, species=Species.BOVINOS, amount=5)]
    buff2 = [BufferPolygon(ugg=UGG.HEMBRAS_MACHOS_1_2_ANIOS, species=Species.BOVINOS, amount=10)]
    assert not buffers_iguales(buff1, buff2)


def test_process_farm_file_geojson_missing_creates_error_csv():
    Adm3(ext_id="12345").save()
    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path, geojson_folder = create_csv_and_geojson(temp_dir)
        os.remove(os.path.join(geojson_folder, "SIT999.geojson"))
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        process_farm_file(csv_path, geojson_folder, output_dir)

        error_files = glob.glob(os.path.join(output_dir, "*_errores_*.csv"))
        assert len(error_files) == 1
        df_errores = pd.read_csv(error_files[0])
        assert df_errores["error"].str.contains("GeoJSON no encontrado").any()


def test_process_farm_file_geojson_invalid_type_creates_error_csv():
    Adm3(ext_id="12345").save()
    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path, geojson_folder = create_csv_and_geojson(temp_dir)
        with open(os.path.join(geojson_folder, "SIT999.geojson"), "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)

        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        process_farm_file(csv_path, geojson_folder, output_dir)

        error_files = glob.glob(os.path.join(output_dir, "*_errores_*.csv"))
        assert len(error_files) == 1
        df_errores = pd.read_csv(error_files[0])
        assert df_errores["error"].str.contains("GeoJSON inválido").any()


def test_save_farm_validates_input_paths(tmp_path):
    with pytest.raises(Exception, match="no es un directorio"):
        save_farm(str(tmp_path / "missing"), str(tmp_path / "out"), None)

    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    with pytest.raises(Exception, match="No se encuentra la carpeta de geojsons"):
        save_farm(str(csv_dir), str(tmp_path / "out"), None)


def test_save_farm_processes_only_csv_and_passes_value_chain(tmp_path, monkeypatch):
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    (csv_dir / "buffers").mkdir()

    (csv_dir / "SAGARI_a.csv").write_text("x", encoding="utf-8")
    (csv_dir / "README.txt").write_text("x", encoding="utf-8")

    calls = []

    def fake_process(csv_path, geojson_folder, output_path_save, farmsource=None, value_chain=None):
        calls.append({
            "csv_path": csv_path,
            "geojson_folder": geojson_folder,
            "output_path_save": output_path_save,
            "farmsource": farmsource,
            "value_chain": value_chain,
        })

    sf_module = importlib.import_module("save_farm.save_farm")
    monkeypatch.setattr(sf_module, "process_farm_file", fake_process)

    save_farm(str(csv_dir), str(tmp_path / "out"), ValueChain.LIVESTOCK)

    assert len(calls) == 1
    assert calls[0]["csv_path"].endswith("SAGARI_a.csv")
    assert calls[0]["farmsource"] is None
    assert calls[0]["value_chain"] == ValueChain.LIVESTOCK


def test_farm_source_csv_returns_none_when_no_match(tmp_path):
    p = tmp_path / "archivo_desconocido.csv"
    p.write_text("x", encoding="utf-8")
    assert farm_source_csv(str(p)) is None


def test_process_farm_file_handles_farm_save_exception(monkeypatch):
    Adm3(ext_id="12345").save()
    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path, geojson_folder = create_csv_and_geojson(temp_dir)
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        original_save = Farm.save

        def boom_save(self, *args, **kwargs):
            raise RuntimeError("farm-save")

        monkeypatch.setattr(Farm, "save", boom_save)
        process_farm_file(csv_path, geojson_folder, output_dir)

        monkeypatch.setattr(Farm, "save", original_save)

        error_files = glob.glob(os.path.join(output_dir, "*_errores_*.csv"))
        assert len(error_files) == 1
        df_errores = pd.read_csv(error_files[0])
        assert df_errores["error"].str.contains("Error guardando Farm").any()


def test_process_farm_file_handles_polygon_save_exception(monkeypatch):
    Adm3(ext_id="12345").save()
    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path, geojson_folder = create_csv_and_geojson(temp_dir)
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        original_poly_save = FarmPolygons.save

        def boom_poly(self, *args, **kwargs):
            raise RuntimeError("poly-save")

        monkeypatch.setattr(FarmPolygons, "save", boom_poly)
        process_farm_file(csv_path, geojson_folder, output_dir)

        monkeypatch.setattr(FarmPolygons, "save", original_poly_save)

        error_files = glob.glob(os.path.join(output_dir, "*_errores_*.csv"))
        assert len(error_files) == 1
        df_errores = pd.read_csv(error_files[0])
        assert df_errores["error"].str.contains("Error guardando FarmPolygon").any()
