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
  - Solo conserva campos permitidos (whitelist) en properties: farm_id, farm_name,
    farm_code, boundary_id, etc. Cualquier otro campo se elimina automáticamente.
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
from pyproj import Geod, Transformer
from tqdm import tqdm

from config import config

logger = logging.getLogger("quality_control_geofarmer")

# CRS esperado
OUTPUT_EPSG = 4326

# Campos permitidos en properties (whitelist) - solo estos se conservan
CAMPOS_PERMITIDOS = {
    "centroid", "type", "farm_id", "FARM_ID",  # farm_id puede venir en mayúscula
    "farm_name", "farm_code", "boundary_id", "boundary_size",
    "verification_status",
    # Los siguientes se agregan durante el QC:
    "adm3_code", "latitude", "longitude", "farm_ha"
}

# Regex para detectar farm_code compuesto solo de ceros
RE_SOLO_CEROS = re.compile(r"^0+$")

# Candidatos para la columna de código ADM3 en el shapefile
ADM3_CODE_CANDIDATES = [
    "ext_id", "cod_ver", "ADM3_CODE", "ADM3",
    "MPIO_CCDGO", "DPTOMPIO", "CODIGO", "CODE",
]

RE_FARM_ID = re.compile(r"FARM_ID[_\-](.+)\.geojson$", re.IGNORECASE)

# Geod para cálculo de área sobre elipsoide WGS84
GEOD = Geod(ellps="WGS84")

# Cache de transformers: EPSG → 4326 (se crean bajo demanda)
_TRANSFORMER_CACHE: dict[int, Transformer] = {}

# CRS comunes que podrían aparecer en los GeoJSONs
_KNOWN_CRS = {
    3116: "MAGNA-SIRGAS / Colombia Bogotá zone",
    3857: "Web Mercator",
    32618: "UTM zone 18N",
    32619: "UTM zone 19N",
}

# Regex para extraer EPSG de URNs como "urn:ogc:def:crs:EPSG::3857"
_RE_EPSG_URN = re.compile(r"EPSG::?(\d+)")


# ========= HELPERS =========

