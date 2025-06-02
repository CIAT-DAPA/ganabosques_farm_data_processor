import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import os
import unicodedata
import requests
import zipfile
import io

def limpiar_texto(texto):
    if pd.isna(texto):
        return None
    texto = texto.lower()
    texto = texto.replace('~n', 'n')
    texto = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode('utf-8')
    texto = texto.strip()
    return texto

def corregir_encoding(texto):
    if isinstance(texto, str):
        try:
            return texto.encode('latin1').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            return texto
    return texto

def quality_control_coordinates(input_path, path_output, workspace, url_geoserver, store,user,password):
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

    user = user
    password = password
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

    # Renombrar columna 'A~NO' a 'ANIO' si existe
    if 'A~NO' in df.columns:
        df = df.rename(columns={'A~NO': 'ANIO'})
        print("🔄 Columna 'A~NO' renombrada a 'ANIO'.")

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
        'cod_ver': 'ID_VEREDA',
        'cod_mpio': 'ID_MUNICIPIO',
        'cod_dpto': 'ID_DEPARTAMENTO'
    })

    gdf_join = gdf_join.drop(columns=['geometry', 'index_right'])

    # Eliminar registros sin IDs
    total_antes = gdf_join.shape[0]
    df_final = gdf_join.dropna(subset=['ID_VEREDA', 'ID_MUNICIPIO', 'ID_DEPARTAMENTO'])
    total_despues = df_final.shape[0]
    print(f"🧹 Registros eliminados por IDs incompletos: {total_antes - total_despues}")

    # Crear carpeta de salida
    #carpeta_salida = os.path.join(path_output, "02_tmp_quality_control_coordinates")
    os.makedirs(path_output, exist_ok=True)

    # Guardar archivo
    output_file = os.path.join(path_output, os.path.basename(ruta_csv))
    df_final.to_csv(output_file, index=False, encoding='utf-8')
    print(f"💾 Archivo final guardado en: {output_file}")

# Ejemplo de uso
""" quality_control_coordinates(
        input_path=r"D:\OneDrive - CGIAR\Desktop\ganabosques\farms\tmp\01_tmp_get_data_sagari",
        path_output=r"D:\OneDrive - CGIAR\Desktop\ganabosques\farms\tmp",
        workspace="administrative",
        url_geoserver="http://localhost:8600/geoserver/",
        store="divipola",
        user = "admin" ,
        password= "geoserver"
) """
