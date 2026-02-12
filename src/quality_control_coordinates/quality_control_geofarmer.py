# -*- coding: utf-8 -*-
"""
Paso 2 GEOFARMER: Control de calidad de GeoJSONs descargados.

Valida:
  - Presencia de código externo (farm_code).
  - farm_code no sea solo ceros (ej: "000", "0" → inválido; "00000001" → válido).
  - Geometría válida en EPSG:4326 (coordenadas lon/lat).

Enriquece:
  - Extrae código ADM3 vía spatial join con shapefile (preserva ceros a la
    izquierda, ej: "05304003").
  - Valida centroide: si ya viene del API y cae dentro de la geometría lo
    conserva; si no, lo recalcula desde el centroide de la geometría.
  - Calcula área en hectáreas sobre elipsoide WGS84 (Geod).
  - Almacena adm3_code, latitude, longitude, farm_ha en las properties.

Limpieza:
  - Elimina campos sensibles (contact_name, address).
  - Genera CSV de errores por empresa para revisión posterior.
"""
from pathlib import Path
import os
import re
import json
import csv
import logging
from datetime import datetime

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape, Point, Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.validation import make_valid
from pyproj import Geod
from tqdm import tqdm

from config import config

logger = logging.getLogger("quality_control_geofarmer")

# CRS esperado
OUTPUT_EPSG = 4326

# Campos sensibles a eliminar de las properties
CAMPOS_SENSIBLES = {"contact_name", "address"}

# Regex para detectar farm_code compuesto solo de ceros
RE_SOLO_CEROS = re.compile(r"^0+$")

# Candidatos para la columna de código ADM3 en el shapefile
ADM3_CODE_CANDIDATES = [
    "ext_id", "cod_ver", "ADM3_CODE", "ADM3",
    "MPIO_CCDGO", "DPTOMPIO", "CODIGO", "CODE",
]

# Geod para cálculo de área sobre elipsoide WGS84
GEOD = Geod(ellps="WGS84")


# ========= HELPERS =========

