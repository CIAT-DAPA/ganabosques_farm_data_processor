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

logger = logging.getLogger("Save Farm")

def process_farm_file(csv_path, geojson_folder, output_path_save, farmsource=None):
    df = pd.read_csv(csv_path, dtype=str)

    ugg_map = generar_ugg_map(df.columns)

    # Columnas a limpiar: códigos externos y administrativos
    columnas_a_limpiar = [source.value for source in Source] + ['adm1', 'adm2', 'adm3']

    for col in columnas_a_limpiar:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: x.split('.')[0] if isinstance(x, str) and x.endswith('.0') else x)

    # Convertir a enteros los campos BOV/BUF
    for col in ugg_map.keys():
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)

    errores = []
    buenos, malos = 0, 0

    farm_creados = 0
    farm_actualizados = 0
    farm_sin_cambios = 0
    farm_errores = 0

    polygon_creados = 0
    polygon_actualizados = 0
    polygon_sin_cambios = 0
    polygon_errores = 0

    adm3_dict = {adm.ext_id: adm for adm in Adm3.objects.only("id", "ext_id")}

    farm_index = {}
    for farm in Farm.objects.only("id", "ext_id", "log", "farm_source", "adm3_id"):
        for e in farm.ext_id:
            farm_index[(e.source, e.ext_code)] = farm

    polygon_index = {
        str(p.farm_id.id): p
        for p in FarmPolygons.objects.only("farm_id", "geojson", "latitude", "longitud", "farm_ha", "radio", "buffer_inputs").no_dereference()
    }

    farm_source = farm_source_csv(csv_path)

    for index, row in tqdm(df.iterrows(), total=len(df), desc=f"🐮 {os.path.basename(csv_path)}"):
        try:
            adm3_id = adm3_dict.get(row['adm3'])
            if not adm3_id:
                raise ValueError(f"No se encontró Adm3 con ID {row['adm3']}")

            ext_ids = []
            for source in Source:
                col = source.value
                if col in row and pd.notna(row[col]) and str(row[col]).strip() != "":
                    ext_ids.append(ExtIdFarm(source=source, ext_code=str(row[col]).strip()))

            codigo_ref = ext_ids[0].ext_code if ext_ids else "SIN_CODIGO"

            farm = None
            for ext in ext_ids:
                farm = farm_index.get((ext.source, ext.ext_code))
                if farm:
                    break

            if farm:
                codigos_actuales = {(e.source, e.ext_code) for e in farm.ext_id}
                nuevos_codigos = {(e.source, e.ext_code) for e in ext_ids}
                nuevos = nuevos_codigos - codigos_actuales

                if nuevos:
                    for source, code in nuevos:
                        farm.ext_id.append(ExtIdFarm(source=source, ext_code=code))
                    farm.log.updated = datetime.now()
                    farm.save()
                    farm_actualizados += 1
                    #log_print(logger, f"🔄 Farm actualizado: {codigo_ref}")
                else:
                    farm_sin_cambios += 1
                    #log_print(logger, f"✅ Farm ya existente sin cambios: {codigo_ref}")
            else:
                log = Log(enable=True, created=datetime.now(), updated=datetime.now())
                farm = Farm(
                    adm3_id=adm3_id,
                    ext_id=ext_ids,
                    farm_source=farm_source,
                    log=log
                )
                try:
                    farm.save()
                    farm_creados += 1
                except Exception as e:
                    farm_errores += 1
                    raise ValueError(f"Error guardando Farm {codigo_ref}: {e}")

                # Agregar todos los códigos al índice
                for ext in ext_ids:
                    farm_index[(ext.source, ext.ext_code)] = farm

            # Leer geojson
            geojson_path = os.path.join(geojson_folder, f"{codigo_ref}.geojson")
            if not os.path.isfile(geojson_path):
                polygon_errores += 1
                raise ValueError(f"GeoJSON no encontrado para {codigo_ref}")

            with open(geojson_path, encoding='utf-8') as geo_file:
                geojson = json.load(geo_file)

            if not isinstance(geojson, dict):
                polygon_errores += 1
                raise ValueError(f"GeoJSON inválido para {codigo_ref}")

            # Construir buffer_inputs
            buffer_inputs = [
                BufferPolygon(ugg=ugg_enum, species=species_enum, amount=row[col])
                for col, (ugg_enum, species_enum) in ugg_map.items()
                if row[col] > 0
            ]

            polygon_existente = polygon_index.get(str(farm.id))

            geojson_str = json.dumps(geojson)
            lat = float(row['LATITUD'])
            lon = float(row['LONGITUD'])
            ha = float(row['hectareas'])
            radio = float(row['radio'])

            if polygon_existente:
                needs_update = (
                    polygon_existente.geojson != geojson_str or
                    polygon_existente.latitude != lat or
                    polygon_existente.longitud != lon or
                    polygon_existente.farm_ha != ha or
                    polygon_existente.radio != radio or
                    not buffers_iguales(polygon_existente.buffer_inputs, buffer_inputs)
                )

                if needs_update:
                    polygon_existente.geojson = geojson_str
                    polygon_existente.latitude = lat
                    polygon_existente.longitud = lon
                    polygon_existente.farm_ha = ha
                    polygon_existente.radio = radio
                    polygon_existente.buffer_inputs = buffer_inputs
                    polygon_existente.log.updated = datetime.now()
                    polygon_existente.save()
                    polygon_actualizados += 1
                    #log_print(logger, f"🔄 Polígono actualizado para Farm {codigo_ref}")
                else:
                    polygon_sin_cambios += 1
                    #log_print(logger, f"✅ Polígono ya existente sin cambios para Farm {codigo_ref}")
            else:
                try:
                    log = Log(enable=True, created=datetime.now(), updated=datetime.now())
                    polygon = FarmPolygons(
                        farm_id=farm,
                        geojson=geojson_str,
                        latitude=lat,
                        longitud=lon,
                        farm_ha=ha,
                        radio=radio,
                        buffer_inputs=buffer_inputs,
                        log=log
                    )
                    polygon.save()
                    polygon_index[str(farm.id)] = polygon
                    polygon_creados += 1
                    #log_print(logger, f"🆕 Polígono creado para Farm {codigo_ref}")
                except Exception as e:
                    polygon_errores += 1
                    raise ValueError(f"Error guardando FarmPolygon para {codigo_ref}: {e}")            
            buenos += 1

        except Exception as e:
            error_msg = str(e)
            errores.append({**row.to_dict(), "fila_original": index+2, "error": error_msg})
            print(error_msg)
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
    log_print(logger, "📊 Resumen:")
    print(errores)
    log_print(logger, f"Farms  ➕ {farm_creados} creados, 🔄 {farm_actualizados} actualizados, ✅ {farm_sin_cambios} sin cambios, ❌ {farm_errores} con error")
    log_print(logger, f"Polys  ➕ {polygon_creados} creados, 🔄 {polygon_actualizados} actualizados, ✅ {polygon_sin_cambios} sin cambios, ❌ {polygon_errores} con error")

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

def generar_ugg_map(df_columns):
    ugg_map = {}
    for grupo in UGG:
        for especie in Species:
            key = f"{grupo.name}_{especie.name}" 
            if key in df_columns:
                ugg_map[key] = (grupo, especie)
    return ugg_map

def farm_source_csv(csv_path):
    nombre_archivo = os.path.basename(csv_path).lower()
    for fuente in FarmSource:
        if fuente.name.lower() in nombre_archivo:
            return fuente
    #raise ValueError(f"No se pudo determinar el FarmSource desde el nombre del archivo: {nombre_archivo}")

def buffers_iguales(buf1, buf2):
    def serialize(buff):
        return sorted([
            (b.ugg.name, b.species.name, b.amount)
            for b in buff
        ])
    return serialize(buf1) == serialize(buf2)