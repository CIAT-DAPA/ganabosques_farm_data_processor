# -*- coding: utf-8 -*-
import os
import re
import json
import logging
from datetime import datetime

from tqdm import tqdm
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.validation import make_valid
from pyproj import Transformer
from mongoengine import connect

# ===== ORM =====
from ganabosques_orm.collections.farm import Farm
from ganabosques_orm.collections.farmpolygons import FarmPolygons
from ganabosques_orm.auxiliaries.extidfarm import ExtIdFarm
from ganabosques_orm.auxiliaries.log import Log
from ganabosques_orm.enums.farmsource import FarmSource
from ganabosques_orm.enums.source import Source
from ganabosques_orm.collections.adm3 import Adm3

# 👇 importa tu config (elige una de las dos según ejecutes)
from config import config
# from src.config import config

from tools.log_print import log_print

# ========= DB & LOG =========
connect(db=config['MONGO_DB_NAME'], host=config['MONGO_URI'])
logger = logging.getLogger("GEOFARMER solo polígonos + ADM3")
logger.setLevel(logging.INFO)

# ========= RUTAS DESDE CONFIG (.env) =========
POLYGONS_DIR = os.path.normpath(config['GEOFARMER_POLYGONS_DIR'])
ADM3_SHP_PATH = os.path.normpath(config['ADM3_SHP_PATH'])
OUTPUT_ERRORS_DIR = os.path.normpath(config['GEOFARMER_ERRORS_DIR'])

# Nombre de la columna del código ADM3 dentro del shapefile (probamos en orden)
ADM3_CODE_CANDIDATES = ["ext_id", "cod_ver", "ADM3_CODE", "ADM3", "MPIO_CCDGO", "DPTOMPIO", "CODIGO", "CODE"]

# ========= HELPERS =========
RE_CODE = re.compile(r"(\d+)", re.IGNORECASE)
TRANS_3116_TO_4326 = Transformer.from_crs(3116, 4326, always_xy=True)

def extract_code_from_filename(fname: str) -> str | None:
    name = os.path.splitext(os.path.basename(fname))[0]
    m = RE_CODE.search(name)
    return m.group(1) if m else None

