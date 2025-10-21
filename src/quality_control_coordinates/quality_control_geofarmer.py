# -*- coding: utf-8 -*-
from pathlib import Path
import os
import re
import json
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

# === CONFIG desde .env ===
# Usa 'from config import config' si ejecutas directo: python src\polygons_buffers.py
# Usa 'from src.config import config' si ejecutas como módulo: python -m src.polygons_buffers
from config import config
# from src.config import config

# Directorios/archivos desde .env (normalizados)
INPUT_DIR  = os.path.normpath(config['GEOFARMER_INPUT_TODOS'])
OUTPUT_DIR = os.path.normpath(config['GEOFARMER_OUTPUT_POLYGONS'])
CSV_PATH   = os.path.normpath(config['SAGARI_CSV_PATH'])

# ==== Parámetros funcionales ====
COL_RUV = "CODIGO_RUV"
CANDIDATOS_COL_SIT = ["SIT_CODE", "SIT", "CODIGO_SIT", "SITCODE", "SIT_CODE_ICA"]

# Si los .geojson NO traen CRS, asumimos este (deja None si ya traen CRS):
ASSUME_INPUT_EPSG = 4326   # típico para GeoJSON; cambia a None si no quieres asumir

# CRS de salida solicitado:
OUTPUT_EPSG = 3116

REPORTE_TXT = "reporte_union_SIT.txt"

# ==== Regex de extracción ====
RE_SIT = re.compile(r"SIT[_\-\s]*(\d+)", re.IGNORECASE)
RE_RUV = re.compile(r"RUV[_\-\s]*(\d+)", re.IGNORECASE)

def _only_digits(val):
    if val is None:
        return None
    m = re.search(r"(\d+)", str(val))
    return m.group(1) if m else None

def _prop_ci(props, key):
    if not isinstance(props, dict):
        return None
    key_l = key.lower()
    for k, v in props.items():
        if str(k).strip().lower() == key_l:
            return v
    return None

