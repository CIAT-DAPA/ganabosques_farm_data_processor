# -*- coding: utf-8 -*-
import os
import re
import json
import logging
from datetime import datetime
from functools import partial

from tqdm import tqdm
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape
from shapely.ops import unary_union, transform as shapely_transform
from shapely.validation import make_valid
from pyproj import Transformer, Geod
from mongoengine import connect

# ===== ORM =====
from ganabosques_orm.collections.farm import Farm
from ganabosques_orm.collections.farmpolygons import FarmPolygons
from ganabosques_orm.auxiliaries.extidfarm import ExtIdFarm
from ganabosques_orm.auxiliaries.log import Log
from ganabosques_orm.enums.farmsource import FarmSource
from ganabosques_orm.enums.source import Source
from ganabosques_orm.collections.adm3 import Adm3

from config import config
from tools.log_print import log_print

# ========= DB & LOG =========
connect(db=config['MONGO_DB_NAME'], host=config['MONGO_URI'])
logger = logging.getLogger("GEOFARMER solo polígonos + ADM3 (4326)")
logger.setLevel(logging.INFO)

# ========= RUTAS DESDE CONFIG (.env) =========
POLYGONS_DIR     = os.path.normpath(config['GEOFARMER_POLYGONS_DIR'])
ADM3_SHP_PATH    = os.path.normpath(config['ADM3_SHP_PATH'])
OUTPUT_ERRORS_DIR= os.path.normpath(config['GEOFARMER_ERRORS_DIR'])

ADM3_CODE_CANDIDATES = ["ext_id", "cod_ver", "ADM3_CODE", "ADM3", "MPIO_CCDGO", "DPTOMPIO", "CODIGO", "CODE"]

# ========= HELPERS =========
RE_CODE = re.compile(r"(\d+)", re.IGNORECASE)

# Transformer for 3116 -> 4326 (used only if input geometries appear projected)
TRANS_3116_TO_4326 = Transformer.from_crs(3116, 4326, always_xy=True)
TRANS_4326_TO_3116 = Transformer.from_crs(4326, 3116, always_xy=True)

# Geod for area calculation on WGS84
GEOD = Geod(ellps="WGS84")

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

def transform_shapely_geom(geom, transformer: Transformer):
    # returns transformed shapely geometry via shapely_transform
    func = lambda x, y, z=None: transformer.transform(x, y)
    return shapely_transform(func, geom)

def ensure_geom_4326(geom):
    """
    Asegura que geom esté en EPSG:4326.
    Heurística:
      - Si bounds indican coordenadas pequeñas (<=180) assume lon/lat → ya 4326.
      - Si bounds muestran valores grandes (>180) asumimos que está proyectado (ej. 3116)
        y lo reproyectamos a 4326.
    Devuelve geometría en 4326 o None.
    """
    if geom is None or geom.is_empty:
        return None

    try:
        geom = make_valid(geom)
    except Exception:
        try:
            geom = geom.buffer(0)
        except Exception:
            pass

    minx, miny, maxx, maxy = geom.bounds
    max_abs = max(abs(minx), abs(miny), abs(maxx), abs(maxy))

    if max_abs > 180:
        # parece proyectado en metros (p. ej. 3116) -> reproyectar a 4326
        try:
            geom4326 = transform_shapely_geom(geom, TRANS_3116_TO_4326)
            return geom4326
        except Exception as e:
            raise RuntimeError(f"Fallo al reproyectar geom proyectada a 4326: {e}")
    else:
        # ya está en lon/lat => dejamos
        return geom

def union_geom_4326(geojson_obj):
    """
    Une las geometrías del GeoJSON y entrega una geometría en EPSG:4326.
    Acepta FeatureCollection, Feature o geom simple.
    """
    try:
        if geojson_obj.get("type") == "FeatureCollection":
            geoms = []
            for feat in geojson_obj.get("features", []):
                geom = shape(feat.get("geometry"))
                try:
                    geom = make_valid(geom)
                except Exception:
                    geom = geom.buffer(0)
                geom4326 = ensure_geom_4326(geom)
                if geom4326 is not None and not geom4326.is_empty:
                    geoms.append(geom4326)
            if not geoms:
                return None
            return unary_union(geoms)
        elif geojson_obj.get("type") == "Feature":
            geom = shape(geojson_obj.get("geometry"))
            try:
                geom = make_valid(geom)
            except Exception:
                geom = geom.buffer(0)
            return ensure_geom_4326(geom)
        else:
            geom = shape(geojson_obj)
            try:
                geom = make_valid(geom)
            except Exception:
                geom = geom.buffer(0)
            return ensure_geom_4326(geom)
    except Exception as e:
        raise RuntimeError(f"Error al unir/transformar geometrías a 4326: {e}")