def load_geojson(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("GeoJSON inválido (no es dict)")
    return data

def union_geom_3116(geojson_obj):
    """Une todas las features del GeoJSON (asumido 3116)."""
    if geojson_obj.get("type") == "FeatureCollection":
        geoms = []
        for feat in geojson_obj.get("features", []):
            geom = shape(feat.get("geometry"))
            try:
                geom = make_valid(geom)
            except Exception:
                geom = geom.buffer(0)
            geoms.append(geom)
        if not geoms:
            return None
        return unary_union(geoms)
    elif geojson_obj.get("type") == "Feature":
        geom = shape(geojson_obj.get("geometry"))
        try:
            return make_valid(geom)
        except Exception:
            return geom.buffer(0)
    else:
        try:
            geom = shape(geojson_obj)
            return make_valid(geom)
        except Exception:
            return None

def centroid_wgs84_from_3116(geom3116):
    c = geom3116.centroid
    lon, lat = TRANS_3116_TO_4326.transform(c.x, c.y)
    return lat, lon  # (lat, lon) en grados

def area_hectares_from_3116(geom3116):
    return float(geom3116.area) / 10000.0

def load_adm3_gdf(shp_path: str) -> tuple[gpd.GeoDataFrame, str]:
    """Carga ADM3, asegura EPSG:3116, devuelve (gdf, nombre_columna_codigo)."""
    gdf = gpd.read_file(shp_path)
    if gdf.crs is None:
        raise ValueError("El shapefile ADM3 no tiene CRS; asígnalo antes de usarlo.")
    if gdf.crs.to_epsg() != 3116:
        gdf = gdf.to_crs(epsg=3116)
    code_col = None
    for cand in ADM3_CODE_CANDIDATES:
        if cand in gdf.columns:
            code_col = cand
            break
    if not code_col:
        raise ValueError(f"No se encontró columna de código ADM3 (probadas: {ADM3_CODE_CANDIDATES})")
    gdf[code_col] = gdf[code_col].astype(str)
    return gdf, code_col

def find_adm3_code_for_geom(geom3116, adm3_gdf: gpd.GeoDataFrame, code_col: str) -> str | None:
    """Centroide dentro; si no, intersección con el polígono completo."""
    pt = gpd.GeoDataFrame(geometry=[geom3116.centroid], crs="EPSG:3116")
    join1 = gpd.sjoin(pt, adm3_gdf[[code_col, "geometry"]], how="left", predicate="within")
    code = join1.iloc[0][code_col] if not join1.empty and pd.notna(join1.iloc[0][code_col]) else None
    if code:
        return str(code)
    poly = gpd.GeoDataFrame(geometry=[geom3116], crs="EPSG:3116")
    candidates = adm3_gdf[adm3_gdf.intersects(geom3116.envelope)]
    if candidates.empty:
        return None
    join2 = gpd.sjoin(poly, candidates[[code_col, "geometry"]], how="left", predicate="intersects")
    code = join2.iloc[0][code_col] if not join2.empty and pd.notna(join2.iloc[0][code_col]) else None
    return str(code) if code else None

def get_adm3_doc_from_code(code: str):
    return Adm3.objects(ext_id=code).only("id", "ext_id").first()

# ========== UPSERT ==========

def upsert_one(filepath: str, adm3_gdf: gpd.GeoDataFrame, adm3_code_col: str, errores: list):
    try:
        code = extract_code_from_filename(filepath)
        if not code:
            raise ValueError(f"No se pudo extraer código SIT del nombre: {os.path.basename(filepath)}")

        geojson_obj = load_geojson(filepath)
        geom = union_geom_3116(geojson_obj)
        if geom is None or geom.is_empty:
            raise ValueError("Geometría vacía o inválida")

        lat, lon = centroid_wgs84_from_3116(geom)  # grados
        farm_ha = area_hectares_from_3116(geom)

        adm3_code = find_adm3_code_for_geom(geom, adm3_gdf, adm3_code_col)
        adm3_doc = get_adm3_doc_from_code(adm3_code) if adm3_code else None
        if not adm3_doc:
            raise ValueError(f"No se encontró Adm3 para el polígono (code={adm3_code}). Verifica shapefile/colección Adm3.")

        # ===== FARM =====
        farm = Farm.objects(ext_id__match={'source': Source.SIT_CODE, 'ext_code': code}).only(
            "id", "ext_id", "log", "farm_source", "adm3_id"
        ).first()

        if farm:
            farm.adm3_id = adm3_doc
            farm.log.updated = datetime.now()
            farm.save()
            action_farm = "actualizado"
        else:
            log = Log(enable=True, created=datetime.now(), updated=datetime.now())
            farm = Farm(
                adm3_id=adm3_doc,
                ext_id=[ExtIdFarm(source=Source.SIT_CODE, ext_code=code)],
                farm_source=FarmSource.GEOFARMER,
                log=log
            )
            farm.save()
            action_farm = "creado"

        # ===== POLYGON =====
        poly = FarmPolygons.objects(farm_id=farm).only(
            "id", "geojson", "latitude", "longitud", "farm_ha", "radio", "buffer_inputs", "log"
        ).first()

        geojson_str = json.dumps(geojson_obj, ensure_ascii=False)
        radio = None
        buffer_inputs = None

        if poly:
            needs_update = (
                poly.geojson != geojson_str or
                poly.latitude != lat or
                poly.longitud != lon or
                poly.farm_ha != farm_ha or
                poly.radio is not None or
                poly.buffer_inputs not in (None, [], {})
            )
            if needs_update:
                poly.geojson = geojson_str
                poly.latitude = lat
                poly.longitud = lon
                poly.farm_ha = farm_ha
                poly.radio = radio
                poly.buffer_inputs = buffer_inputs
                poly.log.updated = datetime.now()
                poly.save()
                action_poly = "actualizado"
            else:
                action_poly = "sin cambios"
        else:
            log = Log(enable=True, created=datetime.now(), updated=datetime.now())
            poly = FarmPolygons(
                farm_id=farm,
                geojson=geojson_str,
                latitude=lat,
                longitud=lon,
                farm_ha=farm_ha,
                radio=radio,
                buffer_inputs=buffer_inputs,
                log=log
            )
            poly.save()
            action_poly = "creado"

        return True, f"SIT={code}: Farm {action_farm}, Polígono {action_poly}, ADM3={adm3_doc.ext_id}"

    except Exception as e:
        errores.append({"archivo": os.path.basename(filepath), "error": str(e)})
        return False, f"ERROR {os.path.basename(filepath)}: {e}"

# ========== RUNNER ==========

def run(polygons_dir: str, adm3_shp: str, errors_out_dir: str | None = None):
    if not os.path.isdir(polygons_dir):
        raise FileNotFoundError(f"No existe la carpeta de polígonos: {polygons_dir}")
    if not os.path.isfile(adm3_shp):
        raise FileNotFoundError(f"No existe el shapefile ADM3: {adm3_shp}")

    adm3_gdf, code_col = load_adm3_gdf(adm3_shp)

    files = [os.path.join(polygons_dir, f) for f in os.listdir(polygons_dir) if f.lower().endswith(".geojson")]
    ok = 0
    errores, logs = [], []

    for fp in tqdm(files, desc="🧭 GEOFARMER + ADM3"):
        success, msg = upsert_one(fp, adm3_gdf, code_col, errores)
        logs.append(msg)
        if success:
            ok += 1

    for line in logs:
        log_print(logger, line)

    log_print(logger, f"✅ Hechos: {ok} | ❌ Errores: {len(errores)}")

    if errores and errors_out_dir:
        os.makedirs(errors_out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_csv = os.path.join(errors_out_dir, f"errores_geofarmer_adm3_{ts}.csv")
        pd.DataFrame(errores).to_csv(out_csv, index=False, encoding="utf-8")
        log_print(logger, f"📄 Archivo de errores: {out_csv}")

# ========== MAIN ==========
if __name__ == "__main__":
    run(POLYGONS_DIR, ADM3_SHP_PATH, OUTPUT_ERRORS_DIR)
