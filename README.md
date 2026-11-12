# ClientHub — Registro de clientes

Aplicación web independiente para registrar, consultar y organizar contactos. Está desarrollada con Flask, SQLite o PostgreSQL (Neon), HTML, CSS y JavaScript, sin frameworks de interfaz adicionales.

## Funciones

- Acceso de administrador con configuración inicial de credenciales y protección de formularios.
- Registro, edición y eliminación de clientes, con fotografía opcional de perfil.
- Directorio en tabla, búsqueda por nombre o teléfono y fecha de registro automática.
- Panel de inicio y estadísticas calculados desde la base de datos configurada.
- Exportación del directorio a Excel (.xlsx) y PDF.
- Interfaz adaptable a escritorio y móvil.

## Ejecutar en Windows

Desde esta carpeta, en PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

Si todavía no existe el entorno virtual:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Abre **http://127.0.0.1:5001/**. En el primer inicio, ClientHub te pedirá crear el usuario y la contraseña de administrador (mínimo 12 caracteres). La clave de sesión se genera y se guarda en `instance\secret_key`; las fotografías se conservan en `instance\profile_photos`.

Sin `DATABASE_URL`, la aplicación usa `clientes.db` en esta carpeta. Al configurar `DATABASE_URL`, usa PostgreSQL y crea las tablas que falten sin borrar datos existentes. La aplicación no copia automáticamente los registros de SQLite a Neon.

## Desplegar en Render con Neon

1. Crea una base de datos PostgreSQL en Neon y copia la cadena de conexión **pooled** para aplicaciones web. Usa la URL completa (`postgresql://...`) y habilita SSL (`sslmode=require`, normalmente ya viene en la URL).
2. Sube este proyecto a un repositorio Git y, en Render, crea un **Blueprint** a partir del repositorio. `render.yaml` configura el servicio web, el comando de inicio y la comprobación de salud.
3. En los ajustes del servicio de Render, configura `DATABASE_URL` con la URL de Neon. No escribas la URL en el código ni la subas al repositorio.
4. Render genera `CLIENTHUB_SECRET_KEY` para proteger las sesiones. En el primer despliegue, abre la URL pública y crea la cuenta de administrador.

El esquema (clientes, administradores y control de intentos) se crea/migra incrementalmente al iniciar la app. Las fotografías se guardan en el sistema de archivos: los servicios de Render sin disco persistente pueden perderlas al reiniciarse o desplegar una nueva versión. Para conservarlas, conecta un disco persistente de Render y configura `CLIENTHUB_UPLOAD_DIR` a una carpeta dentro del punto de montaje (por ejemplo, `/var/data/profile_photos`).

La app local continúa utilizando SQLite siempre que no exista `DATABASE_URL`. Configura `DATABASE_URL` solo en el entorno de despliegue para evitar cambiar accidentalmente la base de datos local.

## Rutas

- `/configurar` — creación inicial de la cuenta de administrador.
- `/login` — inicio de sesión.
- `/` — panel y formulario para registrar clientes.
- `/clientes` — tabla, búsqueda y exportaciones.
- `/editar/<id>` — edición de cliente y fotografía.
- `/exportar/excel` y `/exportar/pdf` — descarga de registros.
- `/estadisticas` — resumen y actividad mensual.
- `/health` — estado de la aplicación.
