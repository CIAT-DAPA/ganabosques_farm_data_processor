import os
import io
import zipfile
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

def quality_control_coordinates(input_path, path_output, workspace, url_geoserver, store, user, password):
    print("🔍 Iniciando control de calidad de coordenadas...")

    # 0) Buscar archivo CSV
    csv_files = [f for f in os.listdir(input_path) if f.lower().endswith(".csv")]
    if not csv_files:
        print("❌ No se encontró ningún archivo CSV en:", input_path)
        return
    ruta_csv = os.path.join(input_path, csv_files[0])
    print("📄 CSV cargado:", ruta_csv)

    # 1) Crear carpeta de salida
    os.makedirs(path_output, exist_ok=True)

    # 2) Descargar shapefile (WFS → SHAPE-ZIP)
    url_shp = (
        f"{url_geoserver.rstrip('/')}/{workspace}/wfs?"
        f"service=WFS&version=1.0.0&request=GetFeature&"
        f"typeName={workspace}:{store}&outputFormat=shape-zip"
    )
    print("🌐 Solicitando shapefile desde:", url_shp)

    response = requests.get(url_shp, auth=(user, password))
    if response.ok:
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                z.extractall(path_output)
            print("✅ Shapefile descargado y extraído con éxito.")
        except zipfile.BadZipFile:
            print("❌ La respuesta no es un ZIP válido (¿layer o permisos?).")
            # Guarda la respuesta para inspección
            diag = os.path.join(path_output, "wfs_error_preview.txt")
            with open(diag, "wb") as f:
                f.write(response.content)
            print("📝 Detalle guardado en:", diag)
            return
    else:
        print("❌ Error al descargar shapefile:", response.status_code)
        return

    # 3) Cargar CSV y normalizar coordenadas
    df = pd.read_csv(ruta_csv)

    # Asegurar numéricos
    if "LONGITUD" not in df.columns or "LATITUD" not in df.columns:
        print("❌ El CSV no contiene columnas LONGITUD y LATITUD.")
        return
    df["LONGITUD"] = pd.to_numeric(df["LONGITUD"], errors="coerce")
    df["LATITUD"]  = pd.to_numeric(df["LATITUD"], errors="coerce")

    # Eliminar coordenadas faltantes
    total_original = len(df)
    df = df.dropna(subset=["LONGITUD", "LATITUD"])
    total_filtrado = len(df)
    print(f"🧹 Registros eliminados por coordenadas incompletas: {total_original - total_filtrado}")

    # 4) Crear puntos en EPSG:4326 (grados) y reproyectar a EPSG:3116 (metros)  ← PUNTO 1 CORREGIDO
    puntos_4326 = gpd.GeoDataFrame(
        df.copy(),
        geometry=[Point(xy) for xy in zip(df["LONGITUD"], df["LATITUD"])],
        crs="EPSG:4326"
    )
    gdf_puntos = puntos_4326.to_crs("EPSG:3116")

    # 5) Cargar shapefile y llevar a EPSG:3116
    shp_files = [f for f in os.listdir(path_output) if f.lower().endswith(".shp")]
    if not shp_files:
        print("❌ No se encontró ningún .shp extraído en", path_output)
        return
    ruta_shp = os.path.join(path_output, shp_files[0])

    gdf_veredas = gpd.read_file(ruta_shp)
    if gdf_veredas.crs is None:
        print("⚠️ La capa no trae CRS definido; se asume EPSG:4326.")
        gdf_veredas = gdf_veredas.set_crs("EPSG:4326")
    gdf_veredas = gdf_veredas.to_crs("EPSG:3116")
    print("📍 Shapefile cargado y CRS transformado a EPSG:3116.")

    # 6) Spatial join (intersects)
    # Ajusta aquí los nombres si tu capa usa otras columnas:
    cols_existentes = set(gdf_veredas.columns.str.lower())
    m_cod_ver  = "cod_ver"  if "cod_ver"  in cols_existentes else None
    m_cod_mpio = "cod_mpio" if "cod_mpio" in cols_existentes else None
    m_cod_dpto = "cod_dpto" if "cod_dpto" in cols_existentes else None

    if not all([m_cod_ver, m_cod_mpio, m_cod_dpto]):
        print("⚠️ La capa no contiene cod_ver/cod_mpio/cod_dpto. Columnas disponibles:",
              list(gdf_veredas.columns))
        return

    gdf_join = gpd.sjoin(
        gdf_puntos,
        gdf_veredas[[m_cod_ver, m_cod_mpio, m_cod_dpto, "geometry"]],
        how="left",
        predicate="intersects"
    )

    # 7) Renombrar a adm1/adm2/adm3
    rename_map = {
        m_cod_ver:  "adm3",
        m_cod_mpio: "adm2",
        m_cod_dpto: "adm1"
    }
    gdf_join = gdf_join.rename(columns=rename_map)

    # 8) Limpiar IDs y columnas extra
    for col in ["adm1", "adm2", "adm3"]:
        if col in gdf_join.columns:
            gdf_join[col] = gdf_join[col].apply(lambda x: str(int(x)) if pd.notna(x) else x)

    if "index_right" in gdf_join.columns:
        gdf_join = gdf_join.drop(columns=["index_right"])
    if "geometry" in gdf_join.columns:
        gdf_join = gdf_join.drop(columns=["geometry"])

    # 9) Filtrar registros con IDs completos
    total_antes = len(gdf_join)
    df_final = gdf_join.dropna(subset=["adm1", "adm2", "adm3"])
    total_despues = len(df_final)
    print(f"🧹 Registros eliminados por IDs incompletos: {total_antes - total_despues}")

    # 10) Guardar resultado
    output_file = os.path.join(path_output, os.path.basename(ruta_csv))
    df_final.to_csv(output_file, index=False, encoding="utf-8")
    print(f"💾 Archivo final guardado en: {output_file}")
