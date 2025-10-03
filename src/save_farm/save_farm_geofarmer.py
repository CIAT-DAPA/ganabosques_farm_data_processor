# -*- coding: utf-8 -*-
"""
GEOFARMER (todo QUEMADO):
- Lee GeoJSONs de las carpetas dadas.
- Determina adm3 por centroide -> intersección contra DIVIPOLA (SHP).
- Busca en Mongo (colección adm3) por ext_id y usa su _id como adm3_id.
- Upsert de Farm (ext_id con Source.SIT_CODE / farm_source=GEOFARMER) y FarmPolygons (solo geojson).

Barras de progreso:
  1) 🔎 Buscando ADM3
  2) 💾 Guardando en Mongo

Ejecución:
  python save_farm_geofarmer.py --create-farms --folders "D:\...\carnatural" "D:\...\colacteos"
"""

import os
import re
import glob
import json
import argparse
from datetime import datetime
from typing import Optional, Dict, Tuple, List

# =======================
# 🔥 VALORES QUEMADOS
# =======================
ADM3_SHP_DEFAULT   = r"D:\OneDrive - CGIAR\Desktop\ganabosques\ganabosques_project_local\data\geoserver\administrative\adm3\adm3.shp"
ADM3_FIELD_DEFAULT = "cod_ver"  # campo del SHP que corresponde a ext_id en Mongo.adm3

MONGO_URI_DEFAULT  = "mongodb://localhost:27017"
MONGO_DB_DEFAULT   = "ganabosques"
# =======================

import geopandas as gpd
from shapely.geometry import shape as shp_shape
from shapely.ops import unary_union
from shapely.errors import TopologicalError

# tqdm opcional (si no está instalado, usamos un stub)
try:
    from tqdm import tqdm
except Exception:
    def tqdm(iterable=None, total=None, desc=None, **kwargs):
        # stub simple
        print(desc or "")
        return iterable

# Mongo y modelos
from mongoengine import connect
from bson import ObjectId

from ganabosques_orm.collections.farm import Farm
from ganabosques_orm.collections.farmpolygons import FarmPolygons
from ganabosques_orm.auxiliaries.extidfarm import ExtIdFarm
from ganabosques_orm.auxiliaries.log import Log
from ganabosques_orm.enums.farmsource import FarmSource
from ganabosques_orm.enums.source import Source
from ganabosques_orm.collections.adm3 import Adm3

# --------------- util de impresión ---------------
def log_print(msg: str):
    # Evitar doble impresión por logging; imprimimos directo.
    print(msg)

# ---------------- util ----------------

def extract_sit_code(filename: str) -> Optional[str]:
    base = os.path.basename(filename)
    m = re.search(r"(?i)SIT[_-]?(\d+)(?=\.geojson$)", base)
    if m:
        return m.group(1)
    m2 = re.search(r"(\d{5,})(?=\.geojson$)", base)
    return m2.group(1) if m2 else None

def geofarmer_source_for_sit() -> Source:
    """Source fijo para guardar el SIT_CODE en ext_id. Prioridad: SIT_CODE -> SIT -> GEOFARMER."""
    for name in ("SIT_CODE", "SIT", "GEOFARMER"):
        if hasattr(Source, name):
            return getattr(Source, name)
    # fallback extremo
    return Source.GEOFARMER

def build_farm_index() -> Dict[Tuple, Farm]:
    idx: Dict[Tuple, Farm] = {}
    for farm in Farm.objects.no_dereference().only("id", "ext_id", "farm_source", "adm3_id", "log"):
        for e in farm.ext_id:
            idx[(e.source, e.ext_code)] = farm
    return idx

