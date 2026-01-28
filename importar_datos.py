import requests
import psycopg
import os
import time
from dotenv import load_dotenv
from math import radians, cos, sin, asin, sqrt

load_dotenv()

# --- CONFIGURACIÓN ESTRATÉGICA ---
# Centro en MAIPÚ (Punto medio estratégico entre Ciudad y San Martín)
LAT_CENTRO = -32.9750
LON_CENTRO = -68.7830
RADIO_METROS = 60000
DISTANCIA_MINIMA = 300 # Metros de separación entre puntos para no saturar

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

def importar_lugares(tipo_osm, valor_osm, tipo_nuestro):
    print(f"🌍 Buscando '{valor_osm}' en un radio de {RADIO_METROS/1000}km...")
    
    # QUERY CIRCULAR (AROUND)
    # Busca nodos (puntos) y ways (edificios) cerca de Maipú
    query = f"""
    [out:json][timeout:90];
    (
      node["{tipo_osm}"="{valor_osm}"](around:{RADIO_METROS},{LAT_CENTRO},{LON_CENTRO});
      way["{tipo_osm}"="{valor_osm}"](around:{RADIO_METROS},{LAT_CENTRO},{LON_CENTRO}); 
    );
    out center;
    """
    
    url = "https://lz4.overpass-api.de/api/interpreter"
    
    datos = []
    exito = False

    # SISTEMA DE REINTENTOS ROBUSTO
    for intento in range(1, 4):
        try:
            headers = {'User-Agent': 'SeguridadVialApp/1.0'}
            response = requests.get(url, params={'data': query}, headers=headers, timeout=100)
            
            if response.status_code == 200:
                datos = response.json().get('elements', [])
                exito = True
                break 
            elif response.status_code == 429:
                print(f"   ⏳ Servidor saturado. Esperando 5s... (Intento {intento}/3)")
                time.sleep(5)
            else:
                print(f"   ⚠️ Error {response.status_code}. Reintentando... (Intento {intento}/3)")
                time.sleep(2)
                
        except Exception as e:
            print(f"   ❌ Fallo de conexión: {str(e)[:50]}... (Intento {intento}/3)")
            time.sleep(2)

    if not exito:
        print(f"❌ No se pudo descargar '{valor_osm}'. Saltando...")
        return

    print(f"📡 Encontrados {len(datos)} candidatos brutos.")

    conn = get_db_connection()
    guardados = 0
    puntos_procesados = [] 
    
    try:
        with conn.cursor() as cur:
            # Primero recuperamos lo que ya hay en la BD para no encimar con categorías anteriores
            cur.execute("SELECT ST_Y(location::geometry) as lat, ST_X(location::geometry) as lon FROM fixed_points")
            puntos_existentes = cur.fetchall()
            # Convertimos tuplas (lat, lon) a lista de dicts para usar tu lógica
            for p in puntos_existentes:
                puntos_procesados.append({'lat': p[0], 'lon': p[1]})

            for item in datos:
                lat = item.get('lat') or item.get('center', {}).get('lat')
                lon = item.get('lon') or item.get('center', {}).get('lon')
                
                if not lat or not lon: continue

                nombre = item.get('tags', {}).get('name', f"{tipo_nuestro.capitalize()} s/n")
                
                # --- FILTRO INTELIGENTE DE 300 METROS ---
                es_duplicado = False
                for p in puntos_procesados:
                    dist = calcular_distancia(lat, lon, p['lat'], p['lon'])
                    if dist < DISTANCIA_MINIMA: # <--- ACÁ ESTÁ EL CAMBIO A 300m
                        es_duplicado = True
                        break
                
                if es_duplicado:
                    continue 

                # Limpieza de dirección
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
            print(f"✅ Guardados {guardados} puntos de tipo {tipo_nuestro}.\n")

    except Exception as e:
        print(f"❌ Error insertando en BD: {e}")
    finally:
        conn.close()

# --- EJECUCIÓN PRINCIPAL ---
if __name__ == "__main__":
    print("🚀 INICIANDO CARGA MASIVA (Radio 30km desde Maipú)...")
    borrar_todo_el_mapa()
    
    # 1. HOSPITALES Y CLÍNICAS 🏥
    importar_lugares("amenity", "hospital", "hospital")
    importar_lugares("amenity", "clinic", "hospital")
    time.sleep(1)
    
    # 2. COMISARÍAS 👮‍♂️
    importar_lugares("amenity", "police", "comisaria")
    time.sleep(1)
    
    # 3. TALLERES MECÁNICOS 🔧 (Agregado nuevo)
    importar_lugares("shop", "car_repair", "taller")
    time.sleep(1)

    # 4. ESTACIONES DE SERVICIO ⛽ (Agregado nuevo - Útil para viajeros)
    importar_lugares("amenity", "fuel", "taller") # Los ponemos como ícono taller o podés crear uno nuevo
    
    print("🎉 ¡MAPA ACTUALIZADO CON ÉXITO!")