def centroid_wgs84_from_4326(geom4326):
    """
    Devuelve lat, lon (WGS84) del centroid en EPSG:4326
    """
    c = geom4326.centroid
    return float(c.y), float(c.x)

def area_hectares_from_4326(geom4326):
    """
    Calcula el área en hectáreas usando GEOD (área sobre elipsoide), entrada geom en lon/lat (4326).
    Maneja polígonos y multipolígonos.
    """
    if geom4326 is None or geom4326.is_empty:
        return 0.0
    # Para MultiPolygons sumamos por parte
    total_area_m2 = 0.0

    def poly_area(polygon):
        # exterior ring
        lon, lat = polygon.exterior.coords.xy
        lons = list(lon)
        lats = list(lat)
        area, perim = GEOD.polygon_area_perimeter(lons, lats)
        a = abs(area)
        # añadir agujeros (interiors) como restas
        for interior in polygon.interiors:
            ilon, ilat = interior.coords.xy
            ia, ip = GEOD.polygon_area_perimeter(list(ilon), list(ilat))
            a -= abs(ia)
        return a

    from shapely.geometry import Polygon, MultiPolygon
    if isinstance(geom4326, Polygon):
        total_area_m2 += poly_area(geom4326)
    elif isinstance(geom4326, MultiPolygon):
        for p in geom4326.geoms:
            total_area_m2 += poly_area(p)
    else:
        # Si no es polígono, area 0
        total_area_m2 = 0.0

    return float(total_area_m2) / 10000.0  # hectáreas

def load_adm3_gdf(shp_path: str) -> tuple[gpd.GeoDataFrame, str]:
    """
    Carga el shapefile ADM3 y lo convierte a EPSG:4326 (si necesario).
    Devuelve gdf y el nombre de la columna de código encontrada.
    """
    gdf = gpd.read_file(shp_path)
    if gdf.crs is None:
        raise ValueError("El shapefile ADM3 no tiene CRS; asígnalo antes de usarlo.")
    # reproyectar a 4326 (todo en 4326)
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    code_col = None
    for cand in ADM3_CODE_CANDIDATES:
        if cand in gdf.columns:
            code_col = cand
            break
    if not code_col:
        raise ValueError(f"No se encontró columna de código ADM3 (probadas: {ADM3_CODE_CANDIDATES})")
    gdf[code_col] = gdf[code_col].astype(str)
    return gdf, code_col

def find_adm3_code_for_geom(geom4326, adm3_gdf: gpd.GeoDataFrame, code_col: str) -> str | None:
    """
    Busca el código ADM3 para la geometría (todo en 4326):
      - primero con centroid dentro (fast)
      - luego por intersects con envelope y finalmente intersects
    """
    if geom4326 is None or geom4326.is_empty:
        return None
    try:
        pt = gpd.GeoDataFrame(geometry=[geom4326.centroid], crs="EPSG:4326")
        join1 = gpd.sjoin(pt, adm3_gdf[[code_col, "geometry"]], how="left", predicate="within")
        if not join1.empty and pd.notna(join1.iloc[0][code_col]):
            return str(join1.iloc[0][code_col])
    except Exception:
        pass

    try:
        poly = gpd.GeoDataFrame(geometry=[geom4326], crs="EPSG:4326")
        candidates = adm3_gdf[adm3_gdf.intersects(geom4326.envelope)]
        if candidates.empty:
            return None
        join2 = gpd.sjoin(poly, candidates[[code_col, "geometry"]], how="left", predicate="intersects")
        if not join2.empty and pd.notna(join2.iloc[0][code_col]):
            return str(join2.iloc[0][code_col])
    except Exception:
        return None
    return None

def get_adm3_doc_from_code(code: str):
    return Adm3.objects(ext_id=code).only("id", "ext_id").first()

# ========= NUEVO: helpers de política SAGARI -> GEOFARMER =========
def delete_sagari_by_sit(sit_code: str) -> tuple[int, int]:
    sagari_q = Farm.objects(
        farm_source=FarmSource.SAGARI,
        ext_id__match={'source': Source.SIT_CODE, 'ext_code': sit_code}
    ).only("id")
    sagari_ids = [f.id for f in sagari_q]
    if not sagari_ids:
        return 0, 0
    polys_del = FarmPolygons.objects(farm_id__in=sagari_ids).delete()
    farms_del = Farm.objects(id__in=sagari_ids).delete()
    return farms_del, polys_del

# ========== UPSERT ==========