# ---- lookup robusto en Mongo (string/int/trim ceros) ----------------
def adm3_oid_by_ext(ext_code: str) -> Optional[ObjectId]:
    """Busca en Mongo (colección adm3) por ext_id y devuelve su _id (ObjectId)."""
    col = Adm3._get_collection()
    c = str(ext_code).strip()
    if not c or c.lower() == "nan":
        return None
    # 1) exacto string
    doc = col.find_one({"ext_id": c}, {"_id": 1})
    if doc:
        return doc["_id"]
    # 2) intentar int (maneja '123.0' y '00123')
    try:
        n = int(float(c))
        doc = col.find_one({"ext_id": n}, {"_id": 1})
        if doc:
            return doc["_id"]
        # 3) int como string
        doc = col.find_one({"ext_id": str(n)}, {"_id": 1})
        if doc:
            return doc["_id"]
    except Exception:
        pass
    # 4) string sin ceros a la izquierda
    c2 = c.lstrip("0")
    if c2 and c2 != c:
        doc = col.find_one({"ext_id": c2}, {"_id": 1})
        if doc:
            return doc["_id"]
    return None

def _sanitize_code(val) -> Optional[str]:
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    return s

def choose_adm3_for_geom(geom, adm3_gdf: gpd.GeoDataFrame, adm3_field: str) -> Optional[str]:
    """Primero punto representativo (dentro) → luego mayor área de intersección. Retorna código válido (no vacío)."""
    if geom.is_empty:
        return None

    try:
        rep = geom.representative_point()
    except Exception:
        rep = geom.centroid

    # 1) contiene punto representativo
    try:
        cand_idx = list(adm3_gdf.sindex.query(rep, predicate="contains"))
    except Exception:
        cand_idx = []
    if cand_idx:
        for i in cand_idx:
            row = adm3_gdf.iloc[i]
            try:
                if row.geometry.contains(rep):
                    code = _sanitize_code(row[adm3_field])
                    if code:
                        return code
            except Exception:
                continue

    # 2) intersección por área
    try:
        cand_idx = list(adm3_gdf.sindex.query(geom, predicate="intersects"))
    except Exception:
        cand_idx = []

    best_code, best_area = None, 0.0
    for i in cand_idx:
        row = adm3_gdf.iloc[i]
        try:
            inter = geom.intersection(row.geometry)
            a = inter.area if not inter.is_empty else 0.0
        except TopologicalError:
            a = 0.0
        code = _sanitize_code(row[adm3_field])
        if code and a > best_area:
            best_area = a
            best_code = code

    return best_code

def ensure_farm_ext_source_geofarmer(farm: Farm, sit_code: str, sit_src: Source) -> bool:
    """Asegura ext_id (sit_src, sit_code) y farm_source=GEOFARMER. Devuelve True si cambió algo."""
    changed = False
    if not any((e.source == sit_src and e.ext_code == sit_code) for e in farm.ext_id):
        farm.ext_id.append(ExtIdFarm(source=sit_src, ext_code=sit_code))
        changed = True
    if farm.farm_source != FarmSource.GEOFARMER:
        farm.farm_source = FarmSource.GEOFARMER
        changed = True
    if changed:
        farm.save()
    return changed

def upsert_polygon_only_geojson(farm: Farm, geojson_dict: dict) -> str:
    """Crea/actualiza FarmPolygons (solo geojson)."""
    geojson_str = json.dumps(geojson_dict, ensure_ascii=False)
    existing = FarmPolygons.objects.no_dereference().only("id", "farm_id", "geojson", "log").filter(farm_id=farm).first()
    if existing:
        if existing.geojson != geojson_str:
            existing.geojson = geojson_str
            existing.save()
            return "updated"
        return "unchanged"
    log = Log(enable=True, created=datetime.now(), updated=datetime.now())
    poly = FarmPolygons(farm_id=farm, geojson=geojson_str, log=log)
    poly.save()
    return "created"

# ---------------- CRS GeoJSON ----------------
def _normalize_gj_geom(gj: dict):
    """Convierte el GeoJSON a shapely. Si viene con crs EPSG:* != 4326, reproyecta a 4326."""
    from shapely.ops import transform as shp_transform
    try:
        from pyproj import Transformer
    except Exception:
        Transformer = None

    # geom
    if "features" in gj:
        geoms = [shp_shape(ft["geometry"]) for ft in gj["features"] if ft.get("geometry")]
        if not geoms:
            raise ValueError("GeoJSON sin geometrías")
        geom = unary_union(geoms)
    elif "geometry" in gj:
        geom = shp_shape(gj["geometry"])
    elif gj.get("type") in ("Polygon", "MultiPolygon"):
        geom = shp_shape(gj)
    else:
        raise ValueError("Formato GeoJSON no soportado")

    # reproyección si trae crs explícito
    if Transformer:
        crs_name = None
        if "crs" in gj and isinstance(gj["crs"], dict):
            props = gj["crs"].get("properties") or {}
            crs_name = props.get("name") or props.get("code")
        if crs_name and isinstance(crs_name, str) and ("EPSG" in crs_name.upper()):
            try:
                epsg_code = int(re.findall(r"(\d+)", crs_name)[0])
                if epsg_code != 4326:
                    transformer = Transformer.from_crs(epsg_code, 4326, always_xy=True)
                    geom = shp_transform(lambda x, y, z=None: transformer.transform(x, y), geom)
            except Exception:
                pass
    return geom