def _load_geojson(path):
    """Carga un archivo GeoJSON como dict."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def extract_geofarmer_code(filename: str) -> str | None:
    """Extrae el UUID de GeoFarmer del nombre del archivo."""
    m = RE_FARM_ID.search(filename)
    return m.group(1) if m else None

def _extract_first_properties(geojson_obj):
    """Extrae las properties del primer Feature (para validación)."""
    if geojson_obj.get("type") == "FeatureCollection":
        features = geojson_obj.get("features", [])
        if features:
            return features[0].get("properties", {})
    elif geojson_obj.get("type") == "Feature":
        return geojson_obj.get("properties", {})
    return {}


def _filter_allowed_fields(geojson_obj):
    """
    Solo conserva los campos permitidos en las properties de todas las features.
    Elimina cualquier campo que no esté en CAMPOS_PERMITIDOS (whitelist).
    Modifica el dict in-place y lo devuelve.
    """
    features = []
    if geojson_obj.get("type") == "FeatureCollection":
        features = geojson_obj.get("features", [])
    elif geojson_obj.get("type") == "Feature":
        features = [geojson_obj]

    for feat in features:
        props = feat.get("properties", {})
        # Crear nuevo dict solo con campos permitidos
        props_filtered = {k: v for k, v in props.items() if k in CAMPOS_PERMITIDOS}
        feat["properties"] = props_filtered

    return geojson_obj


def _get_transformer(src_epsg: int) -> Transformer:
    """Devuelve un Transformer src_epsg→4326, con cache."""
    if src_epsg not in _TRANSFORMER_CACHE:
        _TRANSFORMER_CACHE[src_epsg] = Transformer.from_crs(
            src_epsg, 4326, always_xy=True
        )
    return _TRANSFORMER_CACHE[src_epsg]


def _transform_geom_to_4326(geom, src_epsg: int):
    """Reproyecta geometría de src_epsg a EPSG:4326 vía shapely transform."""
    from shapely.ops import transform as shapely_transform
    transformer = _get_transformer(src_epsg)
    func = lambda x, y, z=None: transformer.transform(x, y)
    return shapely_transform(func, geom)


def _detect_crs_from_geojson(geojson_obj) -> int | None:
    """
    Intenta detectar el EPSG del GeoJSON a partir de metadatos.
    Busca en geojson_obj['crs']['properties']['name'] patrones como
    "urn:ogc:def:crs:EPSG::3857" o "EPSG:3116".
    Devuelve el código EPSG (int) o None si no lo encuentra.
    """
    crs_info = geojson_obj.get("crs")
    if not crs_info or not isinstance(crs_info, dict):
        return None
    props = crs_info.get("properties", {})
    name = props.get("name", "")
    m = _RE_EPSG_URN.search(name)
    if m:
        return int(m.group(1))
    return None


def _guess_epsg_from_bounds(bounds) -> int | None:
    """
    Heurística para adivinar el EPSG según el rango de coordenadas.
      - Colombia MAGNA-SIRGAS 3116: x ~300k–1300k, y ~300k–1800k
      - Web Mercator 3857: x hasta ±20M, y hasta ±20M (valores grandes)
      - UTM zonas 18N/19N: x ~100k–900k, y ~0–10M
    """
    minx, miny, maxx, maxy = bounds
    abs_max = max(abs(minx), abs(miny), abs(maxx), abs(maxy))

    # Web Mercator: valores muy grandes (> 5M)
    if abs_max > 5_000_000:
        return 3857

    # MAGNA-SIRGAS Colombia Bogotá zone (3116)
    # x ≈ 400k–1200k, y ≈ 400k–1800k
    if 100_000 < abs_max < 5_000_000:
        # Si las Y están en rango colombiano, asumir 3116
        if 100_000 < maxy < 2_000_000:
            return 3116
        # Si no, podría ser UTM
        return 3116  # fallback más probable para Colombia

    return None


def _validate_geometry(geojson_obj):
    """
    Extrae la geometría del GeoJSON y valida que sea un polígono en EPSG:4326.

    Si las coordenadas están fuera del rango WGS84:
      1. Intenta detectar el CRS desde los metadatos del GeoJSON.
      2. Si no hay metadatos, usa heurísticas por rango de coordenadas.
      3. Reproyecta dinámicamente al 4326.

    Devuelve (geom_shapely, advertencia_o_None):
      - (geom, None)              → OK, ya estaba en 4326
      - (geom, "ADVERTENCIA: …")  → OK, se reproyectó exitosamente
      - (None, "ERROR: …")        → Fallo irrecuperable
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
        # 1) Intentar detectar CRS desde metadatos del GeoJSON
        src_epsg = _detect_crs_from_geojson(geojson_obj)
        detect_method = "metadatos GeoJSON"

        # 2) Si no hay metadatos, adivinar por heurística de bounds
        if src_epsg is None:
            src_epsg = _guess_epsg_from_bounds(geom_union.bounds)
            detect_method = "heurística de coordenadas"

        if src_epsg is None:
            return None, (
                f"Coordenadas fuera de rango WGS84 (bounds: {geom_union.bounds}). "
                f"No se pudo determinar el CRS de origen."
            )

        crs_label = _KNOWN_CRS.get(src_epsg, f"EPSG:{src_epsg}")

        try:
            geom_4326 = _transform_geom_to_4326(geom_union, src_epsg)
            if geom_4326 is None or geom_4326.is_empty:
                return None, (
                    f"Coordenadas fuera de rango WGS84 (bounds: {geom_union.bounds}). "
                    f"Reproyección EPSG:{src_epsg} ({crs_label}) → 4326 resultó en geometría vacía."
                )
            return geom_4326, (
                f"ADVERTENCIA: CRS original era EPSG:{src_epsg} ({crs_label}), "
                f"detectado vía {detect_method} "
                f"(bounds originales: {geom_union.bounds}). "
                f"Se reproyectó a EPSG:4326 exitosamente."
            )
        except Exception as e:
            return None, (
                f"Coordenadas fuera de rango WGS84 (bounds: {geom_union.bounds}). "
                f"Falló reproyección EPSG:{src_epsg} ({crs_label}) → 4326: {e}"
            )

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

