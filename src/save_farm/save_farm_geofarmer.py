# -*- coding: utf-8 -*-
"""
Paso 3 GEOFARMER: Guardar farms y polígonos en MongoDB.

Lee GeoJSONs validados del paso 2 (ya contienen adm3_code, latitude,
longitude, farm_ha en las properties) y los persiste en MongoDB
como documentos Farm + FarmPolygons.

El paso 2 (quality_control_geofarmer) ya se encargó de:
  - Validar geometría / CRS
  - Buscar código ADM3 vía spatial join
  - Verificar / calcular centroide
  - Calcular área en hectáreas
"""
import os
import json
import logging
from datetime import datetime
from pathlib import Path

from tqdm import tqdm
import pandas as pd

# ===== ORM =====
from ganabosques_orm.collections.farm import Farm
from ganabosques_orm.collections.farmpolygons import FarmPolygons
from ganabosques_orm.auxiliaries.extidfarm import ExtIdFarm
from ganabosques_orm.auxiliaries.log import Log
from ganabosques_orm.enums.farmsource import FarmSource
from ganabosques_orm.enums.source import Source
from ganabosques_orm.collections.adm3 import Adm3
from ganabosques_orm.enums.valuechain import ValueChain

from config import config
from tools.log_print import log_print

# ========= LOG =========
logger = logging.getLogger("save_farm_geofarmer")


# ========= HELPERS =========

