import jwt
import os
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from pydantic import BaseModel, EmailStr
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from passlib.context import CryptContext # <--- NUEVO: Para la seguridad
from datetime import date, datetime, timedelta
from typing import Optional, List  # <--- Agregá esto
import uuid                        # <--- Y esto también (para generar IDs únicos)
import mercadopago                # <--- Y esto para pagos (futuro)

# 1. Cargar variables de entorno
load_dotenv()

mp_access_token = os.getenv("MP_ACCESS_TOKEN")
sdk = mercadopago.SDK(mp_access_token)

app = FastAPI(
    title="API Seguridad Vial Argentina",
    description="Backend para gestión de reportes ciudadanos y controles",
    version="1.1.0" # Subimos versión
)

# --- CONFIGURACIÓN DE SEGURIDAD (NUEVO) ---
# Esto se encarga de encriptar y verificar contraseñas
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- CONFIGURACIÓN JWT (SEGURIDAD) ---
SECRET_KEY = os.getenv("SECRET_KEY", "poné_una_frase_muy_larga_y_secreta_acá_12345") 
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30 # La sesión dura 30 días

def crear_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def encriptar_password(password):
    return pwd_context.hash(password)

def verificar_password(password_plana, password_encriptada):
    return pwd_context.verify(password_plana, password_encriptada)

# 2. Función para conectarse a la Base de Datos
def get_db_connection():
    try:
        conn = psycopg.connect(
            host=os.getenv("DB_HOST"),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            port=os.getenv("DB_PORT"),
            row_factory=dict_row 
        )
        return conn
    except Exception as e:
        print(f"Error conectando a la BD: {e}")
        raise HTTPException(status_code=500, detail="Error de conexión a base de datos")

# Esta función actuará de "Portero" en los endpoints que quieras proteger
def verificar_token(authorization: str = Header(None)):
    if authorization is None:
        raise HTTPException(status_code=401, detail="Falta el token de autenticación")
    
    try:
        # El formato suele ser "Bearer eyJhbGci..."
        token = authorization.replace("Bearer ", "")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Token inválido")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="El token ha expirado. Logueate de nuevo.")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token inválido")

# --- MODELOS DE DATOS ---

# Modelo para cuando alguien manda un reporte
class ReporteNuevo(BaseModel):
    type_code: str
    description: str
    latitud: float
    longitud: float
    user_id: str  # UUID del usuario que reporta

# NUEVO: Modelo para registrarse
class UsuarioRegistro(BaseModel):
    username: str
    email: EmailStr
    password: str
    provincia: str
    localidad: str

# NUEVO: Modelo para loguearse
class UsuarioLogin(BaseModel):
    email: EmailStr
    password: str

# Modelo para recibir el pedido
class CanjeRequest(BaseModel):
    user_id: str
    costo_puntos: int
    cantidad_reportes: int

class CanjePremiumRequest(BaseModel):
    user_id: str
    costo_puntos: int # Ej: 1000

# Modelo para el voto
class VotoReporte(BaseModel):
    user_id: str
    reporte_id: str
    tipo_voto: str # "confirmar" (Sigue ahí) o "borrar" (Ya no está)

class VehiculoRequest(BaseModel):
    user_id: str
    vehiculo: str # 'auto', 'moto', 'bici'
    patente: str = ""
    modelo: str = ""

# Modelo para recibir el cambio de nombre
class PerfilRequest(BaseModel):
    user_id: str
    username: str

# Modelo para recibir la foto
class AvatarRequest(BaseModel):
    user_id: str
    avatar_base64: str # Aquí viene la foto convertida en texto

# --- NUEVA CLASE PARA PUNTOS FIJOS ---
class PuntoFijo(BaseModel):
    id: Optional[str] = None
    nombre: str
    tipo: str  # hospital, taller, comisaria, legal, negocio
    latitud: float
    longitud: float
    direccion: str
    telefono: str
    horario: str

# Modelo para recibir el pedido
class SolicitudPago(BaseModel):
    user_id: str
    titulo: str
    precio: float

# Modelo para pedir la suscripción
class SolicitudSuscripcion(BaseModel):
    user_id: str
    email: str # Necesitamos el email para identificar al pagador en MP

# Modelo para cancelar la suscripción
class CancelacionRequest(BaseModel):
    user_id: str