def _load_geojson(path):
    """Carga un archivo GeoJSON como dict."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _extract_first_properties(geojson_obj):
    """Extrae las properties del primer Feature (para validación)."""
    if geojson_obj.get("type") == "FeatureCollection":
        features = geojson_obj.get("features", [])
        if features:
            return features[0].get("properties", {})
    elif geojson_obj.get("type") == "Feature":
        return geojson_obj.get("properties", {})
    return {}


def _remove_sensitive_fields(geojson_obj):
    """
    Elimina campos sensibles de todas las features del GeoJSON.
    Modifica el dict in-place y lo devuelve.
    """
    features = []
    if geojson_obj.get("type") == "FeatureCollection":
        features = geojson_obj.get("features", [])
    elif geojson_obj.get("type") == "Feature":
        features = [geojson_obj]

    for feat in features:
        props = feat.get("properties", {})
        for campo in CAMPOS_SENSIBLES:
            props.pop(campo, None)

    return geojson_obj


def _validate_geometry(geojson_obj):
    """
    Extrae la geometría del GeoJSON y valida que sea un polígono en EPSG:4326.
    Devuelve (geom_shapely, None) si OK o (None, mensaje_error) si falla.
    """
    features = []
    if geojson_obj.get("type") == "FeatureCollection":
        features = geojson_obj.get("features", [])
    elif geojson_obj.get("type") == "Feature":
        features = [geojson_obj]

    geoms = []
    for feat in features:
        raw_geom = feat.get("geometry")
        if raw_geom:
            geom = shape(raw_geom)
            try:
                geom = make_valid(geom)
            except Exception:
                geom = geom.buffer(0)
            if not geom.is_empty:
                geoms.append(geom)

    if not geoms:
        return None, "Geometría vacía o inválida"

    geom_union = unary_union(geoms) if len(geoms) > 1 else geoms[0]

    # Validar rango de coordenadas (heurística CRS 4326)
    minx, miny, maxx, maxy = geom_union.bounds
    if max(abs(minx), abs(miny), abs(maxx), abs(maxy)) > 180:
        return None, f"Coordenadas fuera de rango WGS84 (bounds: {geom_union.bounds}). Posible CRS incorrecto."

    return geom_union, None


# ========= ADM3 =========

def _load_adm3_gdf(shp_path):
    """Carga el shapefile ADM3, reproyecta a 4326, identifica columna de código."""
    gdf = gpd.read_file(shp_path)
    if gdf.crs is None:
        raise ValueError("Shapefile ADM3 sin CRS definido.")
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    code_col = None
    for cand in ADM3_CODE_CANDIDATES:
        if cand in gdf.columns:
            code_col = cand
            break
    if not code_col:
        raise ValueError(
            f"No se encontró columna de código ADM3 (probadas: {ADM3_CODE_CANDIDATES})"
        )
    # Preservar ceros a la izquierda → siempre string
    gdf[code_col] = gdf[code_col].astype(str).str.strip()
    return gdf, code_col


def _find_adm3_code(geom, adm3_gdf, code_col):
    """Busca código ADM3 por spatial join (centroid within, luego intersects)."""
    if geom is None or geom.is_empty:
        return None
    # Primer intento: centroide dentro de polígono ADM3
    try:
        pt = gpd.GeoDataFrame(geometry=[geom.centroid], crs="EPSG:4326")
        join1 = gpd.sjoin(
            pt, adm3_gdf[[code_col, "geometry"]], how="left", predicate="within"
        )
        if not join1.empty and pd.notna(join1.iloc[0][code_col]):
            return str(join1.iloc[0][code_col])
    except Exception:
        pass
    # Segundo intento: intersección con envelope
    try:
        poly = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
        candidates = adm3_gdf[adm3_gdf.intersects(geom.envelope)]
        if candidates.empty:
            return None
        join2 = gpd.sjoin(
            poly, candidates[[code_col, "geometry"]], how="left", predicate="intersects"
        )
        if not join2.empty and pd.notna(join2.iloc[0][code_col]):
            return str(join2.iloc[0][code_col])
    except Exception:
        return None
    return None


# ========= CENTROIDE =========

def _resolve_centroid(geom, props):
    """
    Determina lat, lon del centroide:
      1. Si props["centroid"] existe y es [lon, lat], verifica que caiga
         dentro de la geometría.
      2. Si cae dentro → lo usa.
      3. Si no cae dentro o no existe → calcula desde geom.centroid.

    Returns:
        (latitude, longitude)
    """
    centroid_raw = props.get("centroid")
    if centroid_raw and isinstance(centroid_raw, (list, tuple)) and len(centroid_raw) >= 2:
        try:
            lon, lat = float(centroid_raw[0]), float(centroid_raw[1])
            pt = Point(lon, lat)
            if geom.contains(pt):
                return lat, lon
        except (ValueError, TypeError):
            pass
    # Calcular desde geometría
    c = geom.centroid
    return float(c.y), float(c.x)


# ========= ÁREA =========

def _area_hectares(geom):
    """Calcula área en hectáreas usando Geod (elipsoide WGS84)."""
    if geom is None or geom.is_empty:
        return 0.0

    def _poly_area(polygon):
        lon, lat = polygon.exterior.coords.xy
        area, _ = GEOD.polygon_area_perimeter(list(lon), list(lat))
        a = abs(area)
        for interior in polygon.interiors:
            ilon, ilat = interior.coords.xy
            ia, _ = GEOD.polygon_area_perimeter(list(ilon), list(ilat))
            a -= abs(ia)
        return a

    total_m2 = 0.0
    if isinstance(geom, Polygon):
        total_m2 = _poly_area(geom)
    elif isinstance(geom, MultiPolygon):
        for p in geom.geoms:
            total_m2 += _poly_area(p)
    return total_m2 / 10_000.0


# ========= ENRIQUECER PROPERTIES =========

def _set_enriched_properties(geojson_obj, adm3_code, latitude, longitude, farm_ha):
    """Agrega adm3_code, latitude, longitude, farm_ha a todas las features."""
    features = []
    if geojson_obj.get("type") == "FeatureCollection":
        features = geojson_obj.get("features", [])
    elif geojson_obj.get("type") == "Feature":
        features = [geojson_obj]
    for feat in features:
        props = feat.setdefault("properties", {})
        props["adm3_code"] = adm3_code
        props["latitude"] = latitude
        props["longitude"] = longitude
        props["farm_ha"] = round(farm_ha, 4)
    return geojson_obj


# ========= FUNCIÓN PRINCIPAL =========

def procesar(input_dir: str = None, output_dir: str = None, adm3_shp: str = None):
    """
    Control de calidad para GeoJSONs de GeoFarmer (paso 2).

    Args:
        input_dir: Carpeta con GeoJSONs del paso 1, organizados por empresa.
        output_dir: Carpeta de salida con GeoJSONs validados, misma estructura.
        adm3_shp: Ruta al shapefile ADM3 para spatial join.
    """
    if not input_dir:
        raise ValueError("Se requiere input_dir (directorio con GeoJSONs descargados)")
    if not output_dir:
        raise ValueError("Se requiere output_dir (directorio de salida)")
    if not adm3_shp:
        raise ValueError("Se requiere adm3_shp (ruta al shapefile ADM3)")

    in_path = Path(os.path.normpath(input_dir))
    out_path = Path(os.path.normpath(output_dir))

    if not in_path.exists():
        raise FileNotFoundError(f"No existe la carpeta de entrada: {in_path}")
    if not os.path.isfile(adm3_shp):
        raise FileNotFoundError(f"No existe el shapefile ADM3: {adm3_shp}")

    out_path.mkdir(parents=True, exist_ok=True)

    errors_dir = out_path / "_errores"

    # Cargar shapefile ADM3
    print("📍 Cargando shapefile ADM3…")
    adm3_gdf, code_col = _load_adm3_gdf(adm3_shp)
    print(f"   Columna código: {code_col} | Registros: {len(adm3_gdf)}")

    # Descubrir subcarpetas de empresa (primer nivel de subdirectorios)
    empresa_dirs = sorted([d for d in in_path.iterdir() if d.is_dir() and d.name != "_errores"])

    if not empresa_dirs:
        # Si no hay subcarpetas, buscar geojsons directamente
        empresa_dirs = [in_path]

    # Acumuladores globales
    resumen_empresas = []
    all_errores = []

    for emp_dir in empresa_dirs:
        empresa = emp_dir.name if emp_dir != in_path else "_sin_empresa"
        geojson_files = list(emp_dir.glob("*.geojson"))

        # Acumuladores por empresa
        errores_emp = []
        total_emp = 0
        escritos_emp = 0
        sin_codigo = 0
        codigo_ceros = 0
        sin_geometria = 0
        crs_invalido = 0
        sin_adm3 = 0

        emp_out = out_path / empresa
        emp_out.mkdir(parents=True, exist_ok=True)

        for gj_path in tqdm(geojson_files, desc=f"  📂 {empresa}", unit="archivo", leave=True):
            total_emp += 1
            try:
                data = _load_geojson(gj_path)
                props = _extract_first_properties(data)

                farm_id = str(props.get("farm_id", ""))
                farm_name = str(props.get("farm_name", ""))
                farm_code = str(props.get("farm_code", "")).strip()

                # 1) Validar código externo presente
                if not farm_code:
                    sin_codigo += 1
                    errores_emp.append({
                        "empresa": empresa,
                        "archivo": gj_path.name,
                        "farm_id": farm_id,
                        "farm_name": farm_name,
                        "farm_code": "",
                        "error": "Sin código externo (farm_code vacío o ausente)"
                    })
                    continue

                # 2) Validar código externo no sea solo ceros
                if RE_SOLO_CEROS.match(farm_code):
                    codigo_ceros += 1
                    errores_emp.append({
                        "empresa": empresa,
                        "archivo": gj_path.name,
                        "farm_id": farm_id,
                        "farm_name": farm_name,
                        "farm_code": farm_code,
                        "error": f"Código externo inválido (solo ceros: '{farm_code}')"
                    })
                    continue

                # 3) Validar geometría y CRS
                geom, geom_err = _validate_geometry(data)
                if geom is None:
                    if "CRS" in (geom_err or ""):
                        crs_invalido += 1
                    else:
                        sin_geometria += 1
                    errores_emp.append({
                        "empresa": empresa,
                        "archivo": gj_path.name,
                        "farm_id": farm_id,
                        "farm_name": farm_name,
                        "farm_code": farm_code,
                        "error": geom_err
                    })
                    continue

                # 4) Buscar código ADM3 vía spatial join
                adm3_code = _find_adm3_code(geom, adm3_gdf, code_col)
                if not adm3_code:
                    sin_adm3 += 1
                    errores_emp.append({
                        "empresa": empresa,
                        "archivo": gj_path.name,
                        "farm_id": farm_id,
                        "farm_name": farm_name,
                        "farm_code": farm_code,
                        "error": f"No se encontró ADM3 (bounds: {geom.bounds})"
                    })
                    continue

                # 5) Centroide: verificar del API o calcular
                latitude, longitude = _resolve_centroid(geom, props)

                # 6) Área en hectáreas
                farm_ha = _area_hectares(geom)

                # 7) Limpiar sensibles + enriquecer properties
                cleaned = _remove_sensitive_fields(data)
                enriched = _set_enriched_properties(
                    cleaned, adm3_code, latitude, longitude, farm_ha
                )

                # 8) Guardar
                destino = emp_out / f"FARM_ID_{farm_id}.geojson"
                with open(destino, "w", encoding="utf-8") as f:
                    json.dump(enriched, f, ensure_ascii=False)
                escritos_emp += 1

            except Exception as e:
                errores_emp.append({
                    "empresa": empresa,
                    "archivo": gj_path.name,
                    "farm_id": "",
                    "farm_name": "",
                    "farm_code": "",
                    "error": str(e)
                })

        # Guardar CSV de errores por empresa
        err_csv_path = None
        if errores_emp:
            errors_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            err_csv_path = errors_dir / f"errores_qc_{empresa}_{ts}.csv"
            with open(err_csv_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "empresa", "archivo", "farm_id", "farm_name", "farm_code", "error"
                ])
                writer.writeheader()
                writer.writerows(errores_emp)

        all_errores.extend(errores_emp)
        resumen_empresas.append({
            "empresa": empresa,
            "total": total_emp,
            "escritos": escritos_emp,
            "sin_codigo": sin_codigo,
            "codigo_ceros": codigo_ceros,
            "sin_geometria": sin_geometria,
            "crs_invalido": crs_invalido,
            "sin_adm3": sin_adm3,
            "errores": len(errores_emp),
            "errors_csv": str(err_csv_path) if err_csv_path else None,
        })

    # Resumen final
    t_total = sum(r["total"] for r in resumen_empresas)
    t_escritos = sum(r["escritos"] for r in resumen_empresas)
    t_errores = sum(r["errores"] for r in resumen_empresas)

    print(f"\n{'='*80}")
    print(f"  RESUMEN CONTROL DE CALIDAD GEOFARMER")
    print(f"{'='*80}")
    for r in resumen_empresas:
        icono = "✅" if r["errores"] == 0 else "⚠️"
        print(
            f"  {icono} {r['empresa']:25s} | total: {r['total']:>4} | OK: {r['escritos']:>4}"
            f" | sin código: {r['sin_codigo']:>3} | ceros: {r['codigo_ceros']:>3}"
            f" | sin geom: {r['sin_geometria']:>3} | CRS: {r['crs_invalido']:>3}"
            f" | sin ADM3: {r['sin_adm3']:>3}"
        )
        if r["errors_csv"]:
            print(f"     📄 {r['errors_csv']}")
    print(f"  {'─'*76}")
    print(f"  TOTAL: {t_total} procesados | {t_escritos} válidos | {t_errores} con problemas")
    print(f"  Salida: {out_path}")
    print(f"{'='*80}")


if __name__ == "__main__":
    procesar()
