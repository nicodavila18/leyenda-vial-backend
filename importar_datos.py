import requests
import psycopg
import os
import time
from dotenv import load_dotenv
from math import radians, cos, sin, asin, sqrt

load_dotenv()

# ZONA GRAN MENDOZA (Luján, Maipú, Capital, Las Heras, Godoy Cruz, Guaymallén)
BBOX = "-33.05,-68.95,-32.75,-68.70"

def get_db_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT")
    )

def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371000 
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi/2)**2 + cos(phi1) * cos(phi2) * sin(dlambda/2)**2
    return 2 * R * asin(sqrt(a))

def borrar_todo_el_mapa():
    print("\n🔥 BORRANDO DATOS VIEJOS DE LA BASE DE DATOS...")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE fixed_points;")
            conn.commit()
            print("✨ ¡Tabla vacía! Lista para empezar de cero.\n")
    except Exception as e:
        print(f"❌ Error borrando: {e}")
    finally:
        conn.close()

def importar_lugares(tipo_osm, tipo_nuestro):
    print(f"🌍 Buscando '{tipo_osm}' en Mendoza (OSM)...")
    
    # 1. AUMENTAMOS EL TIMEOUT A 60 SEGUNDOS
    query = f"""
    [out:json][timeout:60];
    (
      node["amenity"="{tipo_osm}"]({BBOX});
      way["amenity"="{tipo_osm}"]({BBOX}); 
    );
    out center;
    """
    # Nota: Agregué 'way' y 'out center' para encontrar edificios grandes que no son solo puntos (nodos).
    
    # URL alternativa (lz4) que suele ser más rápida
    url = "https://lz4.overpass-api.de/api/interpreter"
    
    datos = []
    exito = False

    # 2. SISTEMA DE REINTENTOS (3 intentos)
    for intento in range(1, 4):
        try:
            headers = {'User-Agent': 'SeguridadVialApp/1.0'}
            response = requests.get(url, params={'data': query}, headers=headers, timeout=65)
            
            if response.status_code == 200:
                datos = response.json().get('elements', [])
                exito = True
                break # ¡Éxito! Salimos del bucle
            elif response.status_code == 429:
                print(f"   ⏳ Servidor ocupado (429). Esperando 5s... (Intento {intento}/3)")
                time.sleep(5)
            else:
                print(f"   ⚠️ Error {response.status_code}. Reintentando... (Intento {intento}/3)")
                time.sleep(2)
                
        except Exception as e:
            print(f"   ❌ Fallo de conexión: {str(e)[:50]}... (Intento {intento}/3)")
            time.sleep(2)

    if not exito:
        print(f"❌ No se pudo descargar '{tipo_osm}' después de 3 intentos.")
        return

    print(f"📡 Encontrados {len(datos)} candidatos.")

    conn = get_db_connection()
    guardados = 0
    puntos_procesados = [] 
    
    try:
        with conn.cursor() as cur:
            for item in datos:
                # Si es un edificio (way), usamos su 'center', si es nodo usamos lat/lon
                lat = item.get('lat') or item.get('center', {}).get('lat')
                lon = item.get('lon') or item.get('center', {}).get('lon')
                
                if not lat or not lon: continue

                nombre = item.get('tags', {}).get('name', 'Sin Nombre')
                
                # FILTRO ANTI-CLONES (100 metros para asegurar)
                es_duplicado = False
                for p in puntos_procesados:
                    dist = calcular_distancia(lat, lon, p['lat'], p['lon'])
                    if dist < 100: 
                        es_duplicado = True
                        break
                
                if es_duplicado:
                    continue 

                calle = item.get('tags', {}).get('addr:street', '')
                altura = item.get('tags', {}).get('addr:housenumber', '')
                direccion = f"{calle} {altura}".strip() or "Ubicación s/d"
                
                cur.execute("""
                    INSERT INTO fixed_points (name, type, location, address, phone, hours)
                    VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, 'Consultar', '24hs')
                """, (nombre, tipo_nuestro, lon, lat, direccion))
                
                puntos_procesados.append({'lat': lat, 'lon': lon})
                guardados += 1
            
            conn.commit()
            print(f"✅ Guardados {guardados} {tipo_nuestro}.\n")

    except Exception as e:
        print(f"❌ Error en BD: {e}")
    finally:
        conn.close()

# --- EJECUCIÓN PRINCIPAL ---
if __name__ == "__main__":
    borrar_todo_el_mapa()
    
    importar_lugares("hospital", "hospital")
    time.sleep(1)
    
    importar_lugares("police", "comisaria")
    time.sleep(1)
    
    importar_lugares("clinic", "hospital") 
    
    print("🎉 ¡PROCESO TERMINADO CORRECTAMENTE!")