import os
from dotenv import load_dotenv
from ganabosques_orm.enums.species import Species
from ganabosques_orm.enums.ugg import UGG
from ganabosques_orm.enums.farmsource import FarmSource

load_dotenv()

config = {}

config['DEBUG'] = os.getenv('DEBUG', 'true').lower() == 'true'
config['URL_GEO'] = os.getenv("URL_GEO")
config['WORKSPACE'] = os.getenv('WORKSPACE')
config['GEO_USER'] = os.getenv("GEO_USER")
config['GEO_PWD'] = os.getenv("GEO_PWD")
config['GEO_WORKSPACE'] = os.getenv("GEO_WORKSPACE")
config['GEO_STORE'] = os.getenv("GEO_STORE")
config['MONGO_DB_NAME'] = os.getenv("MONGO_DB_NAME")
config['MONGO_URI'] = os.getenv("MONGO_URI")
config['DATA'] = os.getenv("DATA")

# G1 = TERNEROS_MENORES_1_ANIO
# G2 = HEMBRAS_MACHOS_1_2_ANIOS
# G3 = HEMBRAS_MENORES_2_3_ANIOS
# G4 = MACHOS_2_3_ANIOS
# G5 = HEMBRAS_MAYORES_3_ANIOS
# G6 = MACHOS_MAYORES_3_ANIOS
config["UGG_GRUPOS"]= {
    'G1': 0.5,
    'G2': 0.7,
    'G3': 0.8,
    'G4': 0.75,
    'G5' : 1.0,
    'G6' :1.25,
}

config[FarmSource.SAGARI.value] = {
    UGG.TERNEROS_MENORES_1_ANIO: ["HEMBRAS_MENORES_A_3_MESES", "HEMBRAS_MENORES_DE_3_A_8_MESES", "DE_8_A_12_MESES",
                                  "TERNEROS_MENORES_A_1_AÑO", "MACHOS_MENORES_A_3_MESES", "MACHOS_3_HASTA_8_MESES", 
                                  "MACHOS_8_HASTA_12_MESES"],
    UGG.HEMBRAS_MACHOS_1_2_ANIOS: ["HEMBRAS_1___2_AÑOS", "MACHOS_1___2_AÑOS"],
    UGG.HEMBRAS_MENORES_2_3_ANIOS: ["HEMBRAS_2___3_AÑOS"],
    UGG.MACHOS_2_3_ANIOS: ["MACHOS_2___3_AÑOS"],
    UGG.HEMBRAS_MAYORES_3_ANIOS: ["HEMBRAS_3___5_AÑOS", "HEMBRAS_MAYORES_A_5_AÑOS"],
    UGG.MACHOS_MAYORES_3_ANIOS: ["MACHOS_MAYORES_A_3_AÑOS"],
}

if __name__ == "__main__":
    print(config)