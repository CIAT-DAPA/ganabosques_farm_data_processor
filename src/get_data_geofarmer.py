import os
import json
import requests
import geopandas as gpd
from shapely.geometry import shape, Point, Polygon

# === CONFIGURACIÓN ===
CLIENT_ID = "a0009aff-16cb-4be5-abba-4fe2e5b3c70d"
CLIENT_SECRET = "t4rokBXq6QWm5vkR44wMfiIvxxLNjUTa2QSMnc4B"
BASE_URL = "https://api-v3.geocitizen.org"
OUTPUT_DIR = r"D:\OneDrive - CGIAR\Desktop\ganabosques\ganabosques_results\03_etl_farms\GEOFARMER\inputs\api_geofarmer"

# === FUNCIONES ===
def get_token():
    """Obtiene el token de acceso desde GeoCitizen"""
    url = f"{BASE_URL}/oauth/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    r = requests.post(url, data=data)
    r.raise_for_status()
    token = r.json().get("access_token")
    print("✅ Token obtenido correctamente")
    return token

def get_farms(token):
    """Consulta las fincas desde GeoCitizen"""
    url = f"{BASE_URL}/client/farms"
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {token}"
    }
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    farms_data = r.json().get("data", [])
    print(f"📦 Se obtuvieron {len(farms_data)} fincas")
    return farms_data

def save_farm_as_geojson(farm, output_dir):
    """Guarda una finca (y sus parcelas) como GeoJSON"""
    features = []

    # Geometría principal de la finca
    if farm.get("geometry"):
        geom = shape(farm["geometry"])
        features.append({
            "type": "Feature",
            "geometry": farm["geometry"],
            "properties": {
                "type": "farm",
                "farm_id": farm["id"],
                "farm_name": farm["farm_name"],
                "contact_name": farm.get("contact_name"),
                "address": farm.get("address")
            }
        })

    # Parcelas de la finca
    for parcel in farm.get("farm_parcels", []):
        if parcel.get("geometry"):
            features.append({
                "type": "Feature",
                "geometry": parcel["geometry"],
                "properties": {
                    "type": "parcel",
                    "parcel_id": parcel["id"],
                    "parcel_name": parcel.get("parcel_name"),
                    "parcel_size_m2": parcel.get("parcel_size"),
                    "farm_id": farm["id"],
                    "farm_name": farm["farm_name"]
                }
            })

    # Crear GeoDataFrame y guardar
    if not features:
        print(f"⚠️ Finca {farm['farm_name']} sin geometrías, se omite.")
        return

    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    os.makedirs(output_dir, exist_ok=True)
    safe_name = farm["farm_name"].replace(" ", "_").replace("/", "_")
    filepath = os.path.join(output_dir, f"finca_{safe_name}.geojson")
    gdf.to_file(filepath, driver="GeoJSON")
    print(f"💾 Guardado: {filepath}")

def main():
    try:
        token = get_token()
        farms = get_farms(token)
        for farm in farms:
            save_farm_as_geojson(farm, OUTPUT_DIR)
        print("✅ Proceso completado correctamente.")
    except Exception as e:
        print("❌ Error:", e)

if __name__ == "__main__":
    main()
