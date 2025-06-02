import csv
import os
import json
from datetime import datetime
import pandas as pd
from tqdm import tqdm

from mongoengine import connect
from ganabosques_orm.collections.farm import Farm
from ganabosques_orm.collections.farmpolygons import FarmPolygons
from ganabosques_orm.auxiliaries.extidfarm import ExtIdFarm
from ganabosques_orm.auxiliaries.log import Log
from ganabosques_orm.auxiliaries.bufferpolygon import BufferPolygon
from ganabosques_orm.enums.farmsource import FarmSource
from ganabosques_orm.enums.source import Source
from ganabosques_orm.enums.ugg import UGG
from ganabosques_orm.enums.species import Species
from ganabosques_orm.collections.adm3 import Adm3
from config import config

import logging
from tools.log_print import log_print 


connect(db=config['MONGO_DB_NAME'], host=config['MONGO_URI'])


UGG_MAP = {
    'BOV_terneros_menores_1_anio': (UGG.TERNEROS_MENORES_1_ANIO, Species.BOVINOS),
    'BOV_hembras_machos_1_2_anios': (UGG.HEMBRAS_MACHOS_1_2_ANIOS, Species.BOVINOS),
    'BOV_hembras_2_3_anios': (UGG.HEMBRAS_MENORES_2_3_ANIOS, Species.BOVINOS),
    'BOV_machos_2_3_anios': (UGG.MACHOS_2_3_ANIOS, Species.BOVINOS),
    'BOV_hembras_mayores_3_anios': (UGG.HEMBRAS_MAYORES_3_ANIOS, Species.BOVINOS),
    'BOV_machos_mayores_3_anios': (UGG.MACHOS_MAYORES_3_ANIOS, Species.BOVINOS),
    'BUF_terneros_menores_1_anio': (UGG.TERNEROS_MENORES_1_ANIO, Species.BUFALINOS),
    'BUF_hembras_machos_1_2_anios': (UGG.HEMBRAS_MACHOS_1_2_ANIOS, Species.BUFALINOS),
    'BUF_hembras_2_3_anios': (UGG.HEMBRAS_MENORES_2_3_ANIOS, Species.BUFALINOS),
    'BUF_machos_2_3_anios': (UGG.MACHOS_2_3_ANIOS, Species.BUFALINOS),
    'BUF_hembras_mayores_3_anios': (UGG.HEMBRAS_MAYORES_3_ANIOS, Species.BUFALINOS),
    'BUF_machos_mayores_3_anios': (UGG.MACHOS_MAYORES_3_ANIOS, Species.BUFALINOS),
}

logger = logging.getLogger("Save Farm")

def process_farm_file(csv_path, geojson_folder, output_path_save):
    df = pd.read_csv(csv_path, dtype=str)

    # Limpiar códigos .0
    for col in ['CODIGO_SIT', 'ID_VEREDA', 'ID_MUNICIPIO', 'ID_DEPARTAMENTO']:
        df[col] = df[col].apply(lambda x: x.split('.')[0] if x and x.endswith('.0') else x)

    # Convertir a enteros los campos BOV/BUF
    for col in UGG_MAP.keys():
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)

    errores = []
    buenos, malos = 0, 0

    adm3_dict = {adm.ext_id: adm for adm in Adm3.objects.only("id", "ext_id")}

    for index, row in tqdm(df.iterrows(), total=len(df), desc=f"🐮 {os.path.basename(csv_path)}"):
        try:
            codigo_sit = row['CODIGO_SIT']
            adm3_id = adm3_dict.get(row['ID_VEREDA'])
            if not adm3_id:
                raise ValueError(f"No se encontró Adm3 con ID {row['ID_VEREDA']}")

            ext_id = ExtIdFarm(source=Source.SIT_CODE, ext_code=codigo_sit)
            log = Log(enable=True, created=datetime.now(), updated=datetime.now())

            farm = Farm(
                adm3_id=adm3_id,
                ext_id=[ext_id],
                farm_source=FarmSource.SAGARI,
                log=log
            )

            try:
                farm.save()
            except Exception as e:
                raise ValueError(f"Error guardando Farm {codigo_sit}: {e}")

            # Leer geojson
            geojson_path = os.path.join(geojson_folder, f"{codigo_sit}.geojson")
            if not os.path.isfile(geojson_path):
                raise ValueError(f"GeoJSON no encontrado para {codigo_sit}")

            with open(geojson_path, encoding='utf-8') as geo_file:
                geojson = json.load(geo_file)

            if not isinstance(geojson, dict):
                raise ValueError(f"GeoJSON inválido para {codigo_sit}")

            # Construir buffer_inputs
            buffer_inputs = [
                BufferPolygon(ugg=ugg_enum, species=species_enum, amount=row[col])
                for col, (ugg_enum, species_enum) in UGG_MAP.items()
                if row[col] > 0
            ]

            try:
                polygon = FarmPolygons(
                    farm_id=farm,
                    geojson=json.dumps(geojson),
                    latitude=float(row['LATITUD']),
                    longitud=float(row['LONGITUD']),
                    farm_ha=float(row['hectareas']),
                    radio=float(row['radio']),
                    buffer_inputs=buffer_inputs
                )
                polygon.save()
                buenos += 1
            except Exception as e:
                raise ValueError(f"Error guardando FarmPolygon para {codigo_sit}: {e}")

        except Exception as e:
            error_msg = str(e)
            errores.append({**row.to_dict(), "fila_original": index, "error": error_msg})
            malos += 1

    if errores:
        os.makedirs(output_path_save, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(csv_path))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{base_name}_errores_{timestamp}.csv"
        error_path = os.path.join(output_path_save, file_name)
        pd.DataFrame(errores).to_csv(error_path, index=False)
        log_print(logger, f"Errores registrados en: {error_path}")

    log_print(logger, f"✅ Finalizado: {buenos} guardados, {malos} con error.")

def save_farm(csv_folder_path, output_path_save):
    if not os.path.isdir(csv_folder_path):
        raise Exception(f"La ruta proporcionada no es un directorio: {csv_folder_path}")

    geojson_folder = os.path.join(csv_folder_path, "buffers")
    if not os.path.isdir(geojson_folder):
        raise Exception(f"No se encuentra la carpeta de geojsons: {geojson_folder}")

    for filename in os.listdir(csv_folder_path, ):
        if filename.endswith(".csv"):
            csv_path = os.path.join(csv_folder_path, filename)
            print(f"📄 Procesando archivo: {csv_path}")
            process_farm_file(csv_path, geojson_folder, output_path_save)