# --- RUTAS (ENDPOINTS) ---

@app.get("/")
def read_root():
    return {"status": "online", "mensaje": "API de Seguridad Vial funcionando 🇦🇷"}

# ==========================================
#   RUTAS DE USUARIOS (REGISTRO Y LOGIN)
# ==========================================

@app.post("/registro")
def registrar_usuario(usuario: UsuarioRegistro):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Verificamos si ya existe
            cur.execute("SELECT id FROM users WHERE email = %s", (usuario.email,))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="El email ya está registrado")

            # 2. Encriptamos
            clave_hash = encriptar_password(usuario.password)

            # 3. Guardamos CON PROVINCIA Y LOCALIDAD 🌍
            cur.execute("""
                INSERT INTO users (username, email, password_hash, provincia, localidad)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, username
            """, (usuario.username, usuario.email, clave_hash, usuario.provincia, usuario.localidad))
            
            nuevo_usuario = cur.fetchone()
            conn.commit()
            return {"mensaje": "Usuario creado con éxito", "usuario": nuevo_usuario}
    except Exception as e:
        conn.rollback()
        print(f"Error registro: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/login")
def login(usuario: UsuarioLogin):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Buscamos al usuario por email
            cur.execute("SELECT * FROM users WHERE email = %s", (usuario.email,))
            user_db = cur.fetchone()

            # 2. Verificamos contraseña
            if not user_db or not verificar_password(usuario.password, user_db['password_hash']):
                raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

            # 3. GENERAMOS EL TOKEN (LA PULSERA VIP) 🎟️
            access_token = crear_access_token(data={"sub": str(user_db['id'])})

            # 4. Login exitoso: Devolvemos Token + Datos del usuario
            return {
                "mensaje": "Login exitoso",
                "access_token": access_token, # <--- ESTO ES LO IMPORTANTE
                "token_type": "bearer",
                "user_id": str(user_db['id']),
                "username": user_db['username'],
                "reputation": user_db['reputation'],
                "is_premium": user_db['is_premium']
            }
    finally:
        conn.close()

# ==========================================
#   RUTAS DE REPORTES (MAPA)
# ==========================================

