import os
from dotenv import load_dotenv

load_dotenv()

config = {}

config['DEBUG'] = os.getenv('DEBUG', 'true').lower() == 'true'
config['WORKSPACE'] = os.getenv('WORKSPACE')
config['DATA'] = os.getenv('DATA')
config['MONGO_URI'] = 'mongodb://localhost:27017'
config['MONGO_DB_NAME'] = 'ganabosques'


if __name__ == "__main__":
    print(config)