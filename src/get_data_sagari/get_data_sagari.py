import os
import pandas as pd
from tqdm import tqdm
from itertools import product
from ganabosques_orm.enums.species import Species
from ganabosques_orm.enums.ugg import UGG
from ganabosques_orm.enums.farmsource import FarmSource
from config import config
from ganabosques_orm.enums.source import Source

def get_data_sagari(path_input, output_file):
    print("Listando archivos en la carpeta de entrada...")
    archivos = [os.path.join(path_input, f) for f in os.listdir(path_input) if f.endswith(".xlsx")]

    if not archivos:
        print("No se encontraron archivos Excel en la ruta especificada.")
        return

    print(f"Se encontraron {len(archivos)} archivos:")
    for archivo in archivos:
        print(f" - {archivo}")

    lista_data = []
    print("Procesando archivos...")
    for archivo in tqdm(archivos):
        df = pd.read_excel(archivo, engine="openpyxl")
        asignaciones = generar_agg_sagari(df)
        columnas_interes = ["CODIGO_SIT", "LATITUD", "LONGITUD"] + sum(asignaciones.values(), [])

        print(f"\nLeyendo {archivo} con {df.shape[0]} filas y {df.shape[1]} columnas")
        
        df_filtrado = df[columnas_interes].copy()
        for idx, (clave, columnas_a_sumar) in enumerate(asignaciones.items()):
            nombre_columna = list(asignaciones.keys())[idx]
            df_filtrado[nombre_columna] = df_filtrado[columnas_a_sumar].sum(axis=1)
        
        columnas_a_eliminar = sum(asignaciones.values(), [])
        df_filtrado.drop(columns=columnas_a_eliminar, inplace=True)
        lista_data.append(df_filtrado)

        print("\nConcatenando todos los archivos...")
        df_final = pd.concat(lista_data, ignore_index=True)
        
        renombrar_codigo_sit = True
        if renombrar_codigo_sit:
            df_final.rename(columns={"CODIGO_SIT": Source.SIT_CODE.value}, inplace=True)

        print(f"Guardando archivo combinado en: {output_file}")
        os.makedirs(output_file, exist_ok=True)
        source = FarmSource.SAGARI.value
        df_final.to_csv(os.path.join(output_file, f"{source}_completo.csv"), index=False)
        print("✅ Proceso completado.")


def generar_agg_sagari(df):
    agg_dict = {}
    for grupo in UGG:
        for especie in Species:
            key = f"{grupo.name}_{especie.name}"  
            columnas = []
            substrings = [f"AFTOSA_{especie.name}_{suf}" for suf in config[FarmSource.SAGARI.value][grupo]]
            columnas = [col for col in df if any(substr in col for substr in substrings)]
            agg_dict[key] = columnas  
    return agg_dict