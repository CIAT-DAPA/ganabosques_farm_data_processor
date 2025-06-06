import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import os
import unicodedata
import requests
import zipfile
import io

def quality_control_coordinates(input_path, path_output, workspace, url_geoserver, store, user, password):
    print("🔍 Iniciando control de calidad de coordenadas...")

    # Buscar archivo CSV en input_path
    csv_files = [f for f in os.listdir(input_path) if f.endswith(".csv")]
    if not csv_files:
        print("❌ No se encontró ningún archivo CSV en:", input_path)
        return
    ruta_csv = os.path.join(input_path, csv_files[0])
    print("📄 CSV cargado:", ruta_csv)

    # Construir URL del shapefile
    url_shp = f"{url_geoserver}{workspace}/wfs?service=WFS&version=1.0.0&request=GetFeature&typeName={workspace}:{store}&outputFormat=shape-zip"
    print("🌐 Solicitando shapefile desde:", url_shp)

    response = requests.get(url_shp, auth=(user, password))

    if response.ok:
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            z.extractall("divipola_shapefile")
        print("✅ Shapefile descargado y extraído con éxito.")
    else:
        print("❌ Error al descargar shapefile:", response.status_code)
        return

    # Cargar CSV
    df = pd.read_csv(ruta_csv)
    # Eliminar coordenadas faltantes
    total_original = df.shape[0]
    df = df.dropna(subset=['LONGITUD', 'LATITUD'])
    total_filtrado = df.shape[0]
    print(f"🧹 Registros eliminados por coordenadas incompletas: {total_original - total_filtrado}")

    # Crear geometría
    geometry = [Point(xy) for xy in zip(df['LONGITUD'], df['LATITUD'])]
    gdf_puntos = gpd.GeoDataFrame(df.copy(), geometry=geometry, crs="EPSG:4326")

    # Cargar shapefile local
    shp_path = [f for f in os.listdir("divipola_shapefile") if f.endswith(".shp")]
    if not shp_path:
        print("❌ No se encontró shapefile dentro del zip.")
        return
    ruta_shp = os.path.join("divipola_shapefile", shp_path[0])
    gdf_veredas = gpd.read_file(ruta_shp)
    gdf_veredas = gdf_veredas.to_crs("EPSG:4326")
    print("📍 Shapefile cargado y CRS transformado a EPSG:4326.")

    # Join espacial
    gdf_join = gpd.sjoin(
        gdf_puntos,
        gdf_veredas[['cod_ver', 'cod_mpio', 'cod_dpto', 'geometry']],
        how='left',
        predicate='intersects'
    )

    gdf_join = gdf_join.rename(columns={
        'cod_ver': 'adm3',
        'cod_mpio': 'adm2',
        'cod_dpto': 'adm1'
    })

    # Eliminar ceros a la izquierda (manteniendo NaN)
    for col in ['adm3', 'adm2', 'adm1']:
        gdf_join[col] = gdf_join[col].apply(lambda x: str(int(x)) if pd.notna(x) else x)

    gdf_join = gdf_join.drop(columns=['geometry', 'index_right'])

    # Eliminar registros sin IDs
    total_antes = gdf_join.shape[0]
    df_final = gdf_join.dropna(subset=['adm3', 'adm2', 'adm1'])
    total_despues = df_final.shape[0]
    print(f"🧹 Registros eliminados por IDs incompletos: {total_antes - total_despues}")

    # Crear carpeta de salida
    os.makedirs(path_output, exist_ok=True)

    # Guardar archivo
    output_file = os.path.join(path_output, os.path.basename(ruta_csv))
    df_final.to_csv(output_file, index=False, encoding='utf-8')
    print(f"💾 Archivo final guardado en: {output_file}")