@app.get("/reportes")
def obtener_reportes():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    r.id, 
                    r.description, 
                    r.type_code as tipo, 
                    ST_X(r.location::geometry) as longitud,
                    ST_Y(r.location::geometry) as latitud,
                    r.created_at,
                    r.user_id,
                    u.username as autor,
                    u.lifetime_xp  -- <--- AGREGAMOS ESTO: Necesitamos la XP para saber su nivel
                FROM reports r
                JOIN users u ON r.user_id = u.id 
                WHERE r.is_active = TRUE
                  AND r.created_at > NOW() - INTERVAL '2 hours' -- <--- ASEGURATE QUE DIGA '2 hours'
            """)
            resultados = cur.fetchall()
            return resultados
    finally:
        conn.close()

@app.post("/reportes")
def crear_reporte(reporte: ReporteNuevo):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 0. OBTENER DATOS DEL USUARIO
            cur.execute("SELECT id, is_premium, daily_reports_count, last_report_date FROM users WHERE id = %s", (reporte.user_id,))
            user = cur.fetchone()
            
            if not user:
                return {"status": "error", "mensaje": "Usuario no encontrado"}

            is_premium = user['is_premium']
            count = user['daily_reports_count'] if user['daily_reports_count'] is not None else 0
            last_date = user['last_report_date']
            today = date.today()

            # 1. RESETEO DIARIO
            if last_date is None or last_date != today:
                count = 0
                cur.execute("UPDATE users SET daily_reports_count = 0, last_report_date = %s WHERE id = %s", (today, reporte.user_id))
                conn.commit()

            # 2. BUSCAMOS DUPLICADOS INTELIGENTES 🧠
            # Regla:
            # - Radio: 50 metros (Más precisión)
            # - Tiempo: Solo confirmamos si el reporte "sigue vivo".
            #   Si es viejo, lo ignoramos y dejamos crear uno nuevo encima.
            
            # Definimos cuánto vive un reporte según el tipo
            intervalo = "2 hours" # Para Policía y Accidente (Cosas temporales)
            if reporte.type_code == 'obra': 
                intervalo = "24 hours" # Las obras duran mucho más
            
            cur.execute(f"""
                SELECT id FROM reports 
                WHERE type_code = %s 
                  AND is_active = TRUE
                  AND created_at > NOW() - INTERVAL '{intervalo}' -- <--- MAGIA: Solo buscamos recientes
                  AND ST_DWithin(location::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 50)
                LIMIT 1
            """, (reporte.type_code, reporte.longitud, reporte.latitud))
            
            reporte_existente = cur.fetchone()

            # 3. LÓGICA DE NEGOCIO
            if reporte_existente:
                # --- CASO A: CONFIRMAR EXISTENTE (Siempre gratis) ---
                reporte_id = reporte_existente['id']
                
                # Renovamos el reporte
                cur.execute("UPDATE reports SET created_at = NOW() WHERE id = %s", (reporte_id,))
                
                # Premio por confirmar (Vamos a subirlo un poquito para que motive)
                # ANTES: +2 Pts, +1 XP
                # AHORA: +3 Pts, +2 XP (¡Más justo!)
                cur.execute("""
                    UPDATE users SET 
                        reputation = reputation + 3,   -- <--- CAMBIADO A 3
                        lifetime_xp = lifetime_xp + 2, -- <--- CAMBIADO A 2
                        total_helps = total_helps + 1 
                    WHERE id = %s
                """, (reporte.user_id,))
                
                conn.commit()
                # Actualizamos el mensaje también
                return {"mensaje": "¡Confirmado! (+3 Pts / +2 XP) 🛡️", "status": "confirmed"}

            else:
                # CASO NUEVO
                LIMITE_GRATIS = 3
                if not is_premium and count >= LIMITE_GRATIS:
                     return {"mensaje": "⛔ ¡Tanque Vacío! Hacete Premium.", "status": "error_limit"}

                # INSERTAR NUEVO (Usamos type_code aquí también)
                cur.execute("""
                    INSERT INTO reports (user_id, type_code, description, location, created_at, is_active)
                    VALUES (%s, %s, 'Reporte desde App', ST_SetSRID(ST_MakePoint(%s, %s), 4326), NOW(), TRUE)
                """, (reporte.user_id, reporte.type_code, reporte.longitud, reporte.latitud)) # <--- ¡AQUÍ ESTABA EL ERROR! (Decía reporte.tipo)
                
                cur.execute("""
                    UPDATE users 
                    SET reputation = reputation + 10, lifetime_xp = lifetime_xp + 5, 
                        total_reports = total_reports + 1, daily_reports_count = daily_reports_count + 1 
                    WHERE id = %s
                """, (reporte.user_id,))
                
                conn.commit()
                return {"mensaje": "¡Creado! (+10 Pts / +5 XP) 🚀", "status": "created"}

    except Exception as e:
        conn.rollback()
        print(f"Error creando reporte: {e}")
        return {"status": "error", "mensaje": str(e)}
    finally:
        conn.close()

@app.get("/usuarios/{user_id}")
def obtener_usuario(user_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, email, reputation, 
                       premium_expires_at, subscription_status, -- <--- TRAEMOS ESTOS NUEVOS
                       daily_reports_count, last_report_date,
                       lifetime_xp, total_reports, total_helps,
                       vehicle_type, patente, modelo, avatar_data
                FROM users WHERE id = %s
            """, (user_id,))
            user = cur.fetchone()
            
            if not user:
                raise HTTPException(status_code=404, detail="Usuario no encontrado")

            # --- LÓGICA MAESTRA DE PREMIUM --- 🧠
            es_premium = False
            
            if user['premium_expires_at']:
                # Si la fecha de vencimiento es MAYOR a ahora, es Premium
                if user['premium_expires_at'] > datetime.now():
                    es_premium = True
                else:
                    # Se venció. Ya no es Premium.
                    es_premium = False
            
            # (El resto de tu lógica de reportes sigue igual...)
            today = date.today()
            reports_used = user['daily_reports_count']
            if user['last_report_date'] != today:
                reports_used = 0

            return {
                "username": user['username'],
                "email": user['email'],
                "reputation": user['reputation'],      # DINERO (Billetera)
                "lifetime_xp": user['lifetime_xp'],    # NIVEL (Experiencia)
                "total_reports": user['total_reports'], # ESTADÍSTICA
                "total_helps": user['total_helps'],     # ESTADÍSTICA
                "is_premium": es_premium,
                "subscription_status": user['subscription_status'], # Para saber si mostrar botón "Cancelar"
                "premium_expires_at": user['premium_expires_at'],   # Para mostrar "Vence el..."
                "reports_used": reports_used,
                "vehicle_type": user['vehicle_type'],   # AGREGAMOS ESTO
                "patente": user['patente'] or "",
                "modelo": user['modelo'] or "",
                "avatar_data": user['avatar_data'] or "",
                "reports_limit": 3,
            }
    finally:
        conn.close()

