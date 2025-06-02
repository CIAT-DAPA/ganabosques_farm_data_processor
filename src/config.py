import os
from dotenv import load_dotenv

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

if __name__ == "__main__":
    print(config)