def load_geojson(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("GeoJSON inválido (no es dict)")
    return data


def extract_first_properties(geojson_obj: dict) -> dict:
    """Extrae properties del primer Feature."""
    if geojson_obj.get("type") == "FeatureCollection":
        features = geojson_obj.get("features", [])
        if features:
            return features[0].get("properties", {})
    elif geojson_obj.get("type") == "Feature":
        return geojson_obj.get("properties", {})
    return {}


def extract_geofarmer_ids(geojson_obj: dict) -> set:
    """Extrae los IDs de GeoFarmer del GeoJSON (campo farm_id en properties)."""
    ids = set()
    features = []
    if geojson_obj.get("type") == "FeatureCollection":
        features = geojson_obj.get("features", [])
    elif geojson_obj.get("type") == "Feature":
        features = [geojson_obj]
    for feat in features:
        props = feat.get("properties", {})
        fid = props.get("farm_id", "")
        if fid:
            ids.add(str(fid))
    return ids


def get_adm3_doc_from_code(code: str):
    """Busca documento Adm3 por ext_id."""
    return Adm3.objects(ext_id=code).only("id", "ext_id").first()


# ========== UPSERT ==========

def upsert_one(filepath: str, errores: list, stats: dict, value_chain: ValueChain = None):
    try:
        geojson_obj = load_geojson(filepath)
        props = extract_first_properties(geojson_obj)

        # Datos ya calculados en el paso 2 (QC)
        farm_code = str(props.get("farm_code", "")).strip()
        farm_id = str(props.get("farm_id", ""))
        adm3_code = str(props.get("adm3_code", "")).strip()
        latitude = props.get("latitude")
        longitude = props.get("longitude")
        farm_ha = props.get("farm_ha", 0.0)

        if not farm_code:
            raise ValueError("farm_code vacío en properties")
        if not adm3_code:
            raise ValueError("adm3_code vacío en properties (¿no pasó por QC?)")

        # Buscar Adm3 en MongoDB
        adm3_doc = get_adm3_doc_from_code(adm3_code)
        if not adm3_doc:
            raise ValueError(f"No se encontró Adm3 en BD para código '{adm3_code}'")

        # Extraer IDs GeoFarmer
        geofarmer_ids = extract_geofarmer_ids(geojson_obj)

        # 1) Buscar farm existente con este SIT_CODE (cualquier fuente)
        farm = Farm.objects(
            ext_id__match={'source': Source.SIT_CODE, 'ext_code': farm_code}
        ).first()

        if farm:
            # Farm existente → agregar ext_ids de GEOFARMER, cambiar fuente, actualizar
            existing_codes = {(e.source, e.ext_code) for e in farm.ext_id}
            for gf_id in geofarmer_ids:
                if (Source.GEOFARMER_ID, gf_id) not in existing_codes:
                    farm.ext_id.append(ExtIdFarm(source=Source.GEOFARMER_ID, ext_code=gf_id))
            farm.farm_source = FarmSource.GEOFARMER
            farm.adm3_id = adm3_doc
            if value_chain:
                farm.value_chain = value_chain
            farm.log.updated = datetime.now()
            farm.save()
            action_farm = "actualizado"
            stats["farm_updates"] += 1
        else:
            # Farm nuevo
            ext_ids = [ExtIdFarm(source=Source.SIT_CODE, ext_code=farm_code)]
            for gf_id in geofarmer_ids:
                ext_ids.append(ExtIdFarm(source=Source.GEOFARMER_ID, ext_code=gf_id))
            log_obj = Log(enable=True, created=datetime.now(), updated=datetime.now())
            farm = Farm(
                adm3_id=adm3_doc,
                ext_id=ext_ids,
                farm_source=FarmSource.GEOFARMER,
                value_chain=value_chain,
                log=log_obj,
            )
            farm.save()
            action_farm = "creado"
            stats["farm_inserts"] += 1

        # 2) Actualizar o crear polígono
        geojson_str = json.dumps(geojson_obj, ensure_ascii=False)
        existing_poly = FarmPolygons.objects(farm_id=farm).first()

        if existing_poly:
            existing_poly.geojson = geojson_str
            existing_poly.latitude = latitude
            existing_poly.longitud = longitude
            existing_poly.farm_ha = farm_ha
            existing_poly.radio = None
            existing_poly.log.updated = datetime.now()
            existing_poly.save()
            action_poly = "actualizado"
            stats["poly_updates"] += 1
        else:
            new_poly = FarmPolygons(
                farm_id=farm,
                geojson=geojson_str,
                latitude=latitude,
                longitud=longitude,
                farm_ha=farm_ha,
                radio=None,
                buffer_inputs=None,
                log=Log(enable=True, created=datetime.now(), updated=datetime.now()),
            )
            new_poly.save()
            action_poly = "creado"
            stats["poly_inserts"] += 1

        return True, f"SIT={farm_code}: Farm {action_farm}, Polígono {action_poly}, ADM3={adm3_code}"

    except Exception as e:
        errores.append({"archivo": os.path.basename(filepath), "error": str(e)})
        return False, f"ERROR {os.path.basename(filepath)}: {e}"


# ========== RUNNER ==========

def run(polygons_dir: str, errors_out_dir: str | None = None, value_chain: ValueChain = None):
    """
    Guarda farms GEOFARMER en MongoDB.
    Lee GeoJSONs del paso 2 (ya enriquecidos con adm3_code, lat, lon, farm_ha).
    """
    if not os.path.isdir(polygons_dir):
        raise FileNotFoundError(f"No existe la carpeta de polígonos: {polygons_dir}")

    files = sorted(str(p) for p in Path(polygons_dir).rglob("*.geojson"))
    if not files:
        log_print(logger, f"⚠️ No se encontraron GeoJSONs en {polygons_dir}")
        return

    ok = 0
    errores, logs = [], []

    stats = dict(
        farm_inserts=0,
        farm_updates=0,
        poly_inserts=0,
        poly_updates=0,
    )

    for fp in tqdm(files, desc="🧭 GEOFARMER → MongoDB"):
        success, msg = upsert_one(fp, errores, stats, value_chain=value_chain)
        logs.append(msg)
        if success:
            ok += 1

    for line in logs:
        log_print(logger, line)

    log_print(logger, f"✅ Hechos: {ok} | ❌ Errores: {len(errores)}")
    log_print(logger, "—— Resumen —————————————————————————————————")
    log_print(logger, f"Farms GEOFARMER insertados       : {stats['farm_inserts']}")
    log_print(logger, f"Farms actualizados (SAGARI→GF)   : {stats['farm_updates']}")
    log_print(logger, f"Polígonos insertados             : {stats['poly_inserts']}")
    log_print(logger, f"Polígonos actualizados           : {stats['poly_updates']}")

    if errores and errors_out_dir:
        os.makedirs(errors_out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_csv = os.path.join(errors_out_dir, f"errores_geofarmer_save_{ts}.csv")
        pd.DataFrame(errores).to_csv(out_csv, index=False, encoding="utf-8")
        log_print(logger, f"📄 Archivo de errores: {out_csv}")


if __name__ == "__main__":
    import argparse
    from mongoengine import connect

    connect(db=config['MONGO_DB_NAME'], host=config['MONGO_URI'])
    parser = argparse.ArgumentParser(description="Guardar farms GEOFARMER en MongoDB")
    parser.add_argument("--polygons-dir", required=True, help="Carpeta con GeoJSONs validados del paso 2")
    parser.add_argument("--errors-dir", default=None, help="Carpeta para errores (opcional)")
    args = parser.parse_args()
    run(args.polygons_dir, args.errors_dir)