@app.put("/usuarios/vehiculo")
def cambiar_vehiculo(req: VehiculoRequest):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Actualizamos TODO junto
            cur.execute("""
                UPDATE users 
                SET vehicle_type = %s, 
                    patente = %s, 
                    modelo = %s 
                WHERE id = %s
            """, (req.vehiculo, req.patente, req.modelo, req.user_id))
            conn.commit()
            return {"status": "success", "mensaje": "Datos del vehículo actualizados 🚗"}
    finally:
        conn.close()

@app.post("/canjear-puntos")
def canjear_puntos(canje: CanjeRequest, authorization: str = Header(None)): # <--- 1. PIDE LA CREDENCIAL
    
    # 2. VERIFICAR QUE LA CREDENCIAL SEA VÁLIDA 👮‍♂️
    usuario_id_del_token = verificar_token(authorization)
    
    # 3. VERIFICAR QUE NO ESTÉ USANDO LA CREDENCIAL DE OTRO 🔐
    if str(usuario_id_del_token) != str(canje.user_id):
        raise HTTPException(status_code=403, detail="No podés usar los puntos de otro usuario")

    # --- A PARTIR DE ACÁ ES IGUAL QUE ANTES ---
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT reputation, daily_reports_count FROM users WHERE id = %s", (canje.user_id,))
            user = cur.fetchone()
            
            if not user:
                raise HTTPException(status_code=404, detail="Usuario no encontrado")
                
            puntos_actuales = user['reputation']
            usados_hoy = user['daily_reports_count']

            if puntos_actuales < canje.costo_puntos:
                return {"status": "error", "mensaje": "❌ Puntos insuficientes"}

            nuevo_contador = usados_hoy - canje.cantidad_reportes 
            
            cur.execute("""
                UPDATE users 
                SET reputation = reputation - %s,
                    daily_reports_count = %s
                WHERE id = %s
            """, (canje.costo_puntos, nuevo_contador, canje.user_id))
            
            conn.commit()
            
            return {
                "status": "success", 
                "mensaje": "¡Canje Exitoso! ⛽ Recargaste el tanque.",
                "nuevo_saldo": puntos_actuales - canje.costo_puntos
            }
    finally:
        conn.close()

@app.post("/canjear-premium")
def canjear_premium(canje: CanjePremiumRequest, authorization: str = Header(None)):
    # 1. SEGURIDAD (Esto lo hiciste perfecto)
    usuario_id_del_token = verificar_token(authorization)
    
    if str(usuario_id_del_token) != str(canje.user_id):
        raise HTTPException(status_code=403, detail="No podés usar los puntos de otro usuario")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 2. CHEQUEOS (Perfectos)
            cur.execute("SELECT reputation, is_premium FROM users WHERE id = %s", (canje.user_id,))
            user = cur.fetchone()
            
            if not user:
                raise HTTPException(status_code=404, detail="Usuario no encontrado")
            
            if user['is_premium']:
                return {"status": "error", "mensaje": "¡Ya sos Premium! 💎"}

            if user['reputation'] < canje.costo_puntos:
                return {"status": "error", "mensaje": "❌ Puntos insuficientes."}

            # 3. EL CANJE (CORREGIDO CON FECHA) 📅
            # Agregamos 'premium_expires_at' para que dure solo 7 días
            cur.execute("""
                UPDATE users 
                SET reputation = reputation - %s,
                    is_premium = TRUE,
                    premium_expires_at = NOW() + INTERVAL '7 days',
                    subscription_status = 'active'
                WHERE id = %s
            """, (canje.costo_puntos, canje.user_id))
            
            conn.commit()
            
            return {
                "status": "success", 
                "mensaje": "¡FELICITACIONES! 💎 Ahora sos Premium por 1 semana.",
                "nuevo_saldo": user['reputation'] - canje.costo_puntos
            }
    finally:
        conn.close()

