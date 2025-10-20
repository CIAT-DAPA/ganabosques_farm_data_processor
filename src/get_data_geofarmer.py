import os
import requests
import geopandas as gpd
from shapely.geometry import shape

# === CONFIGURACIÓN BASE ===
BASE_URL = "https://api-v3.geocitizen.org"
OUTPUT_DIR = r"D:\OneDrive - CGIAR\Desktop\ganabosques\ganabosques_results\03_etl_farms\GEOFARMER\inputs\api_geofarmer"

# Diccionario con todas las empresas
CLIENTS = {
    "Colacteos": {
        "CLIENT_ID": "a0009aff-16cb-4be5-abba-4fe2e5b3c70d",
        "CLIENT_SECRET": "t4rokBXq6QWm5vkR44wMfiIvxxLNjUTa2QSMnc4B"
    },
    "Lacteos_del_Hogar": {
        "CLIENT_ID": "a02956a8-40a8-4c83-a72f-574cadc84042",
        "CLIENT_SECRET": "K4MsItX2zHlYOxQNt95bWKrF542ymKy5mJMCH7tL"
    },
    "Carnatural": {
        "CLIENT_ID": "a029578a-0d07-46c3-a533-1eb48f747192",
        "CLIENT_SECRET": "IJEAWScxi8F03NkFqD7ry3fAG2povPXtBVPdmamg"
    },
    "Fedegwa": {
        "CLIENT_ID": "a02957ac-74e6-484a-a3ca-b5b4a1f8deec",
        "CLIENT_SECRET": "QLWbXfnHUxXcyTT1KwDDFFAMAmjrHzo6qr2I8iN7"
    }
}

# === FUNCIONES ===

def get_token(client_id, client_secret):
    """Obtiene el token OAuth2 de cada cliente"""
    url = f"{BASE_URL}/oauth/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }
    r = requests.post(url, data=data)
    r.raise_for_status()
    token = r.json().get("access_token")
    return token

def get_farms(token):
    """Obtiene las fincas asociadas al cliente"""
    url = f"{BASE_URL}/client/farms"
    headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json().get("data", [])

def save_farm_as_geojson(farm, company_dir):
    """Convierte la finca (y sus parcelas) a GeoJSON y la guarda"""
    features = []

    # Geometría principal
    if farm.get("geometry"):
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

    # Parcelas
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

    if not features:
        print(f"⚠️ {farm['farm_name']} no tiene geometrías, se omite.")
        return

    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")

    os.makedirs(company_dir, exist_ok=True)
    safe_name = farm["farm_name"].replace(" ", "_").replace("/", "_")
    filepath = os.path.join(company_dir, f"finca_{safe_name}.geojson")
    gdf.to_file(filepath, driver="GeoJSON")
    print(f"💾 Guardado: {filepath}")

def process_company(name, creds):
    """Procesa todas las fincas de una empresa"""
    print(f"\n🔹 Procesando empresa: {name}")
    try:
        token = get_token(creds["CLIENT_ID"], creds["CLIENT_SECRET"])
        farms = get_farms(token)
        print(f"📦 {len(farms)} fincas encontradas para {name}")
        company_dir = os.path.join(OUTPUT_DIR, name)
        for farm in farms:
            save_farm_as_geojson(farm, company_dir)
        print(f"✅ Empresa {name} procesada correctamente.")
    except Exception as e:
        print(f"❌ Error procesando {name}: {e}")

def main():
    for company, creds in CLIENTS.items():
        process_company(company, creds)
    print("\n🎯 Descarga completada para todas las empresas.")

# === EJECUCIÓN ===
if __name__ == "__main__":
    main()
