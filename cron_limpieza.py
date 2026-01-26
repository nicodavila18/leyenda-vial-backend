import time
import psycopg
import os
from dotenv import load_dotenv

# Cargamos las claves del archivo .env (DB_HOST, DB_PASSWORD, etc.)
load_dotenv()

# Configuración: Revisa cada 5 minutos (300 segundos)
INTERVALO = 300 

def conectar_bd():
    try:
        return psycopg.connect(
            host=os.getenv("DB_HOST"),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            port=os.getenv("DB_PORT")
        )
    except Exception as e:
        print(f"❌ Error conectando a BD: {e}")
        return None

def archivar_vencidos():
    conn = conectar_bd()
    if not conn: return

    try:
        with conn.cursor() as cur:
            print("📦 Iniciando proceso de archivado...")

            # 1. COPIAR A HISTÓRICO 📜
            # Seleccionamos reportes que:
            # A) Tienen más de 2 horas de antigüedad.
            # B) O fueron desactivados por votos negativos (is_active = FALSE).
            
            cur.execute("""
                INSERT INTO reports_history (id, user_id, type_code, description, location, created_at, score, final_status)
                SELECT 
                    id, user_id, type_code, description, location, created_at, score,
                    CASE 
                        WHEN is_active = FALSE THEN 'borrado_votos' 
                        ELSE 'vencido_tiempo' 
                    END
                FROM reports
                WHERE created_at < NOW() - INTERVAL '2 hours' 
                   OR is_active = FALSE
                ON CONFLICT (id) DO NOTHING; -- Por seguridad, para no duplicar si corre dos veces
            """)
            copiados = cur.rowcount

            # 2. BORRAR DE LA TABLA PRINCIPAL (Limpieza) 🧹
            # Solo borramos lo que cumple las mismas condiciones
            if copiados > 0:
                cur.execute("""
                    DELETE FROM reports
                    WHERE created_at < NOW() - INTERVAL '2 hours' 
                       OR is_active = FALSE
                """)
                conn.commit()
                print(f"✅ Se archivaron y limpiaron {copiados} reportes.")
            else:
                print("💤 Nada para archivar por ahora.")

    except Exception as e:
        print(f"🔥 Error en archivado: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    print("🤖 Servicio de Archivador de Datos: ACTIVO")
    print(f"🕒 Corriendo cada {INTERVALO} segundos...")
    print("------------------------------------------------")
    
    while True:
        archivar_vencidos()
        time.sleep(INTERVALO)