@app.post("/crear-preferencia")
def crear_preferencia(solicitud: SolicitudPago):
    try:
        # Configuración de la "Preferencia" (El carrito de compras)
        preference_data = {
            "items": [
                {
                    "title": solicitud.titulo,
                    "quantity": 1,
                    "unit_price": solicitud.precio,
                    "currency_id": "ARS", # Pesos Argentinos
                    "external_reference": solicitud.user_id,
                }
            ],
            # Datos del pagador (opcional pero recomendado)
            "metadata": { 
                "user_id": solicitud.user_id 
            },
            # A dónde volver después de pagar
            "back_urls": {
                "success": "https://www.google.com", # Acá pondremos algo mejor luego (Deep Link)
                "failure": "https://www.google.com",
                "pending": "https://www.google.com"
            },
            "auto_return": "approved"
        }

        # Le pedimos el link a MercadoPago
        preference_response = sdk.preference().create(preference_data)
        preference = preference_response["response"]

        # Devolvemos el link al celular para que lo abra
        return {"init_point": preference["init_point"]} 

    except Exception as e:
        print(f"Error MP: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/reportes/votar")
def votar_reporte(voto: VotoReporte):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Buscamos el reporte
            cur.execute("SELECT id, user_id, score FROM reports WHERE id = %s AND is_active = TRUE", (voto.reporte_id,))
            reporte = cur.fetchone()
            
            if not reporte:
                return {"status": "error", "mensaje": "Este reporte ya no existe ⏳"}

            if str(reporte['user_id']) == str(voto.user_id):
                return {"status": "error", "mensaje": "¡No podés votar tu propio reporte! 🤨"}

            cur.execute("SELECT id FROM report_votes WHERE user_id = %s AND report_id = %s", (voto.user_id, voto.reporte_id))
            if cur.fetchone():
                 return {"status": "error", "mensaje": "Ya votaste este reporte antes ✋"}

            # 2. CALCULAMOS EL PODER DEL VOTO (SEGÚN LA XP DEL USUARIO) 💪
            cur.execute("SELECT lifetime_xp FROM users WHERE id = %s", (voto.user_id,))
            usuario = cur.fetchone()
            xp = usuario['lifetime_xp'] if usuario else 0
            
            poder_voto = 1
            if xp > 50: poder_voto = 3   # Vigilante
            if xp > 500: poder_voto = 5  # Leyenda (Un voto suyo vale por 5 novatos)

            # 3. APLICAMOS EL VOTO
            if voto.tipo_voto == "confirmar":
                # CONFIRMAR: Sube Score, Renueva Tiempo
                cur.execute("INSERT INTO report_votes (user_id, report_id, vote_type) VALUES (%s, %s, 'confirmar')", (voto.user_id, voto.reporte_id))
                
                # Sumamos al score del reporte
                cur.execute("UPDATE reports SET created_at = NOW(), score = score + %s WHERE id = %s", (poder_voto, voto.reporte_id))
                
                # Premiamos al usuario
                cur.execute("UPDATE users SET reputation = reputation + 2, lifetime_xp = lifetime_xp + 1, total_helps = total_helps + 1 WHERE id = %s", (voto.user_id,))
                
                conn.commit()
                return {"status": "success", "mensaje": "¡Confirmado! (+2 Pts) 🛡️"}
            
            elif voto.tipo_voto == "borrar":
                # BORRAR: Resta Score. Solo borra si el score baja mucho.
                cur.execute("INSERT INTO report_votes (user_id, report_id, vote_type) VALUES (%s, %s, 'borrar')", (voto.user_id, voto.reporte_id))
                
                # Restamos el poder del voto al score actual
                nuevo_score = reporte['score'] - poder_voto
                
                # UMBRAL DE BORRADO: Si llega a -5, se elimina.
                if nuevo_score <= -5:
                    cur.execute("UPDATE reports SET is_active = FALSE WHERE id = %s", (voto.reporte_id,))
                    mensaje = "Reporte eliminado por la comunidad. ¡Gracias! 🧹"
                else:
                    # Si no llega a -5, solo bajamos el score
                    cur.execute("UPDATE reports SET score = score - %s WHERE id = %s", (poder_voto, voto.reporte_id))
                    mensaje = "Voto negativo registrado. 📉"

                # Premiamos por colaborar (aunque sea negativo)
                cur.execute("UPDATE users SET reputation = reputation + 1, lifetime_xp = lifetime_xp + 1, total_helps = total_helps + 1 WHERE id = %s", (voto.user_id,))
                
                conn.commit()
                return {"status": "success", "mensaje": mensaje}

    except Exception as e:
        conn.rollback()
        print(f"Error votando: {e}")
        return {"status": "error", "mensaje": f"Error: {str(e)}"}
    finally:
        conn.close()

