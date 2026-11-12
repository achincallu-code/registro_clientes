from contextlib import contextmanager
from collections.abc import Mapping
from datetime import date, datetime
from functools import wraps
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from uuid import uuid4

from flask import (
    Flask, abort, flash, redirect, render_template, request, send_file,
    send_from_directory, session, url_for,
)
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from PIL import Image, ImageOps, UnidentifiedImageError
import psycopg
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "clientes.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if os.environ.get("RENDER") and not DATABASE_URL:
    raise RuntimeError("Configura DATABASE_URL con la conexión PostgreSQL de Neon en Render.")
UPLOAD_DIR = Path(os.environ.get(
    "CLIENTHUB_UPLOAD_DIR", str(BASE_DIR / "instance" / "profile_photos")
)).expanduser()
SECRET_KEY_PATH = BASE_DIR / "instance" / "secret_key"
ALLOWED_PHOTOS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024
LOGIN_WINDOW_SECONDS = 15 * 60
MAX_LOGIN_ATTEMPTS = 5


def obtener_clave_secreta():
    clave_entorno = (
        os.environ.get("CLIENTHUB_SECRET_KEY")
        or os.environ.get("SECRET_KEY")
    )
    if clave_entorno:
        return clave_entorno

    SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        return SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        clave = secrets.token_urlsafe(48)
        try:
            with SECRET_KEY_PATH.open("x", encoding="utf-8") as archivo:
                archivo.write(clave)
        except FileExistsError:
            return SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
        return clave


app = Flask(__name__)
app.config.update(
    SECRET_KEY=obtener_clave_secreta(),
    MAX_CONTENT_LENGTH=MAX_PHOTO_BYTES + 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.environ.get("CLIENTHUB_SECURE_COOKIE", "1" if os.environ.get("RENDER") else "0") == "1"
    ),
)

_database_initialized = False


class DatabaseRow(Mapping):
    def __init__(self, columns, values):
        self._columns = columns
        self._values = values
        self._indexes = {name: index for index, name in enumerate(columns)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._indexes[key]]

    def __iter__(self):
        return iter(self._columns)

    def __len__(self):
        return len(self._columns)


def postgres_row_factory(cursor):
    if cursor.description is None:
        return tuple
    columns = tuple(column.name for column in cursor.description)

    def crear_fila(values):
        normalized = tuple(
            value.isoformat(sep=" ") if isinstance(value, datetime) else value
            for value in values
        )
        return DatabaseRow(columns, normalized)

    return crear_fila


class PostgreSQLConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, parameters=()):
        return self.connection.execute(query.replace("?", "%s"), parameters)


def obtener_url_postgres():
    if DATABASE_URL.startswith("postgres://"):
        return "postgresql://" + DATABASE_URL[len("postgres://"):]
    if DATABASE_URL.startswith("postgresql+psycopg://"):
        return "postgresql://" + DATABASE_URL[len("postgresql+psycopg://"):]
    if DATABASE_URL and not DATABASE_URL.startswith("postgresql://"):
        raise ValueError("DATABASE_URL debe ser una URL de PostgreSQL de Neon.")
    return DATABASE_URL


obtener_url_postgres()