def extraer_sit_ruv_de_contenido(path_json):
    try:
        with open(path_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, None

    def from_props(props):
        sit = _only_digits(_prop_ci(props, "SIT"))
        ruv = _only_digits(_prop_ci(props, "RUV"))
        return sit, ruv

    if isinstance(data, dict):
        if data.get("type") == "FeatureCollection":
            for feat in data.get("features", []):
                s, r = from_props(feat.get("properties", {}))
                if s or r:
                    return s, r
        elif data.get("type") == "Feature":
            return from_props(data.get("properties", {}))
        if "properties" in data:
            return from_props(data.get("properties", {}))
    return None, None

def cargar_mapa_ruv_a_sit(csv_path):
    df = pd.read_csv(csv_path, dtype=str)
    if COL_RUV not in df.columns:
        raise ValueError(f"No existe la columna {COL_RUV} en el CSV.")
    col_sit = None
    for cand in CANDIDATOS_COL_SIT:
        if cand in df.columns:
            col_sit = cand
            break
    if not col_sit:
        raise ValueError(f"No encontré columna SIT en {CANDIDATOS_COL_SIT}")

    df[COL_RUV] = df[COL_RUV].map(_only_digits)
    df[col_sit] = df[col_sit].map(_only_digits)
    df = df.dropna(subset=[COL_RUV, col_sit]).drop_duplicates(subset=[COL_RUV], keep="first")
    return dict(zip(df[COL_RUV], df[col_sit])), col_sit

def leer_gdf_y_reproyectar(file_path):
    gdf = gpd.read_file(file_path)
    # Asignar CRS si no trae
    if gdf.crs is None and ASSUME_INPUT_EPSG:
        gdf.set_crs(epsg=ASSUME_INPUT_EPSG, inplace=True)
    # Reparar geometrías inválidas antes de uniones/dissolve
    gdf["geometry"] = gdf.geometry.buffer(0)
    # Reproyectar a salida
    if gdf.crs is not None and gdf.crs.to_epsg() != OUTPUT_EPSG:
        gdf = gdf.to_crs(epsg=OUTPUT_EPSG)
    return gdf

def poligono_unido(gdf):
    # Unión topológica de todas las geometrías
    geom = unary_union(gdf.geometry)
    # Normalizar a MultiPolygon/Polygon
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    # Si por alguna razón no es polígono, intentamos buffer(0) y seguimos
    geom = geom.buffer(0)
    return geom

def procesar():
    # Validaciones tempranas de rutas
    if not INPUT_DIR or not OUTPUT_DIR or not CSV_PATH:
        raise ValueError("Faltan variables en .env: GEOFARMER_INPUT_TODOS, GEOFARMER_OUTPUT_POLYGONS/GEOFARMER_POLYGONS_DIR, SAGARI_CSV_PATH")
    in_path = Path(INPUT_DIR)
    out_path = Path(OUTPUT_DIR)
    if not in_path.exists():
        raise FileNotFoundError(f"No existe la carpeta de entrada: {in_path}")
    out_path.mkdir(parents=True, exist_ok=True)
    reporte_path = out_path / REPORTE_TXT

    mapping, col_sit_csv = cargar_mapa_ruv_a_sit(CSV_PATH)

    # Acumular geometrías por SIT
    grupos = {}
    logs = []
    cont_archivos = 0

    for geojson in in_path.rglob("*.geojson"):
        cont_archivos += 1
        nombre = geojson.name

        # 1) SIT / RUV desde nombre
        sit = (RE_SIT.search(nombre).group(1) if RE_SIT.search(nombre) else None)
        ruv = (RE_RUV.search(nombre).group(1) if RE_RUV.search(nombre) else None)

        # 2) Completar desde contenido
        if not sit or not ruv:
            s2, r2 = extraer_sit_ruv_de_contenido(geojson)
            if not sit and s2: sit = s2
            if not ruv and r2: ruv = r2

        # 3) Resolver SIT con CSV si no existe pero hay RUV
        if not sit and ruv:
            sit_csv = mapping.get(ruv)
            if sit_csv:
                sit = sit_csv
                logs.append(f"RESUELTO POR RUV: {nombre}  RUV={ruv} -> SIT={sit}")
            else:
                logs.append(f"ADVERTENCIA: {nombre}  RUV={ruv} sin match en CSV")
                continue

        if not sit:
            logs.append(f"OMITIDO: {nombre}  (sin SIT y sin RUV válido)")
            continue

        try:
            gdf = leer_gdf_y_reproyectar(geojson)
            if gdf.empty:
                logs.append(f"ADVERTENCIA: {nombre} sin geometrías")
                continue
            grupos.setdefault(sit, []).append(gdf[["geometry"]])  # solo geometría para unir
        except Exception as e:
            logs.append(f"ERROR al leer {nombre}: {e}")

    # 4) Unir y escribir UN solo archivo por SIT (EPSG:3116)
    escritos = 0
    for sit, partes in grupos.items():
        try:
            gdf_all = pd.concat(partes, ignore_index=True)
            geom_union = poligono_unido(gdf_all)
            out_gdf = gpd.GeoDataFrame({"SIT": [sit]}, geometry=[geom_union], crs=f"EPSG:{OUTPUT_EPSG}")
            destino = out_path / f"{sit}.geojson"
            # Siempre sobreescribe un único archivo por SIT
            if destino.exists():
                destino.unlink()
            out_gdf.to_file(destino, driver="GeoJSON")
            escritos += 1
        except Exception as e:
            logs.append(f"ERROR al unir/escribir SIT={sit}: {e}")

    # 5) Reporte
    with open(reporte_path, "w", encoding="utf-8") as f:
        f.write("=== REPORTE UNIÓN POR SIT (sin duplicados) ===\n")
        f.write(f"Archivos leídos: {cont_archivos}\n")
        f.write(f"SIT escritos (unión): {escritos}\n")
        f.write(f"CRS de salida: EPSG:{OUTPUT_EPSG}\n")
        f.write(f"CSV: {CSV_PATH}  | RUV: {COL_RUV}  | SIT en CSV: {col_sit_csv}\n\n")
        f.write("---- Detalles ----\n")
        for line in logs:
            f.write(line + "\n")

    print("Listo.")
    print(f"- Archivos origen leídos: {cont_archivos}")
    print(f"- SIT únicos escritos (sin duplicados): {escritos}")
    print(f"- Salida: {out_path}")
    print(f"- Reporte: {reporte_path}")

if __name__ == "__main__":
    procesar()