@app.put("/usuarios/perfil")
def actualizar_perfil(req: PerfilRequest):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Verificamos que el nombre no esté usado por otro (opcional, pero recomendado)
            cur.execute("SELECT id FROM users WHERE username = %s AND id != %s", (req.username, req.user_id))
            if cur.fetchone():
                return {"status": "error", "mensaje": "Ese nombre ya existe 🚫"}

            cur.execute("UPDATE users SET username = %s WHERE id = %s", (req.username, req.user_id))
            conn.commit()
            return {"status": "success", "mensaje": "Nombre actualizado ✅"}
    finally:
        conn.close()

@app.put("/usuarios/avatar")
def subir_avatar(req: AvatarRequest):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET avatar_data = %s WHERE id = %s", (req.avatar_base64, req.user_id))
            conn.commit()
            return {"status": "success", "mensaje": "Foto actualizada 📸"}
    finally:
        conn.close()

# --- ENDPOINT PARA CREAR UN PUNTO (Versión PostgreSQL Correcta) ---
@app.post("/puntos-fijos")
def crear_punto_fijo(punto: PuntoFijo):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Usamos SQL INSERT con geometría PostGIS
            cur.execute("""
                INSERT INTO fixed_points (name, type, location, address, phone, hours)
                VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s)
            """, (punto.nombre, punto.tipo, punto.longitud, punto.latitud, punto.direccion, punto.telefono, punto.horario))
            conn.commit()
            return {"status": "success", "mensaje": "Punto fijo creado"}
    except Exception as e:
        conn.rollback()
        print(f"Error creando punto fijo: {e}")
        # Si la tabla no existe, esto nos avisará
        raise HTTPException(status_code=500, detail=f"Error BD: {str(e)}")
    finally:
        conn.close()

# --- ENDPOINT PARA OBTENER PUNTOS (Versión PostgreSQL Correcta) ---
@app.get("/puntos-fijos")
def obtener_puntos_fijos():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Recuperamos lat/lng de la columna geométrica
            cur.execute("""
                SELECT 
                    id, 
                    name as nombre, 
                    type as tipo, 
                    ST_X(location::geometry) as longitud,
                    ST_Y(location::geometry) as latitud,
                    address as direccion,
                    phone as telefono,
                    hours as horario
                FROM fixed_points
            """)
            puntos = []
            for row in cur.fetchall():
                row['id'] = str(row['id']) # UUID a String
                puntos.append(row)
            return puntos
    except Exception as e:
        print(f"Error trayendo puntos: {e}")
        return []
    finally:
        conn.close()

@app.post("/crear-suscripcion")
def crear_suscripcion(solicitud: SolicitudSuscripcion):
    try:
        if not solicitud.email or "@" not in solicitud.email:
             raise HTTPException(status_code=400, detail="Email inválido")

        print(f"💎 Intento MINIMALISTA para: {solicitud.email}")

        # PAYLOAD "A DIETA" (Sin lujos, solo lo obligatorio)
        suscripcion_data = {
            "reason": "Premium Vial", # Texto corto y simple
            "external_reference": solicitud.user_id,
            "payer_email": solicitud.email,
            "auto_recurring": {
                "frequency": 1,
                "frequency_type": "months",
                "transaction_amount": 2500, # Probemos INT (sin .0) a ver si le gusta más
                "currency_id": "ARS"
            },
            "back_url": "https://www.google.com"
        }

        # Pedimos el link
        resultado = sdk.preapproval().create(suscripcion_data)
        
        status = resultado.get("status")
        response = resultado.get("response", {})

        if status == 201:
            print("✅ ¡EXITO! (Por fin)")
            return {"init_point": response["init_point"]}
        else:
            print("❌ ERROR 400 PERSISTENTE:")
            print(f"Mensaje: {response.get('message')}")
            # Si sigue fallando, imprimimos TODO para ver si hay pistas
            print(f"Dump completo: {response}") 
            raise HTTPException(status_code=400, detail="Fallo MP Minimalista")

    except Exception as e:
        print(f"🔥 Excepción: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cancelar-suscripcion")