def _extract_polygon_parts(geom):
    """Extrae todas las partes poligonales de una geometría compuesta."""
    if geom is None or geom.is_empty:
        return []

    gtype = geom.geom_type
    if gtype == "Polygon":
        return [geom]
    if gtype == "MultiPolygon":
        return [g for g in geom.geoms if not g.is_empty]
    if gtype == "GeometryCollection":
        parts = []
        for g in geom.geoms:
            parts.extend(_extract_polygon_parts(g))
        return parts
    return []


def _largest_polygon(geom):
    """
    Devuelve el polígono más grande dentro de una geometría.

    Esto evita centroides fuera del área cuando hay multipolígonos separados.
    """
    polygons = _extract_polygon_parts(geom)
    if not polygons:
        return None
    return max(polygons, key=lambda p: p.area)

def _resolve_centroid(geom, props):
    """
    Determina lat, lon del centroide usando el polígono más grande:
      1. Si props["centroid"] existe y es [lon, lat], verifica que caiga
         dentro del polígono principal.
      2. Si cae dentro → lo usa.
      3. Si no cae dentro o no existe → calcula desde el centroide del
         polígono más grande.

    Returns:
        (latitude, longitude)
    """
    target_geom = _largest_polygon(geom)
    if target_geom is None:
        # Fallback defensivo
        c = geom.centroid
        return float(c.y), float(c.x)

    centroid_raw = props.get("centroid")
    if centroid_raw and isinstance(centroid_raw, (list, tuple)) and len(centroid_raw) >= 2:
        try:
            lon, lat = float(centroid_raw[0]), float(centroid_raw[1])
            pt = Point(lon, lat)
            # covers acepta puntos en borde; contains no.
            if target_geom.covers(pt):
                return lat, lon
        except (ValueError, TypeError):
            pass
    # Calcular desde el polígono principal
    c = target_geom.centroid
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


# ========= REEMPLAZAR GEOMETRÍA =========