@contextmanager
def conectar():
    url_postgres = obtener_url_postgres()
    if url_postgres:
        with psycopg.connect(
            url_postgres,
            connect_timeout=10,
            prepare_threshold=None,
            row_factory=postgres_row_factory,
        ) as connection:
            yield PostgreSQLConnection(connection)
        return

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def crear_tabla():
    global _database_initialized
    with conectar() as conn:
        if obtener_url_postgres():
            conn.execute("SELECT pg_advisory_xact_lock(726150921)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clientes (
                    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    nombre TEXT NOT NULL,
                    correo TEXT NOT NULL UNIQUE,
                    telefono TEXT NOT NULL,
                    direccion TEXT NOT NULL,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS administradores (
                    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    usuario TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS intentos_inicio (
                    direccion_ip TEXT PRIMARY KEY,
                    intentos INTEGER NOT NULL,
                    ultimo_intento DOUBLE PRECISION NOT NULL
                )
            """)
            columnas = {
                fila["name"] for fila in conn.execute("""
                    SELECT column_name AS name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema() AND table_name = 'clientes'
                """)
            }
        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT NOT NULL,
                    correo TEXT NOT NULL UNIQUE,
                    telefono TEXT NOT NULL,
                    direccion TEXT NOT NULL,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS administradores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS intentos_inicio (
                    direccion_ip TEXT PRIMARY KEY,
                    intentos INTEGER NOT NULL,
                    ultimo_intento REAL NOT NULL
                )
            """)
            columnas = {
                fila["name"] for fila in conn.execute("PRAGMA table_info(clientes)")
            }
        if "foto" not in columnas:
            conn.execute("ALTER TABLE clientes ADD COLUMN foto TEXT")
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_administradores_usuario_lower
            ON administradores (lower(usuario))
        """)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _database_initialized = True


def csrf_actual():
    return session.setdefault("_csrf_token", secrets.token_urlsafe(32))


@app.context_processor
def agregar_contexto_global():
    return {
        "csrf_token": csrf_actual,
        "admin_usuario": session.get("admin_usuario"),
    }


@app.before_request
def proteger_sesion_y_rutas():
    global _database_initialized
    if not _database_initialized:
        crear_tabla()

    if request.method == "POST":
        recibido = request.form.get("csrf_token", "")
        esperado = session.get("_csrf_token", "")
        if not recibido or not esperado or not secrets.compare_digest(recibido, esperado):
            abort(400, description="La sesión del formulario venció. Recarga la página e inténtalo de nuevo.")

    endpoint = request.endpoint
    if endpoint in {None, "static", "health", "login", "configurar"}:
        return None

    with conectar() as conn:
        existe_admin = conn.execute(
            "SELECT 1 FROM administradores LIMIT 1"
        ).fetchone() is not None

    if not existe_admin:
        return redirect(url_for("configurar"))
    if "admin_id" not in session:
        destino = request.full_path if request.query_string else request.path
        return redirect(url_for("login", next=destino))
    return None


def inicio_requerido(funcion):
    @wraps(funcion)
    def protegido(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("login", next=request.path))
        return funcion(*args, **kwargs)
    return protegido


def redireccion_local(destino):
    partes = urlsplit(destino or "")
    if not partes.scheme and not partes.netloc and partes.path.startswith("/") and not partes.path.startswith("//"):
        return destino
    return url_for("inicio")


def validar_datos(formulario):
    datos = {
        "nombre": formulario.get("nombre", "").strip(),
        "correo": formulario.get("correo", "").strip().lower(),
        "telefono": formulario.get("telefono", "").strip(),
        "direccion": formulario.get("direccion", "").strip(),
    }
    if not all(datos.values()):
        return datos, "Completa todos los campos."
    if len(datos["nombre"]) < 3:
        return datos, "El nombre debe tener al menos 3 caracteres."
    if len(datos["nombre"]) > 120 or len(datos["direccion"]) > 250:
        return datos, "El nombre o la dirección superan la longitud permitida."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", datos["correo"]):
        return datos, "Escribe un correo electrónico válido."
    if len(datos["correo"]) > 254:
        return datos, "El correo electrónico es demasiado largo."
    if not datos["telefono"].isdigit() or len(datos["telefono"]) != 10:
        return datos, "El teléfono debe contener exactamente 10 números."
    return datos, None


def guardar_foto(subida):
    if not subida or not subida.filename:
        return None
    nombre_seguro = secure_filename(subida.filename)
    extension = Path(nombre_seguro).suffix.lower()
    if extension not in ALLOWED_PHOTOS:
        raise ValueError("Usa una fotografía JPG, PNG o WebP.")
    contenido = subida.read(MAX_PHOTO_BYTES + 1)
    if not contenido or len(contenido) > MAX_PHOTO_BYTES:
        raise ValueError("La fotografía no puede superar los 5 MB.")
    formatos = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
    try:
        with Image.open(BytesIO(contenido)) as imagen:
            if imagen.format != formatos[extension]:
                raise ValueError("La extensión del archivo no coincide con su formato.")
            if imagen.width * imagen.height > 20_000_000:
                raise ValueError("La imagen es demasiado grande.")
            imagen = ImageOps.exif_transpose(imagen)
            imagen.thumbnail((600, 600), Image.Resampling.LANCZOS)
            if imagen.mode != "RGB":
                if imagen.mode in {"RGBA", "LA"}:
                    transparencia = imagen.convert("RGBA")
                    fondo = Image.new("RGB", transparencia.size, "white")
                    fondo.paste(transparencia, mask=transparencia.getchannel("A"))
                    imagen = fondo
                else:
                    imagen = imagen.convert("RGB")
            procesada = BytesIO()
            imagen.save(procesada, format="JPEG", quality=86, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("El archivo no contiene una fotografía válida.") from error
    except Image.DecompressionBombWarning as error:
        raise ValueError("La imagen es demasiado grande.") from error
    if not procesada.getvalue():
        raise ValueError("El archivo no parece ser una imagen JPG, PNG o WebP válida.")
    nombre_final = f"{uuid4().hex}.jpg"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / nombre_final).write_bytes(procesada.getvalue())
    return nombre_final


def eliminar_foto(nombre):
    if not nombre or Path(nombre).name != nombre:
        return
    ruta = UPLOAD_DIR / nombre
    if ruta.is_file():
        ruta.unlink()


def obtener_resumen():
    with conectar() as conn:
        total = conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
        inicio_mes = (
            "date_trunc('month', CURRENT_DATE)"
            if obtener_url_postgres()
            else "date('now', 'start of month')"
        )
        registrados_mes = conn.execute(
            "SELECT COUNT(*) FROM clientes WHERE fecha_registro >= " + inicio_mes
        ).fetchone()[0]
        recientes = conn.execute(
            "SELECT * FROM clientes ORDER BY id DESC LIMIT 5"
        ).fetchall()
    return total, registrados_mes, recientes


def obtener_clientes():
    with conectar() as conn:
        return conn.execute(
            "SELECT * FROM clientes ORDER BY lower(nombre)"
        ).fetchall()


def verificar_limite_inicio():
    ip = request.remote_addr or "desconocida"
    ahora = time.time()
    with conectar() as conn:
        fila = conn.execute(
            "SELECT intentos, ultimo_intento FROM intentos_inicio WHERE direccion_ip = ?",
            (ip,),
        ).fetchone()
        if fila and ahora - fila["ultimo_intento"] < LOGIN_WINDOW_SECONDS:
            if fila["intentos"] >= MAX_LOGIN_ATTEMPTS:
                return False
            return True
        if fila:
            conn.execute(
                "DELETE FROM intentos_inicio WHERE direccion_ip = ?", (ip,)
            )
    return True


def registrar_intento_fallido():
    ip = request.remote_addr or "desconocida"
    ahora = time.time()
    with conectar() as conn:
        fila = conn.execute(
            "SELECT intentos, ultimo_intento FROM intentos_inicio WHERE direccion_ip = ?",
            (ip,),
        ).fetchone()
        if fila and ahora - fila["ultimo_intento"] < LOGIN_WINDOW_SECONDS:
            conn.execute(
                "UPDATE intentos_inicio SET intentos = ?, ultimo_intento = ? WHERE direccion_ip = ?",
                (fila["intentos"] + 1, ahora, ip),
            )
        else:
            conn.execute("""
                INSERT INTO intentos_inicio (direccion_ip, intentos, ultimo_intento)
                VALUES (?, 1, ?)
                ON CONFLICT(direccion_ip) DO UPDATE SET intentos = 1, ultimo_intento = excluded.ultimo_intento
            """, (ip, ahora))


@app.route("/configurar", methods=["GET", "POST"])
def configurar():
    with conectar() as conn:
        existe_admin = conn.execute(
            "SELECT 1 FROM administradores LIMIT 1"
        ).fetchone() is not None
    if existe_admin:
        return redirect(url_for("inicio") if "admin_id" in session else url_for("login"))

    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        contrasena = request.form.get("contrasena", "")
        confirmar = request.form.get("confirmar", "")
        if not re.fullmatch(r"[\w.@+-]{3,40}", usuario, flags=re.UNICODE):
            flash("El usuario debe tener entre 3 y 40 caracteres válidos.", "error")
        elif len(contrasena) < 12:
            flash("La contraseña debe tener al menos 12 caracteres.", "error")
        elif contrasena != confirmar:
            flash("Las contraseñas no coinciden.", "error")
        else:
            try:
                with conectar() as conn:
                    existe_admin = conn.execute(
                        "SELECT 1 FROM administradores LIMIT 1"
                    ).fetchone()
                    if existe_admin:
                        flash("La cuenta de administrador ya está configurada.", "error")
                        return redirect(url_for("login"))
                    conn.execute(
                        "INSERT INTO administradores (usuario, password_hash) VALUES (?, ?)",
                        (usuario, generate_password_hash(contrasena)),
                    )
                flash("Administrador creado. Ya puedes iniciar sesión.", "success")
                return redirect(url_for("login"))
            except (sqlite3.IntegrityError, psycopg.IntegrityError):
                flash("No se pudo completar la configuración. Recarga la página e inténtalo de nuevo.", "error")
    return render_template("configurar.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    with conectar() as conn:
        existe_admin = conn.execute(
            "SELECT 1 FROM administradores LIMIT 1"
        ).fetchone() is not None
    if not existe_admin:
        return redirect(url_for("configurar"))
    if "admin_id" in session:
        return redirect(url_for("inicio"))

    if request.method == "POST":
        if not verificar_limite_inicio():
            flash("Demasiados intentos. Espera 15 minutos antes de volver a intentarlo.", "error")
            return render_template("login.html"), 429
        usuario = request.form.get("usuario", "").strip()
        contrasena = request.form.get("contrasena", "")
        with conectar() as conn:
            admin = conn.execute(
                "SELECT id, usuario, password_hash FROM administradores WHERE lower(usuario) = lower(?)",
                (usuario,),
            ).fetchone()
        if admin and check_password_hash(admin["password_hash"], contrasena):
            with conectar() as conn:
                conn.execute(
                    "DELETE FROM intentos_inicio WHERE direccion_ip = ?",
                    (request.remote_addr or "desconocida",),
                )
            session.clear()
            session["admin_id"] = admin["id"]
            session["admin_usuario"] = admin["usuario"]
            destino = redireccion_local(request.form.get("next"))
            return redirect(destino)
        registrar_intento_fallido()
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html", next=redireccion_local(request.args.get("next")))


@app.post("/logout")
@inicio_requerido
def logout():
    session.clear()
    flash("Sesión cerrada correctamente.", "success")
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@inicio_requerido
def inicio():
    if request.method == "POST":
        datos, error = validar_datos(request.form)
        if error:
            flash(error, "error")
            return render_template("index.html", datos=datos, resumen=obtener_resumen())
        try:
            foto = guardar_foto(request.files.get("foto"))
        except ValueError as error_foto:
            flash(str(error_foto), "error")
            return render_template("index.html", datos=datos, resumen=obtener_resumen())
        try:
            with conectar() as conn:
                conn.execute("""
                    INSERT INTO clientes (nombre, correo, telefono, direccion, foto)
                    VALUES (?, ?, ?, ?, ?)
                """, (*datos.values(), foto))
        except (sqlite3.IntegrityError, psycopg.IntegrityError):
            eliminar_foto(foto)
            flash("Ese correo ya está registrado.", "error")
            return render_template("index.html", datos=datos, resumen=obtener_resumen())
        flash("Cliente registrado correctamente.", "success")
        return redirect(url_for("clientes"))
    return render_template("index.html", datos={}, resumen=obtener_resumen())


@app.get("/clientes")
@inicio_requerido
def clientes():
    busqueda = request.args.get("q", "").strip()
    parametro = f"%{busqueda}%"
    with conectar() as conn:
        registros = conn.execute("""
            SELECT * FROM clientes
            WHERE lower(nombre) LIKE lower(?) OR lower(telefono) LIKE lower(?)
                OR lower(correo) LIKE lower(?) OR lower(direccion) LIKE lower(?)
            ORDER BY lower(nombre)
        """, (parametro, parametro, parametro, parametro)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
    return render_template(
        "clientes.html", registros=registros, total=total, busqueda=busqueda
    )


@app.route("/editar/<int:cliente_id>", methods=["GET", "POST"])
@inicio_requerido
def editar(cliente_id):
    with conectar() as conn:
        cliente = conn.execute(
            "SELECT * FROM clientes WHERE id = ?", (cliente_id,)
        ).fetchone()
    if not cliente:
        abort(404)

    if request.method == "POST":
        datos, error = validar_datos(request.form)
        eliminar_foto_actual = request.form.get("eliminar_foto") == "1"
        try:
            nueva_foto = guardar_foto(request.files.get("foto"))
        except ValueError as error_foto:
            flash(str(error_foto), "error")
            return render_template("editar.html", cliente={**dict(cliente), **datos})
        if error:
            eliminar_foto(nueva_foto)
            flash(error, "error")
            return render_template("editar.html", cliente={**dict(cliente), **datos})

        foto_guardada = nueva_foto or (None if eliminar_foto_actual else cliente["foto"])
        try:
            with conectar() as conn:
                conn.execute("""
                    UPDATE clientes
                    SET nombre = ?, correo = ?, telefono = ?, direccion = ?, foto = ?
                    WHERE id = ?
                """, (*datos.values(), foto_guardada, cliente_id))
        except (sqlite3.IntegrityError, psycopg.IntegrityError):
            eliminar_foto(nueva_foto)
            flash("Ese correo ya está registrado por otro cliente.", "error")
            return render_template("editar.html", cliente={**dict(cliente), **datos})

        if cliente["foto"] and cliente["foto"] != foto_guardada:
            eliminar_foto(cliente["foto"])
        flash("Los datos del cliente se actualizaron.", "success")
        return redirect(url_for("clientes"))
    return render_template("editar.html", cliente=cliente)


@app.post("/eliminar/<int:cliente_id>")
@inicio_requerido
def eliminar(cliente_id):
    with conectar() as conn:
        cliente = conn.execute(
            "SELECT foto FROM clientes WHERE id = ?", (cliente_id,)
        ).fetchone()
        conn.execute("DELETE FROM clientes WHERE id = ?", (cliente_id,))
    if cliente:
        eliminar_foto(cliente["foto"])
    flash("Registro eliminado.", "success")
    return redirect(url_for("clientes"))


@app.get("/fotos/<path:nombre>")
@inicio_requerido
def foto_cliente(nombre):
    if Path(nombre).name != nombre:
        abort(404)
    return send_from_directory(UPLOAD_DIR, nombre, max_age=3600)


@app.get("/exportar/excel")
@inicio_requerido
def exportar_excel():
    registros = obtener_clientes()
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Clientes"
    hoja.append(["ID", "Nombre", "Correo electrónico", "Teléfono", "Dirección", "Fecha de registro"])
    for celda in hoja[1]:
        celda.font = Font(name="Aptos", bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="DF634F")
        celda.alignment = Alignment(vertical="center")
    for cliente in registros:
        hoja.append([
            cliente["id"], cliente["nombre"], cliente["correo"],
            cliente["telefono"], cliente["direccion"], cliente["fecha_registro"],
        ])
        for celda in hoja[hoja.max_row]:
            if isinstance(celda.value, str):
                celda.data_type = "s"
    hoja.freeze_panes = "A2"
    hoja.auto_filter.ref = hoja.dimensions
    for columna, ancho in {"A": 9, "B": 28, "C": 34, "D": 16, "E": 38, "F": 23}.items():
        hoja.column_dimensions[columna].width = ancho
    salida = BytesIO()
    libro.save(salida)
    salida.seek(0)
    return send_file(
        salida, as_attachment=True, download_name="clientes_clienthub.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/exportar/pdf")
@inicio_requerido
def exportar_pdf():
    registros = obtener_clientes()
    salida = BytesIO()
    documento = SimpleDocTemplate(
        salida, pagesize=landscape(letter), rightMargin=13 * mm,
        leftMargin=13 * mm, topMargin=13 * mm, bottomMargin=13 * mm,
        title="Directorio de clientes ClientHub",
    )
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle(
        "TituloClientHub", parent=estilos["Title"], fontName="Helvetica-Bold",
        fontSize=19, textColor=colors.HexColor("#37334B"), alignment=TA_LEFT,
        spaceAfter=5 * mm,
    )
    contenido = [
        Paragraph("Directorio de clientes", titulo),
        Paragraph(f"ClientHub  ·  {len(registros)} clientes  ·  Generado {date.today().isoformat()}", estilos["Normal"]),
        Spacer(1, 6 * mm),
    ]
    filas = [["ID", "Nombre", "Correo electrónico", "Teléfono", "Dirección", "Fecha de registro"]]
    for cliente in registros:
        filas.append([
            str(cliente["id"]), cliente["nombre"], cliente["correo"],
            cliente["telefono"], cliente["direccion"],
            (cliente["fecha_registro"] or "")[:10],
        ])
    tabla = Table(
        filas, repeatRows=1,
        colWidths=[12 * mm, 43 * mm, 58 * mm, 29 * mm, 70 * mm, 31 * mm],
    )
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DF634F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 11),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FFF6ED")]),
        ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#E9E0D5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    contenido.append(tabla)
    documento.build(contenido)
    salida.seek(0)
    return send_file(
        salida, as_attachment=True, download_name="clientes_clienthub.pdf",
        mimetype="application/pdf",
    )


@app.get("/estadisticas")
@inicio_requerido
def estadisticas():
    with conectar() as conn:
        total = conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
        recientes = conn.execute(
            "SELECT * FROM clientes ORDER BY id DESC LIMIT 5"
        ).fetchall()
        if obtener_url_postgres():
            por_mes = conn.execute("""
                SELECT to_char(fecha_registro, 'YYYY-MM') AS mes, COUNT(*) AS cantidad
                FROM clientes
                WHERE fecha_registro >= date_trunc('month', CURRENT_DATE) - INTERVAL '5 months'
                GROUP BY mes
            """).fetchall()
        else:
            por_mes = conn.execute("""
                SELECT substr(fecha_registro, 1, 7) AS mes, COUNT(*) AS cantidad
                FROM clientes
                WHERE fecha_registro >= date('now', 'start of month', '-5 months')
                GROUP BY mes
            """).fetchall()

    cantidades = {fila["mes"]: fila["cantidad"] for fila in por_mes}
    nombres_meses = (
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    )
    hoy = date.today()
    mes_actual = hoy.year * 12 + hoy.month - 1
    actividad_mensual = []
    for desplazamiento in range(5, -1, -1):
        anio, mes_indice = divmod(mes_actual - desplazamiento, 12)
        mes = mes_indice + 1
        clave = f"{anio:04d}-{mes:02d}"
        actividad_mensual.append({
            "nombre": nombres_meses[mes_indice][:3].capitalize(),
            "cantidad": cantidades.get(clave, 0),
        })
    maximo_mes = max((mes["cantidad"] for mes in actividad_mensual), default=0)
    for mes in actividad_mensual:
        mes["altura"] = round(mes["cantidad"] / maximo_mes * 100) if maximo_mes else 0

    return render_template(
        "estadisticas.html", total=total, recientes=recientes,
        actividad_mensual=actividad_mensual,
    )


@app.get("/health")
def health():
    return {"status": "ok", "app": "Registro de clientes"}


if __name__ == "__main__":
    crear_tabla()
    app.run(
        host="0.0.0.0",
        debug=os.environ.get("CLIENTHUB_DEBUG") == "1",
        port=int(os.environ.get("PORT", "5001")),
    )
