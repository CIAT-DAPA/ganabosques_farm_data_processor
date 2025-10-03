from pymongo import MongoClient
from bson import ObjectId

# Configuración de conexión
MONGO_URI_DEFAULT = "mongodb://localhost:27017"
MONGO_DB_DEFAULT = "ganabosques"

# Conexión al cliente de MongoDB
client = MongoClient(MONGO_URI_DEFAULT)
db = client[MONGO_DB_DEFAULT]

# Selección de la colección
enterprise_collection = db["enterprise"]

# Ejemplo de nuevo documento (empresa)
new_enterprise = {
    "adm2_id": ObjectId("6847013a7bbf516b66a9e8ce"),  # ajusta según corresponda
    "name": "Carnatural",
    "ext_id": [
        {
            "label": "PRODUCTIONUNIT_ID",
            "ext_code": "99999"
        }
    ],
    "type_enterprise": "enterprise",
    "latitude": 4.1565,
    "longitud": -73.6389,
    "log": {}
}

# Insertar en MongoDB
result = enterprise_collection.insert_one(new_enterprise)

print(f"Empresa guardada con _id: {result.inserted_id}")