def _replace_geometry_in_geojson(geojson_obj, geom_4326):
    """
    Normaliza el GeoJSON a un solo feature con la geometría unificada/reproyectada.

    Esto asegura que, si llegan múltiples features, el resultado de QC siempre
    sea un FeatureCollection de 1 feature con la unión total de la finca.

    También actualiza el CRS explícitamente a EPSG:4326.
    """
    from shapely.geometry import mapping

    geom_dict = mapping(geom_4326)

    # Tomar properties del primer feature existente (si hay), para no perder metadatos
    base_properties = {}
    if geojson_obj.get("type") == "FeatureCollection":
        features = geojson_obj.get("features", [])
        if features:
            base_properties = dict(features[0].get("properties", {}))
    elif geojson_obj.get("type") == "Feature":
        base_properties = dict(geojson_obj.get("properties", {}))

    # Construir salida con un único feature
    geojson_obj["type"] = "FeatureCollection"
    geojson_obj["features"] = [{
        "type": "Feature",
        "properties": base_properties,
        "geometry": geom_dict
    }]

    # Actualizar CRS explícitamente
    geojson_obj["crs"] = {
        "type": "name",
        "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}
    }

    return geojson_obj


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
        crs_convertido = 0
        sin_adm3 = 0

        emp_out = out_path / empresa
        emp_out.mkdir(parents=True, exist_ok=True)

        for gj_path in tqdm(geojson_files, desc=f"  📂 {empresa}", unit="archivo", leave=True):
            total_emp += 1
            try:
                data = _load_geojson(gj_path)
                props = _extract_first_properties(data)

                farm_id = str(props.get("farm_id", ""))
                if not farm_id:
                    farm_id = extract_geofarmer_code(gj_path.name) or ""
                farm_name = str(props.get("farm_name", ""))
                farm_code = str(props.get("farm_code", "")).strip()

                # 1) Validar código externo presente (advertencia, no bloquea)
                if not farm_code:
                    sin_codigo += 1
                    errores_emp.append({
                        "empresa": empresa,
                        "archivo": gj_path.name,
                        "farm_id": farm_id,
                        "farm_name": farm_name,
                        "farm_code": "",
                        "tipo": "advertencia",
                        "error": "ADVERTENCIA: Sin código externo (farm_code vacío o ausente)"
                    })

                # 2) Validar código externo no sea solo ceros (advertencia, no bloquea)
                elif farm_code and RE_SOLO_CEROS.match(farm_code):
                    codigo_ceros += 1
                    errores_emp.append({
                        "empresa": empresa,
                        "archivo": gj_path.name,
                        "farm_id": farm_id,
                        "farm_name": farm_name,
                        "farm_code": farm_code,
                        "tipo": "advertencia",
                        "error": f"ADVERTENCIA: Código externo solo ceros ('{farm_code}')"
                    })

                # 3) Validar geometría y CRS
                geom, geom_msg = _validate_geometry(data)
                if geom is None:
                    # Fallo irrecuperable → saltar archivo
                    if "CRS" in (geom_msg or "") or "rango WGS84" in (geom_msg or ""):
                        crs_invalido += 1
                    else:
                        sin_geometria += 1
                    errores_emp.append({
                        "empresa": empresa,
                        "archivo": gj_path.name,
                        "farm_id": farm_id,
                        "farm_name": farm_name,
                        "farm_code": farm_code,
                        "tipo": "error",
                        "error": geom_msg
                    })
                    continue
                elif geom_msg:
                    # Geometría OK pero hubo reproyección → advertencia
                    crs_convertido += 1
                    errores_emp.append({
                        "empresa": empresa,
                        "archivo": gj_path.name,
                        "farm_id": farm_id,
                        "farm_name": farm_name,
                        "farm_code": farm_code,
                        "tipo": "advertencia",
                        "error": geom_msg
                    })

                # Normalizar SIEMPRE a un único feature con geometría unificada
                # (cubre casos de múltiples features y/o multipolígonos).
                data = _replace_geometry_in_geojson(data, geom)

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
                        "tipo": "error",
                        "error": f"No se encontró ADM3 (bounds: {geom.bounds})"
                    })
                    continue

                # 5) Centroide: verificar del API o calcular
                latitude, longitude = _resolve_centroid(geom, props)

                # 6) Área en hectáreas
                farm_ha = _area_hectares(geom)

                # 7) Filtrar solo campos permitidos + enriquecer properties
                cleaned = _filter_allowed_fields(data)
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
                    "tipo": "error",
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
                    "empresa", "archivo", "farm_id", "farm_name", "farm_code", "tipo", "error"
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
            "crs_convertido": crs_convertido,
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
            f" | sin geom: {r['sin_geometria']:>3} | CRS err: {r['crs_invalido']:>3}"
            f" | CRS conv: {r['crs_convertido']:>3} | sin ADM3: {r['sin_adm3']:>3}"
        )
        if r["errors_csv"]:
            print(f"     📄 {r['errors_csv']}")
    print(f"  {'─'*76}")
    print(f"  TOTAL: {t_total} procesados | {t_escritos} válidos | {t_errores} con problemas")
    print(f"  Salida: {out_path}")
    print(f"{'='*80}")


if __name__ == "__main__":
    procesar()