# ---------------- main ----------------

def parse_args():
    ap = argparse.ArgumentParser(description="GEOFARMER: adm3 desde DIVIPOLA y guardado Farm/FarmPolygons (todo QUEMADO).")
    ap.add_argument("--folders", nargs="+", required=True, help="Carpetas con .geojson (no recursivo).")
    # Permitimos override opcional, pero por defecto usamos los QUEMADOS
    ap.add_argument("--adm3-shp", default=ADM3_SHP_DEFAULT, help="Ruta al DIVIPOLA (SHP).")
    ap.add_argument("--adm3-field", default=ADM3_FIELD_DEFAULT, help="Columna del SHP usada como ext_id en Mongo.adm3.")
    ap.add_argument("--create-farms", action="store_true", help="Crear Farm si no existe.")
    ap.add_argument("--force-adm3", action="store_true", help="Actualizar adm3_id aunque ya exista.")
    ap.add_argument("--mongo-uri", default=MONGO_URI_DEFAULT, help="URI MongoDB (QUEMADO por defecto).")
    ap.add_argument("--mongo-db", default=MONGO_DB_DEFAULT, help="DB MongoDB (QUEMADO por defecto).")
    return ap.parse_args()

def main():
    args = parse_args()

    # Conexión Mongo (QUEMADO con posibilidad de override por CLI)
    mongo_uri = args.mongo_uri or MONGO_URI_DEFAULT
    mongo_db = args.mongo_db or MONGO_DB_DEFAULT
    connect(db=mongo_db, host=mongo_uri)
    log_print(f"🔌 Conectado a MongoDB (db={mongo_db})")

    # Source fijo para el SIT extraído del filename (flujo GEOFARMER)
    sit_source = geofarmer_source_for_sit()
    log_print(f"Usando Source.{sit_source.name} para ext_id (SIT_CODE). FarmSource=GEOFARMER.")

    # Cargar DIVIPOLA (SHP → EPSG:4326)
    adm3_shp = args.adm3_shp
    adm3_field = args.adm3_field

    if not os.path.isfile(adm3_shp):
        raise FileNotFoundError(f"No existe el shapefile: {adm3_shp}")

    adm3_gdf = gpd.read_file(adm3_shp)
    if adm3_gdf.crs is None:
        adm3_gdf.set_crs(epsg=4326, inplace=True)
    else:
        adm3_gdf = adm3_gdf.to_crs(epsg=4326)

    if adm3_field not in adm3_gdf.columns:
        raise ValueError(f"No existe la columna '{adm3_field}' en {adm3_shp}. Columnas: {list(adm3_gdf.columns)}")

    # saneo códigos y descartar vacíos para evitar 'nan'
    adm3_gdf = adm3_gdf[[adm3_field, "geometry"]].copy()
    adm3_gdf[adm3_field] = adm3_gdf[adm3_field].astype(str).str.strip()
    adm3_gdf = adm3_gdf[adm3_gdf[adm3_field].str.len() > 0].reset_index(drop=True)

    # índice espacial
    _ = adm3_gdf.sindex

    # Índice de farms
    farm_index = build_farm_index()

    # Recolectar archivos
    files: List[str] = []
    for root in args.folders:
        if not os.path.isdir(root):
            log_print(f"[AVISO] Carpeta no existe: {root}")
            continue
        files.extend(glob.glob(os.path.join(root, "*.geojson")))

    total_files = len(files)
    if total_files == 0:
        log_print("No se encontraron .geojson en las carpetas dadas.")
        return

    # ------------------ PASO 1: Búsqueda ADM3 ------------------
    work = []
    errors = []

    for path in tqdm(files, desc="🔎 Buscando ADM3", total=total_files):
        try:
            sit_code = extract_sit_code(path)
            if not sit_code:
                raise ValueError(f"No se pudo extraer SIT_CODE de: {os.path.basename(path)}")

            with open(path, "r", encoding="utf-8") as f:
                gj = json.load(f)

            geom = _normalize_gj_geom(gj)
            adm3_code = choose_adm3_for_geom(geom, adm3_gdf, adm3_field)
            if not adm3_code:
                raise ValueError("No se pudo determinar adm3 por intersección/centroide (o código vacío)")

            adm3_id = adm3_oid_by_ext(adm3_code)
            if not adm3_id:
                raise ValueError(f"No existe Adm3 en Mongo con ext_id='{adm3_code}'")

            work.append({
                "path": path,
                "sit_code": sit_code,
                "gj": gj,
                "adm3_id": adm3_id
            })

        except Exception as e:
            errors.append({"file": path, "error": str(e)})
            log_print(f"[ERROR] {os.path.basename(path)} -> {e}")

    # ------------------ PASO 2: Guardado Mongo ------------------
    farms_created = farms_changed = 0
    polys_created = polys_updated = polys_unchanged = 0

    for item in tqdm(work, desc="💾 Guardando en Mongo", total=len(work)):
        try:
            sit_code = item["sit_code"]
            gj = item["gj"]
            adm3_id = item["adm3_id"]

            # Farm por (sit_source, sit_code)
            farm = farm_index.get((sit_source, sit_code))

            if not farm:
                if not args.create_farms:
                    raise ValueError(
                        f"No existe Farm ({sit_source.name}, {sit_code}). Ejecuta con --create-farms para crearlo."
                    )
                log = Log(enable=True, created=datetime.now(), updated=datetime.now())
                farm = Farm(
                    adm3_id=Adm3(id=adm3_id),  # stub para ReferenceField
                    ext_id=[ExtIdFarm(source=sit_source, ext_code=sit_code)],
                    farm_source=FarmSource.GEOFARMER,
                    log=log
                )
                farm.save()
                farms_created += 1
                farm_index[(sit_source, sit_code)] = farm
            else:
                # asegurar ext_id y farm_source
                if ensure_farm_ext_source_geofarmer(farm, sit_code, sit_source):
                    farms_changed += 1
                # actualizar adm3_id si vacío o si --force-adm3
                if (getattr(farm, "adm3_id", None) in (None, "")) or args.force_adm3:
                    farm.adm3_id = Adm3(id=adm3_id)
                    farm.save()

            # Upsert polígono (solo geojson)
            res = upsert_polygon_only_geojson(farm, gj)
            if res == "created":
                polys_created += 1
            elif res == "updated":
                polys_updated += 1
            else:
                polys_unchanged += 1

        except Exception as e:
            errors.append({"file": item["path"], "error": str(e)})
            log_print(f"[ERROR] {os.path.basename(item['path'])} -> {e}")

    # Resumen
    log_print("\n📊 RESUMEN")
    log_print(f"Archivos procesados: {total_files}")
    log_print(f"Farms:   creados={farms_created}, cambios={farms_changed}")
    log_print(f"Polygons: creados={polys_created}, actualizados={polys_updated}, sin_cambios={polys_unchanged}")

    if errors:
        out_dir = os.path.join(os.getcwd(), "errores_geofarmer")
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_csv = os.path.join(out_dir, f"errores_{ts}.csv")
        try:
            import pandas as pd
            pd.DataFrame(errors).to_csv(out_csv, index=False)
        except Exception:
            with open(out_csv.replace(".csv", ".txt"), "w", encoding="utf-8") as f:
                for e in errors:
                    f.write(f"{e['file']}\t{e['error']}\n")
        log_print(f"❌ Errores: {len(errors)}. Ver: {out_csv}")
    else:
        log_print("✅ Sin errores.")

if __name__ == "__main__":
    main()