def upsert_one(filepath: str, adm3_gdf: gpd.GeoDataFrame, adm3_code_col: str, errores: list, stats: dict):
    try:
        code = extract_code_from_filename(filepath)
        if not code:
            raise ValueError(f"No se pudo extraer código SIT del nombre: {os.path.basename(filepath)}")

        geojson_obj = load_geojson(filepath)

        # UNION + ensure 4326
        geom4326 = union_geom_4326(geojson_obj)
        if geom4326 is None or geom4326.is_empty:
            raise ValueError("Geometría vacía o inválida")

        # debug info
        bounds = geom4326.bounds

        # centroid y area (usando geod)
        lat, lon = centroid_wgs84_from_4326(geom4326)
        farm_ha = area_hectares_from_4326(geom4326)

        adm3_code = find_adm3_code_for_geom(geom4326, adm3_gdf, adm3_code_col)
        adm3_doc = get_adm3_doc_from_code(adm3_code) if adm3_code else None
        if not adm3_doc:
            raise ValueError(
                f"No se encontró Adm3 para el polígono (code={adm3_code}). Bounds={bounds}, area_ha={farm_ha:.4f}"
            )

        # 1) BORRAR SAGARI con este SIT (y polígonos)
        del_farms, del_polys = delete_sagari_by_sit(code)
        stats["sagari_deleted_farms"] += del_farms
        stats["sagari_deleted_polys"] += del_polys

        # 2) UPSERT del FARM de GEOFARMER por SIT
        farm = Farm.objects(
            farm_source=FarmSource.GEOFARMER,
            ext_id__match={'source': Source.SIT_CODE, 'ext_code': code}
        ).only("id", "ext_id", "log", "farm_source", "adm3_id").first()

        if farm:
            farm.adm3_id = adm3_doc
            farm.log.updated = datetime.now()
            farm.save()
            action_farm = "actualizado"
            stats["farm_updates"] += 1
        else:
            others = Farm.objects(
                ext_id__match={'source': Source.SIT_CODE, 'ext_code': code},
                farm_source__ne=FarmSource.GEOFARMER
            ).only("id")
            other_ids = [o.id for o in others]
            if other_ids:
                FarmPolygons.objects(farm_id__in=other_ids).delete()
                Farm.objects(id__in=other_ids).delete()

            log = Log(enable=True, created=datetime.now(), updated=datetime.now())
            farm = Farm(
                adm3_id=adm3_doc,
                ext_id=[ExtIdFarm(source=Source.SIT_CODE, ext_code=code)],
                farm_source=FarmSource.GEOFARMER,
                log=log
            )
            farm.save()
            action_farm = "creado"
            stats["farm_inserts"] += 1

        # 3) REEMPLAZAR POLÍGONOS del farm por el de GEOFARMER
        geojson_str = json.dumps(geojson_obj, ensure_ascii=False)
        existing_polys = FarmPolygons.objects(farm_id=farm).only("id")
        prev_n = existing_polys.count()
        if prev_n:
            FarmPolygons.objects(farm_id=farm).delete()
            stats["poly_deleted_for_replace"] += prev_n

        new_poly = FarmPolygons(
            farm_id=farm,
            geojson=geojson_str,
            latitude=lat,
            longitud=lon,
            farm_ha=farm_ha,
            radio=None,
            buffer_inputs=None,
            log=Log(enable=True, created=datetime.now(), updated=datetime.now())
        )
        new_poly.save()
        stats["poly_inserts"] += 1
        action_poly = "creado" if prev_n == 0 else "reemplazado"

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

    stats = dict(
        sagari_deleted_farms=0,
        sagari_deleted_polys=0,
        farm_inserts=0,
        farm_updates=0,
        poly_inserts=0,
        poly_deleted_for_replace=0
    )

    for fp in tqdm(files, desc="🧭 GEOFARMER + ADM3"):
        success, msg = upsert_one(fp, adm3_gdf, code_col, errores, stats)
        logs.append(msg)
        if success:
            ok += 1

    for line in logs:
        log_print(logger, line)

    log_print(logger, f"✅ Hechos: {ok} | ❌ Errores: {len(errores)}")
    log_print(logger, "—— Resumen —————————————————————————————————")
    log_print(logger, f"Farms SAGARI eliminados          : {stats['sagari_deleted_farms']}")
    log_print(logger, f"Polígonos SAGARI eliminados      : {stats['sagari_deleted_polys']}")
    log_print(logger, f"Farms GEOFARMER insertados       : {stats['farm_inserts']}")
    log_print(logger, f"Farms GEOFARMER actualizados     : {stats['farm_updates']}")
    log_print(logger, f"Polígonos insertados (GEOFARMER) : {stats['poly_inserts']}")
    log_print(logger, f"Polígonos previos reemplazados   : {stats['poly_deleted_for_replace']}")

    if errores and errors_out_dir:
        os.makedirs(errors_out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_csv = os.path.join(errors_out_dir, f"errores_geofarmer_adm3_{ts}.csv")
        pd.DataFrame(errores).to_csv(out_csv, index=False, encoding="utf-8")
        log_print(logger, f"📄 Archivo de errores: {out_csv}")

if __name__ == "__main__":
    run(POLYGONS_DIR, ADM3_SHP_PATH, OUTPUT_ERRORS_DIR)
