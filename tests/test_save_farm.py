import os
import json
import pandas as pd
import tempfile
from datetime import datetime
import glob

from ganabosques_orm.collections.adm3 import Adm3
from ganabosques_orm.collections.farm import Farm
from ganabosques_orm.collections.farmpolygons import FarmPolygons
from ganabosques_orm.auxiliaries.bufferpolygon import BufferPolygon
from ganabosques_orm.enums.source import Source
from ganabosques_orm.enums.ugg import UGG
from ganabosques_orm.enums.species import Species
from ganabosques_orm.auxiliaries.log import Log


from save_farm.save_farm import process_farm_file, generar_ugg_map, buffers_iguales

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

def test_no_crea_polygon_duplicado():
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

def test_no_crear_farm_duplicado():
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

def test_actualizar_farm_con_nuevo_codigo_ext():
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

        print(df)

        process_farm_file(csv_path, geojson_folder, output_dir)

        updated_farm = Farm.objects.first()
        ext_sources = [e["source"] for e in updated_farm.ext_id]

        print(ext_sources)

        assert Source.SIT_CODE in ext_sources
        assert Source.GEOFARMER_ID in ext_sources
        assert Farm.objects.count() == 1

def test_generar_ugg_map():
    columns = ["TERNEROS_MENORES_1_ANIO_BOVINOS"]
    result = generar_ugg_map(columns)
    assert isinstance(result, dict)
    assert "TERNEROS_MENORES_1_ANIO_BOVINOS" in result

def test_buffers_iguales_true():
    buff1 = [BufferPolygon(ugg=UGG.HEMBRAS_MACHOS_1_2_ANIOS, species=Species.BOVINOS, amount=5)]
    buff2 = [BufferPolygon(ugg=UGG.HEMBRAS_MACHOS_1_2_ANIOS, species=Species.BOVINOS, amount=5)]
    assert buffers_iguales(buff1, buff2)

def test_buffers_iguales_false():
    buff1 = [BufferPolygon(ugg=UGG.HEMBRAS_MACHOS_1_2_ANIOS, species=Species.BOVINOS, amount=5)]
    buff2 = [BufferPolygon(ugg=UGG.HEMBRAS_MACHOS_1_2_ANIOS, species=Species.BOVINOS, amount=10)]
    assert not buffers_iguales(buff1, buff2)
