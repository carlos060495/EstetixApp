# EstetixApp

Aplicación para que los salones de belleza organicen su trabajo diario. Cada cuenta puede gestionar sus datos y uno o varios centros.

## Qué hace

- Agenda y citas, fichas de clientas y reservas desde la web.
- Caja, ventas, stock y estadísticas.
- Fichaje y gestión de la plantilla.
- Asistente de WhatsApp para atender reservas.
- Suscripciones por planes y programa de referidos.

## Cómo está montado

- `frontend`: interfaz en Next.js.
- `backend`: API en FastAPI.
- `supabase`: PostgreSQL, autenticación, migraciones y datos de muestra.

Las reglas de acceso también se aplican en la base de datos. Un salón no puede leer ni modificar los datos de otro aunque se salte la interfaz.

## Requisitos

Python 3.14.3, FastAPI 0.139.0, Next.js `^16.3.6` y PostgreSQL 15. También hacen falta Node.js, Docker y la herramienta de línea de comandos de Supabase. El repositorio no fija las versiones de Node.js ni de esa herramienta.

## Arranque

Desde la raíz del repositorio:

**Base de datos**

```sh
supabase start
supabase db reset
supabase status
```

`db reset` aplica las migraciones y los datos de muestra.

**Backend**

```sh
cd backend
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

En `.env`, completa `SUPABASE_URL` y `SUPABASE_SECRET_KEY` con los valores de `supabase status`. `SUPABASE_JWKS_URL` se forma añadiendo `/auth/v1/.well-known/jwks.json` a la URL. Para las pruebas contra la base local, añade también `ESTETIX_STACK_CLAVE` con la misma clave secreta.

```sh
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Frontend**

```sh
cd frontend
npm ci
cp .env.local.example .env.local
```

Completa `NEXT_PUBLIC_API_URL` con la dirección local del backend y `NEXT_PUBLIC_SUPABASE_URL` y `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` con los valores de `supabase status`.

```sh
npm run dev
```

## Pruebas

Backend, sin pruebas que necesitan la base local:

```sh
cd backend
pytest -m "not base_real"
```

Para incluirlas, levanta la base, completa `ESTETIX_STACK_CLAVE` y ejecuta `ESTETIX_SIN_SALTOS=1 pytest`. Si ejecutas `pytest` sin la base levantada, las pruebas `base_real` se saltan y se informa; con `ESTETIX_SIN_SALTOS=1`, la ejecución falla en vez de ocultar que no se hicieron.

Frontend, sin necesitar la base local:

```sh
cd frontend
npm test
```

## Servicios externos

Si no completas sus variables, los pagos, WhatsApp, el correo y las respuestas de inteligencia artificial quedan desactivados. La aplicación puede arrancar igual.