def cancelar_suscripcion(req: CancelacionRequest):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Buscamos el ID de suscripción de este usuario
            cur.execute("SELECT subscription_id FROM users WHERE id = %s", (req.user_id,))
            user = cur.fetchone()
            
            if not user or not user['subscription_id']:
                return {"status": "error", "mensaje": "No tenés una suscripción activa."}

            sub_id = user['subscription_id']

            # 2. Avisamos a MercadoPago: "CANCELALO" 🚫
            # (Esto evita que le cobren el mes que viene)
            sdk.preapproval().update(sub_id, {"status": "cancelled"})

            # 3. Actualizamos nuestra DB
            # IMPORTANTE: No tocamos 'premium_expires_at'. 
            # Si pagó hasta el 30, sigue siendo Premium hasta el 30.
            cur.execute("UPDATE users SET subscription_status = 'cancelled' WHERE id = %s", (req.user_id,))
            conn.commit()

            return {"status": "success", "mensaje": "Suscripción cancelada. Disfrutá tus días restantes."}

    except Exception as e:
        print(f"Error cancelando: {e}")
        return {"status": "error", "mensaje": "Error al cancelar en MercadoPago."}
    finally:
        conn.close()


@app.get("/prueba-vida-mp")
def prueba_vida_mp():
    try:
        # Creamos una preferencia SIMPLE (Pago único de $10)
        # Usamos un email falso random para que no choque con tu cuenta
        preference_data = {
            "items": [
                {
                    "title": "Prueba de Vida",
                    "quantity": 1,
                    "unit_price": 10.0,
                    "currency_id": "ARS"
                }
            ],
            "payer": {
                "email": "comprador_falso_123@gmail.com" 
            },
            "back_urls": {
                "success": "https://www.google.com",
                "failure": "https://www.google.com",
                "pending": "https://www.google.com"
            },
            "auto_return": "approved"
        }

        resultado = sdk.preference().create(preference_data)
        respuesta = resultado["response"]

        print("✅ PRUEBA DE VIDA EXITOSA. Link:", respuesta['init_point'])
        return {"mensaje": "¡FUNCIONA!", "link": respuesta['init_point']}

    except Exception as e:
        print(f"❌ FALLÓ LA PRUEBA: {e}")
        return {"error": str(e)}

# --- WEBHOOK DE MERCADOPAGO ---
@app.post("/webhook")
async def recibir_notificacion(request: Request):
    # 1. Leer los datos que manda MercadoPago
    params = request.query_params
    topic = params.get("topic") or params.get("type")
    id_pago = params.get("id") or params.get("data.id")

    print(f"🔔 Notificación recibida: {topic} - ID: {id_pago}")

    # 2. Solo nos importan los pagos, no otras notificaciones
    if topic == "payment" and id_pago:
        try:
            # 3. PREGUNTAR a MP el estado real del pago (Seguridad 🛡️)
            # No confiamos ciegamente en lo que llega, verificamos con el ID
            payment_info = sdk.payment().get(id_pago)
            payment = payment_info["response"]
            
            status = payment.get("status")
            external_reference = payment.get("external_reference") # Acá guardamos el ID del usuario

            print(f"💰 Estado del pago: {status} | Usuario: {external_reference}")

            # 4. Si está APROBADO, damos el Premium
            if status == "approved" and external_reference:
                # Conectar a la base de datos
                conn = get_db_connection()
                cur = conn.cursor()
                
                # Actualizar usuario a Premium + Guardar ID de suscripción
                cur.execute("""
                    UPDATE users 
                    SET is_premium = TRUE, 
                        subscription_status = 'active',
                        subscription_id = %s,
                        premium_expires_at = NOW() + INTERVAL '30 days'
                    WHERE id = %s
                """, (str(id_pago), external_reference))
                
                conn.commit()
                cur.close()
                conn.close()
                print("✅ ¡Usuario actualizado a PREMIUM!")

        except Exception as e:
            print(f"❌ Error procesando pago: {str(e)}")

    return {"status": "ok"}