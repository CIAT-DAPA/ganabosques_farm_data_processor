# -*- coding: utf-8 -*-
"""
Utilidad para descarga y gestión de shapefiles compartidos.

Carpeta compartida: farm/outputs/shapefiles/
Ambos pipelines (SAGARI y GEOFARMER) pueden usar el mismo shapefile ADM3.
"""
import os
import io
import zipfile
import logging

import requests

logger = logging.getLogger("shapefile_utils")


def ensure_adm3_shapefile(
    shapefiles_dir: str,
    workspace: str,
    url_geoserver: str,
    store: str,
    user: str,
    password: str,
) -> str | None:
    """
    Verifica que exista el shapefile ADM3 en *shapefiles_dir*.
    Si no existe, lo descarga desde GeoServer WFS (SHAPE-ZIP).

    Returns:
        Ruta absoluta al archivo .shp, o None si falló la descarga.
    """
    os.makedirs(shapefiles_dir, exist_ok=True)

    # 1) ¿Ya hay un .shp descargado?
    shp_files = [f for f in os.listdir(shapefiles_dir) if f.lower().endswith(".shp")]
    if shp_files:
        path = os.path.join(shapefiles_dir, shp_files[0])
        logger.info("Shapefile ADM3 encontrado: %s", path)
        print(f"📍 Shapefile ADM3 encontrado: {path}")
        return path

    # 2) Descargar desde GeoServer WFS
    url_shp = (
        f"{url_geoserver.rstrip('/')}/{workspace}/wfs?"
        f"service=WFS&version=1.0.0&request=GetFeature&"
        f"typeName={workspace}:{store}&outputFormat=shape-zip"
    )
    print(f"🌐 Descargando shapefile ADM3 desde GeoServer…")
    logger.info("Descargando shapefile ADM3 desde: %s", url_shp)

    try:
        response = requests.get(url_shp, auth=(user, password), timeout=120)
        if not response.ok:
            msg = f"Error HTTP {response.status_code} al descargar shapefile"
            logger.error(msg)
            print(f"❌ {msg}")
            return None

        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            z.extractall(shapefiles_dir)

        shp_files = [f for f in os.listdir(shapefiles_dir) if f.lower().endswith(".shp")]
        if shp_files:
            path = os.path.join(shapefiles_dir, shp_files[0])
            logger.info("Shapefile ADM3 descargado: %s", path)
            print(f"✅ Shapefile ADM3 descargado: {path}")
            return path

        logger.error("No se encontró .shp después de extraer el ZIP")
        print("❌ No se encontró .shp después de extraer el ZIP")
        return None

    except zipfile.BadZipFile:
        logger.error("La respuesta no es un ZIP válido")
        diag = os.path.join(shapefiles_dir, "wfs_error_preview.txt")
        with open(diag, "wb") as f:
            f.write(response.content)
        print(f"❌ Respuesta no es un ZIP válido. Diagnóstico en: {diag}")
        return None

    except Exception as e:
        logger.error("Error descargando shapefile: %s", e)
        print(f"❌ Error descargando shapefile: {e}")
        return None
